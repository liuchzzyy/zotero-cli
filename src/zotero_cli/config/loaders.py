"""Load config.toml sections and apply environment overrides."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from . import paths
from .models import (
    AiNoteConfig,
    AppConfig,
    EmbeddingConfig,
    PdfConfig,
    RerankConfig,
    SemanticSearchConfig,
    VectorStoreConfig,
)


def _load_toml_data(path: Path | None = None) -> dict[str, Any]:
    resolved = path or paths.config_file_path()
    if not resolved.exists():
        return {}
    with open(resolved, "rb") as f:
        loaded = tomllib.load(f)
    return loaded if isinstance(loaded, dict) else {}


def load_config(path: Path | None = None, profile: str | None = None) -> AppConfig:
    data = _load_toml_data(path)
    if profile and profile not in data.get("profile", {}):
        import click

        raise click.BadParameter(f"Unknown config profile '{profile}'", param_hint="--profile")
    if not data:
        return AppConfig()

    if "profile" in data:
        profile_name = profile or data.get("default", {}).get("profile", "")
        if profile_name and profile_name in data["profile"]:
            profile_data = data["profile"][profile_name]
            output = profile_data.get("output", data.get("output", {}))
            export = profile_data.get("export", data.get("export", {}))
            integrations = profile_data.get("integrations", data.get("integrations", {}))
            return AppConfig(
                data_dir=profile_data.get("data_dir", ""),
                library_id=profile_data.get("library_id", ""),
                api_key=profile_data.get("api_key", ""),
                semantic_scholar_api_key=profile_data.get("semantic_scholar_api_key", ""),
                crossref_mailto=profile_data.get("crossref_mailto", integrations.get("crossref_mailto", "")),
                default_format=output.get("default_format", "table"),
                default_limit=output.get("limit", 50),
                default_export_style=export.get("default_style", "bibtex"),
                prefs_js_path=profile_data.get("prefs_js_path", ""),
            )

    zotero = data.get("zotero", {})
    output = data.get("output", {})
    export = data.get("export", {})
    integrations = data.get("integrations", {})
    return AppConfig(
        data_dir=zotero.get("data_dir", ""),
        library_id=zotero.get("library_id", ""),
        api_key=zotero.get("api_key", ""),
        semantic_scholar_api_key=zotero.get("semantic_scholar_api_key", ""),
        crossref_mailto=integrations.get("crossref_mailto", ""),
        default_format=output.get("default_format", "table"),
        default_limit=output.get("limit", 50),
        default_export_style=export.get("default_style", "bibtex"),
        prefs_js_path=zotero.get("prefs_js_path", ""),
    )


def load_embedding_config(path: Path | None = None, *, apply_env_overrides: bool = False) -> EmbeddingConfig:
    defaults = EmbeddingConfig()
    data = _load_toml_data(path)
    if data:
        emb = _select_provider_config(
            data.get("embedding", {}),
            env_active="ZOT_EMBEDDING_ACTIVE" if apply_env_overrides else "",
        )
        defaults = EmbeddingConfig(
            active=emb.get("active", defaults.active),
            provider=emb.get("provider", defaults.provider),
            url=emb.get("url", defaults.url),
            api_key=emb.get("api_key", defaults.api_key),
            model=emb.get("model", defaults.model),
            batch_size=int(emb.get("batch_size", defaults.batch_size)),
        )
    if apply_env_overrides:
        defaults.active = os.environ.get("ZOT_EMBEDDING_ACTIVE", defaults.active)
        defaults.provider = os.environ.get("ZOT_EMBEDDING_PROVIDER", defaults.provider)
        defaults.url = os.environ.get("ZOT_EMBEDDING_URL", defaults.url)
        defaults.api_key = os.environ.get("ZOT_EMBEDDING_KEY", defaults.api_key)
        defaults.model = os.environ.get("ZOT_EMBEDDING_MODEL", defaults.model)
        defaults.batch_size = int(os.environ.get("ZOT_EMBEDDING_BATCH_SIZE", defaults.batch_size))
    return defaults


def load_rerank_config(path: Path | None = None, *, apply_env_overrides: bool = False) -> RerankConfig:
    defaults = RerankConfig()
    data = _load_toml_data(path)
    if data:
        rerank = _select_provider_config(
            data.get("rerank", {}),
            env_active="ZOT_RERANK_ACTIVE" if apply_env_overrides else "",
        )
        defaults = RerankConfig(
            active=rerank.get("active", defaults.active),
            provider=rerank.get("provider", defaults.provider),
            url=rerank.get("url", defaults.url),
            api_key=rerank.get("api_key", defaults.api_key),
            model=rerank.get("model", defaults.model),
            batch_size=int(rerank.get("batch_size", defaults.batch_size)),
        )
    if apply_env_overrides:
        defaults.active = os.environ.get("ZOT_RERANK_ACTIVE", defaults.active)
        defaults.provider = os.environ.get("ZOT_RERANK_PROVIDER", defaults.provider)
        defaults.url = os.environ.get("ZOT_RERANK_URL", defaults.url)
        defaults.api_key = os.environ.get("ZOT_RERANK_KEY", defaults.api_key)
        defaults.model = os.environ.get("ZOT_RERANK_MODEL", defaults.model)
        defaults.batch_size = int(os.environ.get("ZOT_RERANK_BATCH_SIZE", defaults.batch_size))
    return defaults


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
    data = _load_toml_data(path)
    pdf = data.get("pdf", {}) if data else {}
    config = PdfConfig(
        mineru_token=pdf.get("mineru_token", defaults.mineru_token),
        mineru_model_version=pdf.get("mineru_model_version", defaults.mineru_model_version),
    )
    if apply_env_overrides:
        config.mineru_token = os.environ.get("ZOT_MINERU_TOKEN", config.mineru_token)
        config.mineru_model_version = os.environ.get("ZOT_MINERU_MODEL_VERSION", config.mineru_model_version)
    return config


def load_vector_store_config(path: Path | None = None) -> VectorStoreConfig:
    defaults = VectorStoreConfig()
    data = _load_toml_data(path)
    if not data:
        return defaults
    vs = data.get("vector_store", {})
    return VectorStoreConfig(
        provider=vs.get("provider", defaults.provider),
        path=vs.get("path", defaults.path),
    )


def load_semantic_search_config(path: Path | None = None) -> SemanticSearchConfig:
    defaults = SemanticSearchConfig()
    data = _load_toml_data(path)
    if not data:
        return defaults
    ss = data.get("semantic_search", {})
    return SemanticSearchConfig(
        candidate_k=int(ss.get("candidate_k", defaults.candidate_k)),
        top_k=int(ss.get("top_k", defaults.top_k)),
        rrf_k=int(ss.get("rrf_k", defaults.rrf_k)),
        semantic_weight=float(ss.get("semantic_weight", defaults.semantic_weight)),
        bm25_weight=float(ss.get("bm25_weight", defaults.bm25_weight)),
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
    data = _load_toml_data(path)
    if data:
        ai_notes = data.get("ai_notes", {})
        config = AiNoteConfig(
            api_key=ai_notes.get("api_key", config.api_key),
            base_url=ai_notes.get("base_url", config.base_url),
            model=ai_notes.get("model", config.model),
            fallback_models=_split_models(ai_notes.get("fallback_models")) or config.fallback_models,
            reasoning_effort=ai_notes.get("reasoning_effort", config.reasoning_effort),
            api_mode=ai_notes.get("api_mode", config.api_mode),
            chat_token_param=ai_notes.get("chat_token_param", config.chat_token_param),
            temperature=float(ai_notes.get("temperature", config.temperature)),
            max_tokens=int(ai_notes.get("max_tokens", config.max_tokens)),
            max_extracted_chars=int(ai_notes.get("max_extracted_chars", config.max_extracted_chars)),
        )

    if apply_env_overrides:
        config.api_key = os.environ.get("ZOT_AI_API_KEY", config.api_key)
        config.base_url = os.environ.get("ZOT_AI_BASE_URL", config.base_url)
        config.model = os.environ.get("ZOT_AI_MODEL", config.model)
        config.fallback_models = _split_models(os.environ.get("ZOT_AI_FALLBACK_MODELS", "")) or config.fallback_models
        config.reasoning_effort = os.environ.get("ZOT_AI_REASONING_EFFORT", config.reasoning_effort)
        config.temperature = float(os.environ.get("ZOT_AI_TEMPERATURE", config.temperature))
        config.max_tokens = int(os.environ.get("ZOT_AI_MAX_TOKENS", config.max_tokens))
        config.max_extracted_chars = int(os.environ.get("ZOT_AI_MAX_EXTRACTED_CHARS", config.max_extracted_chars))
    return config


def list_profiles(path: Path | None = None) -> list[str]:
    """List all profile names from config."""
    return list(_load_toml_data(path).get("profile", {}).keys())


def get_default_profile(path: Path | None = None) -> str:
    """Get the default profile name from config."""
    return str(_load_toml_data(path).get("default", {}).get("profile", ""))


def save_config(config: AppConfig, path: Path | None = None) -> None:
    resolved = path or paths.config_file_path()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    # JSON string literals are also valid TOML basic strings and correctly
    # escape quotes, backslashes, control characters, and non-ASCII values.
    # Hand-written single-quoted TOML breaks as soon as a path or credential
    # contains an apostrophe.
    def quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    lines = [
        "[zotero]",
        f"data_dir = {quote(config.data_dir)}",
        f"library_id = {quote(config.library_id)}",
        f"api_key = {quote(config.api_key)}",
        f"semantic_scholar_api_key = {quote(config.semantic_scholar_api_key)}",
        f"prefs_js_path = {quote(config.prefs_js_path)}",
        "",
        "[integrations]",
        f"crossref_mailto = {quote(config.crossref_mailto)}",
        "",
        "[output]",
        f"default_format = {quote(config.default_format)}",
        f"limit = {config.default_limit}",
        "",
        "[export]",
        f"default_style = {quote(config.default_export_style)}",
        "",
    ]
    resolved.write_text("\n".join(lines), encoding="utf-8")
