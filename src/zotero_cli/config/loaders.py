"""Load config.toml sections, validate them, and write merged config files.

Saving is merge-preserving: sections the caller does not touch (profiles,
ai_notes, pdf, embedding, rerank, semantic_search, ...) are round-tripped
verbatim. Writes are atomic (temp file + fsync + os.replace), keep a `.bak`
copy of the previous file, and use 0600 file / 0700 directory permissions
because the file may hold API keys.
"""

from __future__ import annotations

import copy
import datetime
import json
import math
import os
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError
from .models import (
    AiNoteConfig,
    AppConfig,
    EmbeddingConfig,
    PdfConfig,
    RerankConfig,
    SemanticSearchConfig,
    VectorStoreConfig,
)

_BARE_TOML_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_BACKUP_SUFFIX = ".bak"


# --- reading -----------------------------------------------------------------


def read_config_data(path: Path | None = None) -> dict[str, Any]:
    """Load raw TOML data; missing files yield {}, malformed files raise ConfigError."""
    resolved = path or paths.config_file_path()
    if not resolved.exists():
        return {}
    try:
        with open(resolved, "rb") as handle:
            loaded = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed TOML in {resolved}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Malformed TOML in {resolved}: file is not valid UTF-8 ({exc})") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {resolved}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"Invalid config file {resolved}: expected a TOML table")
    return loaded


def resolve_config_path(explicit: str | Path | None = None, *, must_exist: bool = False) -> Path:
    """Resolve an explicit --config-path (typed error when missing) or the repo default."""
    if explicit is None or str(explicit) == "":
        return paths.config_file_path()
    resolved = Path(explicit).expanduser()
    if must_exist and not resolved.exists():
        raise ConfigError(f"Config file not found: {resolved}")
    return resolved


# --- value validation --------------------------------------------------------


def _require_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"Invalid config: [{key}] must be a table")
    return value


def _parse_string(value: Any, label: str, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"Invalid config: {label} must be a string")
    return value


def _parse_int(value: Any, label: str, default: int, *, minimum: int | None = None) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"Invalid config: {label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config: {label} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ConfigError(f"Invalid config: {label} must be an integer")
    if minimum is not None and number < minimum:
        raise ConfigError(f"Invalid config: {label} must be >= {minimum}")
    return number


def _parse_float(value: Any, label: str, default: float, *, minimum: float | None = None) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"Invalid config: {label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config: {label} must be a number") from exc
    if not math.isfinite(number):
        raise ConfigError(f"Invalid config: {label} must be a finite number")
    if minimum is not None and number < minimum:
        raise ConfigError(f"Invalid config: {label} must be >= {minimum}")
    return number


def _env_value(name: str) -> Any:
    value = os.environ.get(name)
    return value if value is not None else None


def _section_for_profile(data: dict[str, Any], section: str, profile: str | None) -> dict[str, Any]:
    """Return a config section, overlaying [profile.<name>.<section>] when requested.

    Without an explicit profile only the top-level section is used (historical
    behavior). With a profile, keys from the profile-scoped section win and
    missing keys fall back to the top-level section.
    """
    top_value = data.get(section, {})
    if not isinstance(top_value, dict):
        raise ConfigError(f"Invalid config: [{section}] must be a table")
    top: dict[str, Any] = top_value
    if profile is None:
        return top
    profiles_value = data.get("profile", {})
    if not isinstance(profiles_value, dict):
        raise ConfigError("Invalid config: [profile] must be a table")
    profile_data = profiles_value.get(profile)
    if not isinstance(profile_data, dict):
        raise ConfigError(f"Unknown config profile '{profile}'")
    nested_value = profile_data.get(section, {})
    if not isinstance(nested_value, dict):
        raise ConfigError(f"Invalid config: [profile.{profile}.{section}] must be a table")
    if not nested_value:
        return top
    return {**top, **nested_value}


# --- section loaders ---------------------------------------------------------


def _normalize_profile(value: str | None) -> str | None:
    """Strip a profile name; blank names mean "unset"."""
    if value is None:
        return None
    return value.strip() or None


def resolve_active_profile(
    profiles: dict[str, Any], default_profile: str | None, *, explicit: str | None = None
) -> str | None:
    """Resolve the active profile name, failing closed on invalid configuration.

    A config that defines [profile.*] tables must name one of them in
    [default].profile (or receive an explicit --profile); it never silently falls
    back to the legacy flat [zotero] section, which could point at a different
    library or credentials.
    """
    explicit_name = _normalize_profile(explicit)
    default_name = _normalize_profile(default_profile)
    if explicit_name is not None:
        if explicit_name not in profiles:
            raise ConfigError(f"Unknown config profile '{explicit_name}'")
        return explicit_name
    if profiles:
        if default_name is None:
            raise ConfigError(
                "Invalid config: profiles are defined but no default profile is set; "
                "set [default].profile or pass --profile"
            )
        if default_name not in profiles:
            raise ConfigError(f"Unknown config profile '{default_name}' (set in [default].profile)")
        return default_name
    if default_name is not None:
        raise ConfigError(f"Unknown config profile '{default_name}' (no [profile.*] tables are defined)")
    return None


def load_config(path: Path | None = None, profile: str | None = None) -> AppConfig:
    data = read_config_data(path)
    if not data:
        if _normalize_profile(profile) is not None:
            raise ConfigError(f"Unknown config profile '{profile}'")
        return AppConfig()

    profiles_value = data.get("profile")
    if profiles_value is not None and not isinstance(profiles_value, dict):
        raise ConfigError("Invalid config: [profile] must be a table")
    profiles: dict[str, Any] = profiles_value if isinstance(profiles_value, dict) else {}
    default_value = data.get("default")
    if default_value is not None and not isinstance(default_value, dict):
        raise ConfigError("Invalid config: [default] must be a table")
    default_table: dict[str, Any] = default_value if isinstance(default_value, dict) else {}
    default_profile = _parse_string(default_table.get("profile"), "[default] profile", "")

    profile_name = resolve_active_profile(profiles, default_profile, explicit=profile)
    if profile_name is not None:
        profile_data = profiles[profile_name]
        if not isinstance(profile_data, dict):
            raise ConfigError(f"Invalid config: [profile.{profile_name}] must be a table")
        output = _require_table(profile_data, "output")
        export = _require_table(profile_data, "export")
        integrations = _require_table(profile_data, "integrations")
        crossref = profile_data.get("crossref_mailto", integrations.get("crossref_mailto", ""))
        return AppConfig(
            data_dir=_parse_string(profile_data.get("data_dir"), f"[profile.{profile_name}] data_dir", ""),
            library_id=_parse_string(profile_data.get("library_id"), f"[profile.{profile_name}] library_id", ""),
            api_key=_parse_string(profile_data.get("api_key"), f"[profile.{profile_name}] api_key", ""),
            semantic_scholar_api_key=_parse_string(
                profile_data.get("semantic_scholar_api_key"),
                f"[profile.{profile_name}] semantic_scholar_api_key",
                "",
            ),
            crossref_mailto=_parse_string(crossref, f"[profile.{profile_name}] crossref_mailto", ""),
            default_format=_parse_string(output.get("default_format"), "output.default_format", "table"),
            default_limit=_parse_int(output.get("limit"), "output.limit", 50, minimum=1),
            default_export_style=_parse_string(export.get("default_style"), "export.default_style", "bibtex"),
            prefs_js_path=_parse_string(
                profile_data.get("prefs_js_path"), f"[profile.{profile_name}] prefs_js_path", ""
            ),
        )

    zotero = _require_table(data, "zotero")
    output = _require_table(data, "output")
    export = _require_table(data, "export")
    integrations = _require_table(data, "integrations")
    return AppConfig(
        data_dir=_parse_string(zotero.get("data_dir"), "zotero.data_dir", ""),
        library_id=_parse_string(zotero.get("library_id"), "zotero.library_id", ""),
        api_key=_parse_string(zotero.get("api_key"), "zotero.api_key", ""),
        semantic_scholar_api_key=_parse_string(
            zotero.get("semantic_scholar_api_key"), "zotero.semantic_scholar_api_key", ""
        ),
        crossref_mailto=_parse_string(integrations.get("crossref_mailto"), "integrations.crossref_mailto", ""),
        default_format=_parse_string(output.get("default_format"), "output.default_format", "table"),
        default_limit=_parse_int(output.get("limit"), "output.limit", 50, minimum=1),
        default_export_style=_parse_string(export.get("default_style"), "export.default_style", "bibtex"),
        prefs_js_path=_parse_string(zotero.get("prefs_js_path"), "zotero.prefs_js_path", ""),
    )


def load_embedding_config(
    path: Path | None = None, *, apply_env_overrides: bool = False, profile: str | None = None
) -> EmbeddingConfig:
    defaults = EmbeddingConfig()
    data = read_config_data(path)
    section_value = _section_for_profile(data, "embedding", profile)
    emb = _select_provider_config(
        section_value,
        env_active="ZOT_EMBEDDING_ACTIVE" if apply_env_overrides else "",
    )
    config = EmbeddingConfig(
        active=_parse_string(emb.get("active"), "embedding.active", defaults.active),
        provider=_parse_string(emb.get("provider"), "embedding.provider", defaults.provider),
        url=_parse_string(emb.get("url"), "embedding.url", defaults.url),
        api_key=_parse_string(emb.get("api_key"), "embedding.api_key", defaults.api_key),
        model=_parse_string(emb.get("model"), "embedding.model", defaults.model),
        batch_size=_parse_int(emb.get("batch_size"), "embedding.batch_size", defaults.batch_size, minimum=1),
    )
    if apply_env_overrides:
        config.active = _parse_string(_env_value("ZOT_EMBEDDING_ACTIVE"), "ZOT_EMBEDDING_ACTIVE", config.active)
        config.provider = _parse_string(_env_value("ZOT_EMBEDDING_PROVIDER"), "ZOT_EMBEDDING_PROVIDER", config.provider)
        config.url = _parse_string(_env_value("ZOT_EMBEDDING_URL"), "ZOT_EMBEDDING_URL", config.url)
        config.api_key = _parse_string(_env_value("ZOT_EMBEDDING_KEY"), "ZOT_EMBEDDING_KEY", config.api_key)
        config.model = _parse_string(_env_value("ZOT_EMBEDDING_MODEL"), "ZOT_EMBEDDING_MODEL", config.model)
        config.batch_size = _parse_int(
            _env_value("ZOT_EMBEDDING_BATCH_SIZE"),
            "ZOT_EMBEDDING_BATCH_SIZE",
            config.batch_size,
            minimum=1,
        )
    return config


def load_rerank_config(
    path: Path | None = None, *, apply_env_overrides: bool = False, profile: str | None = None
) -> RerankConfig:
    defaults = RerankConfig()
    data = read_config_data(path)
    section_value = _section_for_profile(data, "rerank", profile)
    rerank = _select_provider_config(
        section_value,
        env_active="ZOT_RERANK_ACTIVE" if apply_env_overrides else "",
    )
    config = RerankConfig(
        active=_parse_string(rerank.get("active"), "rerank.active", defaults.active),
        provider=_parse_string(rerank.get("provider"), "rerank.provider", defaults.provider),
        url=_parse_string(rerank.get("url"), "rerank.url", defaults.url),
        api_key=_parse_string(rerank.get("api_key"), "rerank.api_key", defaults.api_key),
        model=_parse_string(rerank.get("model"), "rerank.model", defaults.model),
        batch_size=_parse_int(rerank.get("batch_size"), "rerank.batch_size", defaults.batch_size, minimum=1),
    )
    if apply_env_overrides:
        config.active = _parse_string(_env_value("ZOT_RERANK_ACTIVE"), "ZOT_RERANK_ACTIVE", config.active)
        config.provider = _parse_string(_env_value("ZOT_RERANK_PROVIDER"), "ZOT_RERANK_PROVIDER", config.provider)
        config.url = _parse_string(_env_value("ZOT_RERANK_URL"), "ZOT_RERANK_URL", config.url)
        config.api_key = _parse_string(_env_value("ZOT_RERANK_KEY"), "ZOT_RERANK_KEY", config.api_key)
        config.model = _parse_string(_env_value("ZOT_RERANK_MODEL"), "ZOT_RERANK_MODEL", config.model)
        config.batch_size = _parse_int(
            _env_value("ZOT_RERANK_BATCH_SIZE"),
            "ZOT_RERANK_BATCH_SIZE",
            config.batch_size,
            minimum=1,
        )
    return config


def _select_provider_config(section: Any, *, env_active: str = "") -> dict[str, Any]:
    if not isinstance(section, dict):
        return {}

    active = os.environ.get(env_active, "") if env_active else ""
    active = active or str(section.get("active", "")).strip()
    if not active:
        return {key: value for key, value in section.items() if not isinstance(value, dict)}

    selected: dict[str, Any] = {}
    current: Any = section
    for part in active.split("."):
        if not isinstance(current, dict):
            return selected
        selected.update(
            {key: value for key, value in current.items() if key != "active" and not isinstance(value, dict)}
        )
        current = current.get(part, {})

    if isinstance(current, dict):
        selected.update({key: value for key, value in current.items() if not isinstance(value, dict)})
    selected["active"] = active
    return selected


def load_pdf_config(path: Path | None = None, *, apply_env_overrides: bool = True) -> PdfConfig:
    defaults = PdfConfig()
    data = read_config_data(path)
    pdf = _require_table(data, "pdf")
    config = PdfConfig(
        mineru_token=_parse_string(pdf.get("mineru_token"), "pdf.mineru_token", defaults.mineru_token),
        mineru_model_version=_parse_string(
            pdf.get("mineru_model_version"), "pdf.mineru_model_version", defaults.mineru_model_version
        ),
    )
    if apply_env_overrides:
        config.mineru_token = _parse_string(_env_value("ZOT_MINERU_TOKEN"), "ZOT_MINERU_TOKEN", config.mineru_token)
        config.mineru_model_version = _parse_string(
            _env_value("ZOT_MINERU_MODEL_VERSION"),
            "ZOT_MINERU_MODEL_VERSION",
            config.mineru_model_version,
        )
    return config


def load_vector_store_config(path: Path | None = None, *, profile: str | None = None) -> VectorStoreConfig:
    defaults = VectorStoreConfig()
    data = read_config_data(path)
    vector_store = _section_for_profile(data, "vector_store", profile)
    return VectorStoreConfig(
        provider=_parse_string(vector_store.get("provider"), "vector_store.provider", defaults.provider),
        path=_parse_string(vector_store.get("path"), "vector_store.path", defaults.path),
    )


def load_semantic_search_config(path: Path | None = None, *, profile: str | None = None) -> SemanticSearchConfig:
    defaults = SemanticSearchConfig()
    data = read_config_data(path)
    semantic_search = _section_for_profile(data, "semantic_search", profile)
    return SemanticSearchConfig(
        candidate_k=_parse_int(
            semantic_search.get("candidate_k"), "semantic_search.candidate_k", defaults.candidate_k, minimum=1
        ),
        top_k=_parse_int(semantic_search.get("top_k"), "semantic_search.top_k", defaults.top_k, minimum=1),
        rrf_k=_parse_int(semantic_search.get("rrf_k"), "semantic_search.rrf_k", defaults.rrf_k, minimum=1),
        semantic_weight=_parse_float(
            semantic_search.get("semantic_weight"), "semantic_search.semantic_weight", defaults.semantic_weight
        ),
        bm25_weight=_parse_float(
            semantic_search.get("bm25_weight"), "semantic_search.bm25_weight", defaults.bm25_weight
        ),
    )


def _split_models(value: Any) -> list[str] | None:
    """Accept a TOML array, or a comma-separated string from a shell variable."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return None
    return [m.strip() for m in map(str, value) if m.strip()] or None


def load_ai_note_config(path: Path | None = None, *, apply_env_overrides: bool = True) -> AiNoteConfig:
    """Load `[ai_notes]`, then layer `ZOT_AI_*` overrides on top.

    Precedence is env > config file > defaults, matching the other loaders, so
    a run can point `ai_analyze` at another endpoint or model without editing
    the tracked `.zot/config.toml`.
    """
    config = AiNoteConfig()
    data = read_config_data(path)
    ai_notes = _require_table(data, "ai_notes")
    config = AiNoteConfig(
        api_key=_parse_string(ai_notes.get("api_key"), "ai_notes.api_key", config.api_key),
        base_url=_parse_string(ai_notes.get("base_url"), "ai_notes.base_url", config.base_url),
        model=_parse_string(ai_notes.get("model"), "ai_notes.model", config.model),
        fallback_models=_split_models(ai_notes.get("fallback_models")) or config.fallback_models,
        reasoning_effort=_parse_string(
            ai_notes.get("reasoning_effort"), "ai_notes.reasoning_effort", config.reasoning_effort
        ),
        temperature=_parse_float(ai_notes.get("temperature"), "ai_notes.temperature", config.temperature),
        max_tokens=_parse_int(ai_notes.get("max_tokens"), "ai_notes.max_tokens", config.max_tokens, minimum=0),
        max_extracted_chars=_parse_int(
            ai_notes.get("max_extracted_chars"), "ai_notes.max_extracted_chars", config.max_extracted_chars, minimum=0
        ),
        timeout_seconds=_parse_float(
            ai_notes.get("timeout_seconds"), "ai_notes.timeout_seconds", config.timeout_seconds, minimum=0.001
        ),
        max_retries=_parse_int(ai_notes.get("max_retries"), "ai_notes.max_retries", config.max_retries, minimum=0),
    )

    if apply_env_overrides:
        config.api_key = _parse_string(_env_value("ZOT_AI_API_KEY"), "ZOT_AI_API_KEY", config.api_key)
        config.base_url = _parse_string(_env_value("ZOT_AI_BASE_URL"), "ZOT_AI_BASE_URL", config.base_url)
        config.model = _parse_string(_env_value("ZOT_AI_MODEL"), "ZOT_AI_MODEL", config.model)
        config.fallback_models = _split_models(_env_value("ZOT_AI_FALLBACK_MODELS") or "") or config.fallback_models
        config.reasoning_effort = _parse_string(
            _env_value("ZOT_AI_REASONING_EFFORT"), "ZOT_AI_REASONING_EFFORT", config.reasoning_effort
        )
        config.temperature = _parse_float(_env_value("ZOT_AI_TEMPERATURE"), "ZOT_AI_TEMPERATURE", config.temperature)
        config.max_tokens = _parse_int(
            _env_value("ZOT_AI_MAX_TOKENS"), "ZOT_AI_MAX_TOKENS", config.max_tokens, minimum=0
        )
        config.max_extracted_chars = _parse_int(
            _env_value("ZOT_AI_MAX_EXTRACTED_CHARS"),
            "ZOT_AI_MAX_EXTRACTED_CHARS",
            config.max_extracted_chars,
            minimum=0,
        )
        config.timeout_seconds = _parse_float(
            _env_value("ZOT_AI_TIMEOUT"), "ZOT_AI_TIMEOUT", config.timeout_seconds, minimum=0.001
        )
        config.max_retries = _parse_int(
            _env_value("ZOT_AI_MAX_RETRIES"), "ZOT_AI_MAX_RETRIES", config.max_retries, minimum=0
        )
    return config


def list_profiles(path: Path | None = None) -> list[str]:
    """List all profile names from config."""
    data = read_config_data(path)
    profiles = data.get("profile", {})
    if not isinstance(profiles, dict):
        raise ConfigError("Invalid config: [profile] must be a table")
    return [str(name) for name in profiles]


def get_default_profile(path: Path | None = None) -> str:
    """Get the active/default profile name from config (blank when unset)."""
    data = read_config_data(path)
    default = data.get("default", {})
    if not isinstance(default, dict):
        raise ConfigError("Invalid config: [default] must be a table")
    return _parse_string(default.get("profile"), "[default] profile", "").strip()


# --- merge-preserving save ---------------------------------------------------


def merge_config_data(
    existing: dict[str, Any],
    config: AppConfig,
    *,
    profile: str | None = None,
    allow_new_profile: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Merge AppConfig values into raw TOML data, preserving every other key.

    Returns the merged data and the dotted keys that were written. Existing
    non-empty strings are never overwritten with an empty value and scalar
    defaults never clobber user-set values, so credentials and unrelated
    sections survive config-init reruns on profile-based configs.
    """
    profile = _normalize_profile(profile)
    data = copy.deepcopy(existing) if existing else {}
    changed: list[str] = []
    defaults = AppConfig()

    profiles_value = data.get("profile")
    if profiles_value is not None and not isinstance(profiles_value, dict):
        raise ConfigError("Invalid config: [profile] must be a table")
    profiles: dict[str, Any] = profiles_value if isinstance(profiles_value, dict) else {}
    default_value = data.get("default")
    if default_value is not None and not isinstance(default_value, dict):
        raise ConfigError("Invalid config: [default] must be a table")
    default_table: dict[str, Any] = default_value if isinstance(default_value, dict) else {}
    default_profile = _parse_string(default_table.get("profile"), "[default] profile", "")

    if profile is not None and profile not in profiles:
        if not allow_new_profile:
            raise ConfigError(f"Unknown config profile '{profile}'")
        profiles[profile] = {}
        data["profile"] = profiles
    if profile is not None:
        current_default = _normalize_profile(default_profile)
        # Point [default].profile at the explicit profile when it is unset or names a
        # profile that no longer exists, so an init can repair a broken default.
        if current_default is None or current_default not in profiles:
            default_table["profile"] = profile
            data["default"] = default_table
            changed.append("default.profile")
            default_profile = profile

    active_profile = resolve_active_profile(profiles, default_profile, explicit=profile)
    if active_profile is not None:
        profile_data = profiles.get(active_profile)
        if not isinstance(profile_data, dict):
            raise ConfigError(f"Unknown config profile '{active_profile}'")
        _merge_profile_section(profile_data, config, defaults, changed, f"profile.{active_profile}.")
        return data, changed

    zotero = _ensure_table(data, "zotero")
    _merge_zotero_values(zotero, config, defaults, changed, "zotero.")
    integrations = _ensure_table(data, "integrations")
    _record_string(
        integrations,
        "crossref_mailto",
        config.crossref_mailto,
        changed,
        "integrations.crossref_mailto",
        default=defaults.crossref_mailto,
    )
    output = _ensure_table(data, "output")
    _record_string(
        output,
        "default_format",
        config.default_format,
        changed,
        "output.default_format",
        default=defaults.default_format,
    )
    _record_scalar(output, "limit", config.default_limit, defaults.default_limit, changed, "output.limit")
    export = _ensure_table(data, "export")
    _record_string(
        export,
        "default_style",
        config.default_export_style,
        changed,
        "export.default_style",
        default=defaults.default_export_style,
    )
    return data, changed


def _merge_profile_section(
    profile_data: dict[str, Any], config: AppConfig, defaults: AppConfig, changed: list[str], prefix: str
) -> None:
    _merge_zotero_values(profile_data, config, defaults, changed, prefix)
    integrations_value = profile_data.get("integrations")
    if isinstance(integrations_value, dict):
        _record_string(
            integrations_value,
            "crossref_mailto",
            config.crossref_mailto,
            changed,
            prefix + "integrations.crossref_mailto",
            default=defaults.crossref_mailto,
        )
    else:
        _record_string(
            profile_data,
            "crossref_mailto",
            config.crossref_mailto,
            changed,
            prefix + "crossref_mailto",
            default=defaults.crossref_mailto,
        )
    output = _ensure_table(profile_data, "output")
    _record_string(
        output,
        "default_format",
        config.default_format,
        changed,
        prefix + "output.default_format",
        default=defaults.default_format,
    )
    _record_scalar(output, "limit", config.default_limit, defaults.default_limit, changed, prefix + "output.limit")
    export = _ensure_table(profile_data, "export")
    _record_string(
        export,
        "default_style",
        config.default_export_style,
        changed,
        prefix + "export.default_style",
        default=defaults.default_export_style,
    )


def _merge_zotero_values(
    mapping: dict[str, Any], config: AppConfig, defaults: AppConfig, changed: list[str], prefix: str
) -> None:
    _record_string(mapping, "data_dir", config.data_dir, changed, prefix + "data_dir", default=defaults.data_dir)
    _record_string(
        mapping, "library_id", config.library_id, changed, prefix + "library_id", default=defaults.library_id
    )
    _record_string(mapping, "api_key", config.api_key, changed, prefix + "api_key", default=defaults.api_key)
    _record_string(
        mapping,
        "semantic_scholar_api_key",
        config.semantic_scholar_api_key,
        changed,
        prefix + "semantic_scholar_api_key",
        default=defaults.semantic_scholar_api_key,
    )
    _record_string(
        mapping,
        "prefs_js_path",
        config.prefs_js_path,
        changed,
        prefix + "prefs_js_path",
        default=defaults.prefs_js_path,
    )


def _ensure_table(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        raise ConfigError(f"Invalid config: [{key}] must be a table")
    return value


def _record_string(
    mapping: dict[str, Any], key: str, value: str, changed: list[str], dotted: str, *, default: str = ""
) -> None:
    current = mapping.get(key)
    present = key in mapping
    should_write = not present or (value != default and value != current)
    if should_write and current != value:
        mapping[key] = value
        changed.append(dotted)


def _record_scalar(
    mapping: dict[str, Any], key: str, value: Any, default: Any, changed: list[str], dotted: str
) -> None:
    current = mapping.get(key)
    present = key in mapping
    if (not present or value != default) and current != value:
        mapping[key] = value
        changed.append(dotted)


# --- writing -----------------------------------------------------------------


def _toml_key(key: str) -> str:
    return key if _BARE_TOML_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _toml_datetime(value: datetime.datetime | datetime.date | datetime.time) -> str:
    # timespec="microseconds" always includes the seconds component TOML requires.
    if isinstance(value, (datetime.datetime, datetime.time)):
        return value.isoformat(timespec="microseconds")
    return value.isoformat()


def _toml_value(value: Any, *, path: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, str):
        # JSON string literals are valid TOML basic strings and escape quotes,
        # backslashes, control characters and Unicode correctly.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return _toml_datetime(value)
    if isinstance(value, list):
        rendered = [_toml_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
        return "[" + ", ".join(rendered) + "]"
    if isinstance(value, dict):
        return _toml_inline_table(value, path=path)
    raise ConfigError(
        f"Invalid config: value at {path} has unsupported type {type(value).__name__}; "
        "edit the file manually to remove it before running this command"
    )


def _toml_inline_table(table: dict[str, Any], *, path: str) -> str:
    items = [
        f"{_toml_key(key)} = {_toml_value(value, path=f'{path}.{key}')}"
        for key, value in table.items()
        if value is not None
    ]
    return "{ " + ", ".join(items) + " }" if items else "{}"


def _dump_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit_table(table: dict[str, Any], prefix: list[str]) -> None:
        if prefix:
            if lines:
                lines.append("")
            lines.append("[" + ".".join(_toml_key(part) for part in prefix) + "]")
        scalars = [(key, value) for key, value in table.items() if value is not None and not isinstance(value, dict)]
        tables = [(key, value) for key, value in table.items() if isinstance(value, dict)]
        for key, value in scalars:
            dotted = ".".join([*prefix, key])
            lines.append(f"{_toml_key(key)} = {_toml_value(value, path=dotted)}")
        for key, value in tables:
            emit_table(value, [*prefix, key])

    emit_table(data, [])
    lines.append("")
    return "\n".join(lines)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _prepare_config_directory(directory: Path) -> None:
    """Create missing config directories with 0700 permissions.

    Only directories created by this call are chmod-ed. A pre-existing parent
    (for example a foreign or sticky /tmp) is left exactly as it is: chmod on a
    directory the process does not own must never make a config save fail.
    """
    if directory.exists():
        return
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for candidate in reversed(missing):
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as exc:
            raise ConfigError(f"Could not create config directory {candidate}: {exc}") from exc
        try:
            candidate.chmod(0o700)
        except OSError:
            # The directory exists now; a failed chmod (umask/ACL quirk) must not
            # turn a successful config save into an error.
            pass


def _config_write_target(path: Path) -> Path:
    """Follow a symlinked config path so a save updates the target, not the link."""
    return path.resolve() if path.is_symlink() else path


def write_config_data(path: Path, data: dict[str, Any]) -> Path | None:
    """Write TOML data atomically with a .bak backup and restrictive permissions.

    Symlinked config paths write through to their target. Returns the .bak path
    created by this write, or None when the file already had identical content
    (no-op saves never rewrite the file or churn the backup).
    """
    target = _config_write_target(path)
    text = _dump_toml(data)
    encoded = text.encode("utf-8")
    if target.exists():
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise ConfigError(f"Could not read config file {target}: {exc}") from exc
        if current == encoded:
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            return None

    directory = target.parent
    _prepare_config_directory(directory)
    backup: Path | None = None
    if target.exists():
        backup = target.with_name(target.name + _BACKUP_SUFFIX)
        try:
            shutil.copy2(target, backup)
            os.chmod(backup, 0o600)
        except OSError as exc:
            raise ConfigError(f"Could not back up config file {target}: {exc}") from exc

    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(directory))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, target)
            temp_path = None
            _fsync_directory(directory)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError(f"Could not write config file {target}: {exc}") from exc
    return backup


def save_config(
    config: AppConfig,
    path: Path | None = None,
    *,
    profile: str | None = None,
    allow_new_profile: bool = False,
) -> Path:
    """Persist AppConfig values without dropping unrelated sections or profiles."""
    profile = profile or None
    resolved = path or paths.config_file_path()
    existing = read_config_data(resolved) if resolved.exists() else {}
    data, _changed = merge_config_data(existing, config, profile=profile, allow_new_profile=allow_new_profile)
    write_config_data(resolved, data)
    return resolved
