"""解析 Zotero 数据目录、凭据与本地文库 ID。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .sections import AppConfig


class LibraryResolutionError(Exception):
    """无法从本地数据库解析所请求的群组文库。

    CLI 会将其映射为类型化的 `not_found`（退出码 4）；调用方不得回退到
    用户文库，因为那会静默改变写入目标。
    """

    code = "not_found"
    retryable = False

    def __init__(self, group_id: str) -> None:
        super().__init__(f"Group '{group_id}' not found in local database")
        self.group_id = group_id


def _detect_zotero_data_dir_from_registry() -> Path | None:
    """从 Windows 注册表检测 Zotero 数据目录。"""
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
    """获取 Zotero 数据目录：环境变量覆盖 > 配置 > 默认值。"""
    env_dir = os.environ.get("ZOT_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return detect_zotero_data_dir(config)


def get_prefs_js_path(config: AppConfig) -> Path | None:
    """获取 Zotero prefs.js 路径：环境变量覆盖 > 配置。"""
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
    """从 ctx.obj 解析 library_id，默认值为 1（用户文库）。"""
    if ctx_obj.get("library_type") != "group" or not ctx_obj.get("group_id"):
        return 1
    from zotero_cli.zotero.reader import ZoteroReader

    reader = ZoteroReader(db_path)
    try:
        resolved = reader.resolve_group_library_id(int(ctx_obj["group_id"]))
    finally:
        reader.close()
    if resolved is None:
        raise LibraryResolutionError(str(ctx_obj["group_id"]))
    return resolved
