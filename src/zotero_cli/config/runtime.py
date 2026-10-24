"""统一的 TOML 运行策略及其来源；保留用户的原始配置。"""

from __future__ import annotations

import os
import tomllib
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .paths import config_file_path

# Connection and identity fields are user data rather than built-in defaults.
_USER_FIELDS = {
    "default": {"profile"},
    "profile": None,
    "integrations": {"crossref_mailto"},
    "export": set(),
    "output": set(),
    "embedding": {"active", "api_key"},
    "rerank": {"active", "api_key"},
    "pdf": {"mineru_token"},
    "ai_notes": {"api_key", "base_url"},
    "zotero": {"data_dir", "library_id", "api_key", "semantic_scholar_api_key", "prefs_js_path"},
}
_PROVIDER_FIELDS = {"provider", "url", "api_key", "model", "batch_size", "timeout_seconds"}
_PROFILE_SCALARS: set[str] = {
    "data_dir",
    "library_id",
    "api_key",
    "semantic_scholar_api_key",
    "prefs_js_path",
    "crossref_mailto",
}


@lru_cache(maxsize=1)
def built_in_defaults() -> dict[str, Any]:
    return tomllib.loads(files("zotero_cli.config").joinpath("defaults.toml").read_text(encoding="utf-8"))


def _merge(
    target: dict[str, Any], incoming: dict[str, Any], source: str, provenance: dict[str, str], prefix: str = ""
) -> None:
    for key, value in incoming.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value, source, provenance, name)
        else:
            target[key] = deepcopy(value)
            provenance[name] = source


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, member in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(member, dict):
            result.update(_flatten(member, name))
        else:
            result[name] = member
    return result


def _validate(section: dict[str, Any], standard: dict[str, Any], prefix: str = "") -> None:
    for key, value in section.items():
        path = f"{prefix}.{key}" if prefix else key
        if prefix == "profile" and isinstance(value, dict):
            # Validate profile-scoped sections against the top-level registry.
            for nested, table in value.items():
                if nested in _PROFILE_SCALARS:
                    continue
                if nested not in standard or not isinstance(table, dict):
                    raise ConfigError(f"Unknown setting: {path}.{nested}")
                _validate(table, standard[nested], f"{path}.{nested}")
            continue
        if key not in standard:
            if prefix in _USER_FIELDS and key in (_USER_FIELDS[prefix] or set()):
                continue
            if not prefix and key in _USER_FIELDS:
                if isinstance(value, dict):
                    allowed = _USER_FIELDS[key]
                    if allowed is None:
                        _validate(value, standard, key)
                    else:
                        _validate(value, {**{name: None for name in allowed}, **standard.get(key, {})}, key)
                    continue
            if (
                prefix in {"embedding", "rerank"}
                or ".embedding" in prefix
                or ".rerank" in prefix
                or prefix.startswith(("embedding.", "rerank."))
            ) and isinstance(value, dict):
                _validate(value, {name: None for name in _PROVIDER_FIELDS}, path)
                continue
            if prefix.startswith("embedding.") or prefix.startswith("rerank."):
                if key in _PROVIDER_FIELDS:
                    continue
            if prefix == "ai_notes.routes" and isinstance(value, dict):
                _validate(value, {"context": 0, "per_image": 0}, path)
                continue
            raise ConfigError(f"Unknown setting: {path}")
        expected = standard[key]
        if isinstance(value, dict):
            if not isinstance(expected, dict):
                raise ConfigError(f"Invalid setting {path}: expected scalar")
            _validate(value, expected, path)
        elif isinstance(expected, dict):
            raise ConfigError(f"Invalid setting {path}: expected table")
        elif expected is not None and not (
            (isinstance(expected, bool) and type(value) is bool)
            or (isinstance(expected, int) and not isinstance(expected, bool) and type(value) is int)
            or (isinstance(expected, float) and type(value) in {int, float})
            or (isinstance(expected, str) and type(value) is str)
            or (isinstance(expected, list) and isinstance(value, list) and all(isinstance(x, str) for x in value))
        ):
            kind = (
                "an integer"
                if isinstance(expected, int) and not isinstance(expected, bool)
                else "a number"
                if isinstance(expected, float)
                else "a string"
                if isinstance(expected, str)
                else "a list of strings"
                if isinstance(expected, list)
                else "a boolean"
            )
            raise ConfigError(f"Invalid config: {path} must be {kind}")


def resolve_settings(
    path: Path | None = None,
    *,
    profile: str | None = None,
    environment: dict[str, str] | None = None,
    cli: dict[str, Any] | None = None,
    validate: bool = True,
) -> tuple[dict[str, Any], dict[str, str]]:
    """解析显式CLI > 环境 > profile > TOML > 内置默认，返回值与来源。"""
    env = os.environ if environment is None else environment
    defaults = built_in_defaults()
    target = deepcopy(defaults)
    origins = {key: "default" for key in _flatten(defaults)}
    config_path = path or config_file_path()
    if config_path.exists():
        try:
            user = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Invalid config {config_path}: {exc}") from exc
    else:
        user = {}
    if validate:
        _validate(user, defaults)
    ordinary = {k: v for k, v in user.items() if k not in {"profile", "default", "units"}}
    _merge(target, ordinary, "toml", origins)
    profiles = user.get("profile", {})
    selected = profile or env.get("ZOT_PROFILE") or user.get("default", {}).get("profile")
    if selected is not None:
        if selected not in profiles:
            raise ConfigError(f"Unknown config profile {selected!r}")
        scoped = profiles[selected]
        _merge(
            target,
            {k: v for k, v in scoped.items() if k not in _PROFILE_SCALARS},
            "profile",
            origins,
        )
    # Legacy provider selection: nested [embedding.api.gitee] / [rerank.api.gitee].
    for section in ("embedding", "rerank"):
        selected_provider = env.get(f"ZOT_{section.upper()}_ACTIVE") or target[section].get("active")
        if selected_provider:
            value = target[section]
            for part in selected_provider.split("."):
                value = value.get(part, {}) if isinstance(value, dict) else {}
            if isinstance(value, dict):
                _merge(
                    target[section],
                    {k: v for k, v in value.items() if not isinstance(v, dict)},
                    origins.get(f"{section}.{selected_provider}.provider", "toml"),
                    origins,
                    section,
                )
    env_map = {
        "ZOT_AI_TIMEOUT": "ai_notes.timeout_seconds",
        "ZOT_AI_MAX_RETRIES": "ai_notes.max_retries",
        "ZOT_AI_API_KEY": "ai_notes.api_key",
        "ZOT_AI_BASE_URL": "ai_notes.base_url",
        "ZOT_AI_MODEL": "ai_notes.model",
        "ZOT_AI_MAX_TOKENS": "ai_notes.max_tokens",
        "ZOT_AI_FALLBACK_MODELS": "ai_notes.fallback_models",
        "ZOT_AI_TEMPERATURE": "ai_notes.temperature",
        "ZOT_AI_REASONING_EFFORT": "ai_notes.reasoning_effort",
        "ZOT_AI_MAX_EXTRACTED_CHARS": "ai_notes.max_extracted_chars",
        "ZOT_MINERU_TOKEN": "pdf.mineru_token",
        "ZOT_MINERU_MODEL_VERSION": "pdf.mineru_model_version",
    }
    for section in ("EMBEDDING", "RERANK"):
        for field, suffix in (
            ("url", "URL"),
            ("model", "MODEL"),
            ("provider", "PROVIDER"),
            ("api_key", "KEY"),
            ("batch_size", "BATCH_SIZE"),
        ):
            env_map[f"ZOT_{section}_{suffix}"] = f"{section.lower()}.{field}"
    for name, dotted in env_map.items():
        if name not in env:
            continue
        value: Any = env[name]
        keys = dotted.split(".")
        cursor = target
        for key in keys[:-1]:
            cursor = cursor[key]
        existing = cursor.get(keys[-1])
        if isinstance(existing, list):
            value = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(existing, int):
            try:
                value = int(value)
            except ValueError as exc:
                raise ConfigError(f"Invalid {name}: expected integer") from exc
        elif isinstance(existing, float):
            try:
                value = float(value)
            except ValueError as exc:
                raise ConfigError(f"Invalid {name}: expected number") from exc
        cursor[keys[-1]] = value
        origins[dotted] = f"env:{name}"
    for dotted, value in (cli or {}).items():
        if value is None:
            continue
        cursor = target
        parts = dotted.split(".")
        for key in parts[:-1]:
            cursor = cursor[key]
        if parts[-1] not in cursor:
            raise ConfigError(f"Unknown CLI setting: {dotted}")
        cursor[parts[-1]] = value
        origins[dotted] = "cli"
    flattened_defaults = _flatten(defaults)
    flattened_target = _flatten(target)
    for name, default in flattened_defaults.items():
        value = flattened_target[name]
        if type(value) is not type(default) and not (isinstance(default, float) and type(value) is int):
            raise ConfigError(f"Invalid setting: {name}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = (
                1
                if name
                in {
                    "embedding.batch_size",
                    "rerank.batch_size",
                    "semantic_search.candidate_k",
                    "semantic_search.top_k",
                    "semantic_search.rrf_k",
                }
                else 0
            )
            if value < minimum:
                raise ConfigError(f"Invalid config: {name} must be >= {minimum}")
    model = target["ai_notes"]["model"].strip()
    if not model or any(not item.strip() for item in target["ai_notes"]["fallback_models"]):
        raise ConfigError("Invalid ai_notes.model or ai_notes.fallback_models: model chain must be nonempty")
    return target, origins


def settings() -> dict[str, Any]:
    """返回当前生效运行策略（含用户 TOML 覆盖）；按文件 mtime 与环境缓存。"""
    path = config_file_path()
    try:
        stamp = path.stat().st_mtime_ns if path.exists() else 0
    except OSError:
        stamp = 0
    env_key = tuple(sorted((key, value) for key, value in os.environ.items() if key.startswith("ZOT_")))
    return _cached_settings(str(path), stamp, env_key)


@lru_cache(maxsize=8)
def _cached_settings(path: str, stamp: int, env_key: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    values, _ = resolve_settings(Path(path) if path else None, environment=dict(env_key))
    return values


def section(name: str) -> dict[str, Any]:
    """按分节名读取生效值，例如 ``section("ai_notes")``。"""
    value = settings()
    for part in name.split("."):
        value = value[part]
    return value


def effective_settings(path: Path | None = None, *, profile: str | None = None) -> dict[str, dict[str, Any]]:
    values, origins = resolve_settings(path, profile=profile)
    flattened = _flatten(values)
    result = {}
    for key in _flatten(built_in_defaults()):
        value = flattened.get(key)
        if key.endswith(("api_key", "mineru_token")):
            value = {"set": bool(value)}
        unit = (
            "bytes"
            if key.endswith("_bytes")
            else "seconds"
            if key.endswith("_seconds")
            else "characters"
            if key.endswith("_chars")
            else "tokens"
            if key.endswith("_tokens")
            else "count"
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else "value"
        )
        meaning = (
            "0 disables this cap or leaves it to the provider"
            if value == 0
            else "empty list disables fallbacks"
            if key == "ai_notes.fallback_models"
            else key.replace("_", " ").split(".")[-1]
        )
        result[key] = {"value": value, "source": origins[key], "unit": unit, "meaning": meaning}
    for dotted in ("ai_notes.api_key", "ai_notes.base_url", "pdf.mineru_token", "embedding.api_key", "rerank.api_key"):
        keys = dotted.split(".")
        obj = values
        for key in keys:
            obj = obj.get(key, {}) if isinstance(obj, dict) else {}
        result[dotted] = {
            "value": {"set": bool(obj)} if dotted.endswith(("api_key", "mineru_token")) else obj,
            "source": origins.get(dotted, "unset"),
            "unit": "value",
            "meaning": dotted.split(".")[-1],
        }
    return result
