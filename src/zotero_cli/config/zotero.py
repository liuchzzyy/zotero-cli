"""Resolve Zotero data directories, credentials, and local library ids."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .models import AppConfig


class LibraryResolutionError(Exception):
    """A requested group library could not be resolved from the local database.

    The CLI maps this to a typed `not_found` (exit 4); callers must not fall back
    to the user library because that would silently redirect writes.
    """

    code = "not_found"
    retryable = False

    def __init__(self, group_id: str) -> None:
        super().__init__(f"Group '{group_id}' not found in local database")
        self.group_id = group_id


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
        return Path(env_dir).expanduser()
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
    from zotero_cli.core.zotero.reader import ZoteroReader

    reader = ZoteroReader(db_path)
    try:
        resolved = reader.resolve_group_library_id(int(ctx_obj["group_id"]))
    finally:
        reader.close()
    if resolved is None:
        raise LibraryResolutionError(str(ctx_obj["group_id"]))
    return resolved
