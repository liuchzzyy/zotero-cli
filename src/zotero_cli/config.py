from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

SETTINGS_DIRNAME = ".zot"
CONFIG_FILENAME = "config.toml"
STATE_DIRNAME = "state"


def _find_project_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _source_project_root() -> Path | None:
    return _find_project_root(Path(__file__).resolve())


def project_root(start: Path | None = None) -> Path:
    if start is not None:
        current = start.resolve()
        return _find_project_root(current) or current

    source_root = _source_project_root()
    if source_root is not None:
        return source_root

    current = Path.cwd().resolve()
    return _find_project_root(current) or current


def settings_dir(start: Path | None = None) -> Path:
    return project_root(start) / SETTINGS_DIRNAME


def config_file_path(start: Path | None = None) -> Path:
    return settings_dir(start) / CONFIG_FILENAME


def state_dir(start: Path | None = None) -> Path:
    return settings_dir(start) / STATE_DIRNAME


CONFIG_DIR = settings_dir()
CONFIG_FILE = config_file_path()


def _load_toml_data(path: Path | None = None) -> dict[str, Any]:
    resolved = path or config_file_path()
    if not resolved.exists():
        return {}
    with open(resolved, "rb") as f:
        loaded = tomllib.load(f)
    return loaded if isinstance(loaded, dict) else {}


def _detect_zotero_data_dir_from_registry() -> Path | None:
    """Detect Zotero data directory from Windows Registry."""
    if sys.platform != "win32":
        return None

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Zotero\Zotero") as key:
            data_dir, _ = winreg.QueryValueEx(key, "dataDir")
            if data_dir and Path(data_dir).exists():
                return Path(data_dir)
    except (OSError, FileNotFoundError, ImportError):
        pass

    return None


@dataclass
class AppConfig:
    data_dir: str = ""
    library_id: str = ""
    api_key: str = ""
    semantic_scholar_api_key: str = ""
    crossref_mailto: str = ""
    default_format: str = "table"
    default_limit: int = 50
    default_export_style: str = "bibtex"
    prefs_js_path: str = ""

    @property
    def has_write_credentials(self) -> bool:
        return bool(self.library_id and self.api_key)


@dataclass
class EmbeddingConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1"
    api_key: str = ""
    model: str = "bge-m3"
    batch_size: int = 50

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class RerankConfig:
    active: str = ""
    provider: str = "gitee"
    url: str = "https://ai.gitee.com/v1/rerank"
    api_key: str = ""
    model: str = "bge-reranker-v2-m3"
    batch_size: int = 16

    @property
    def is_configured(self) -> bool:
        return bool(self.url and self.api_key and self.model)


@dataclass
class VectorStoreConfig:
    provider: str = "qdrant_local"
    path: str = ".workspace/_qdrant"


@dataclass
class SemanticSearchConfig:
    candidate_k: int = 150
    top_k: int = 20
    rrf_k: int = 60
    semantic_weight: float = 0.8
    bm25_weight: float = 0.2


@dataclass
class PdfConfig:
    mineru_token: str = ""
    mineru_model_version: str = "vlm"


@dataclass
class AiNoteConfig:
    api_key: str = ""
    base_url: str = ""
    model: str = "gpt-5.4"
    reasoning_effort: str = "high"
    api_mode: str = "auto"
    chat_token_param: str = "auto"
    temperature: float = 0.7
    max_tokens: int = 0
    max_extracted_chars: int = 180000


def load_config(path: Path | None = None, profile: str | None = None) -> AppConfig:
    data = _load_toml_data(path)
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


def load_ai_note_config(path: Path | None = None) -> AiNoteConfig:
    defaults = AiNoteConfig()
    data = _load_toml_data(path)
    if not data:
        return defaults
    ai_notes = data.get("ai_notes", {})
    return AiNoteConfig(
        api_key=ai_notes.get("api_key", defaults.api_key),
        base_url=ai_notes.get("base_url", defaults.base_url),
        model=ai_notes.get("model", defaults.model),
        reasoning_effort=ai_notes.get("reasoning_effort", defaults.reasoning_effort),
        api_mode=ai_notes.get("api_mode", defaults.api_mode),
        chat_token_param=ai_notes.get("chat_token_param", defaults.chat_token_param),
        temperature=float(ai_notes.get("temperature", defaults.temperature)),
        max_tokens=int(ai_notes.get("max_tokens", defaults.max_tokens)),
        max_extracted_chars=int(ai_notes.get("max_extracted_chars", defaults.max_extracted_chars)),
    )


def list_profiles(path: Path | None = None) -> list[str]:
    """List all profile names from config."""
    return list(_load_toml_data(path).get("profile", {}).keys())


def get_default_profile(path: Path | None = None) -> str:
    """Get the default profile name from config."""
    return str(_load_toml_data(path).get("default", {}).get("profile", ""))


def save_config(config: AppConfig, path: Path | None = None) -> None:
    resolved = path or config_file_path()
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


def detect_zotero_data_dir(config: AppConfig) -> Path:
    if config.data_dir:
        return Path(config.data_dir).expanduser()

    if sys.platform == "win32":
        registry_dir = _detect_zotero_data_dir_from_registry()
        if registry_dir:
            return registry_dir

        appdata = Path(os.environ.get("APPDATA", ""))
        if appdata and (appdata / "Zotero").exists():
            return appdata / "Zotero"

        local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
        if local_appdata and (local_appdata / "Zotero").exists():
            return local_appdata / "Zotero"

        return appdata / "Zotero"

    return Path.home() / "Zotero"


def get_data_dir(config: AppConfig) -> Path:
    """Get Zotero data dir: env override > config > default."""
    env_dir = os.environ.get("ZOT_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return detect_zotero_data_dir(config)


def get_prefs_js_path(config: AppConfig) -> Path | None:
    """Get Zotero prefs.js path: env override > config."""
    env_path = os.environ.get("ZOT_PREFS_JS_PATH")
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            if path.is_dir():
                path = path / "prefs.js"
            return path if path.exists() else None
        return None
    if config.prefs_js_path:
        path = Path(config.prefs_js_path).expanduser()
        if path.exists():
            if path.is_dir():
                path = path / "prefs.js"
            return path if path.exists() else None
        return None
    return None


def resolve_write_credentials(
    config: AppConfig, *, library_type: str = "user", group_id: str | None = None
) -> tuple[str, str]:
    library_id = os.environ.get("ZOT_LIBRARY_ID", config.library_id)
    api_key = os.environ.get("ZOT_API_KEY", config.api_key)
    if library_type == "group" and group_id:
        library_id = group_id
    return library_id, api_key


def resolve_semantic_scholar_api_key(config: AppConfig, explicit: str | None = None) -> str:
    return explicit or config.semantic_scholar_api_key


def resolve_crossref_mailto(config: AppConfig) -> str:
    env_mailto = os.environ.get("ZOT_CROSSREF_MAILTO", "").strip()
    if env_mailto:
        return env_mailto
    return config.crossref_mailto.strip()


def resolve_library_id(db_path: Path, ctx_obj: dict) -> int:
    """Resolve the library_id from ctx.obj, defaulting to 1 (user library)."""
    if ctx_obj.get("library_type") != "group" or not ctx_obj.get("group_id"):
        return 1
    from zotero_cli.core.reader import ZoteroReader

    reader = ZoteroReader(db_path)
    try:
        resolved = reader.resolve_group_library_id(int(ctx_obj["group_id"]))
    finally:
        reader.close()
    if resolved is None:
        import click

        raise click.ClickException(f"Group '{ctx_obj['group_id']}' not found in local database")
    return resolved
