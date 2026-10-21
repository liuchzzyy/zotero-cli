"""配置与状态路径。

源码检出将配置和运行时状态保存在仓库内部
（/.zot、/.workspace）。已安装的 wheel 则按用户确定性地解析两者，
而不依赖当前工作目录：

* 状态：绝对路径 `ZOT_STATE_DIR`，否则 `XDG_STATE_HOME/zotero-cli`
  （`~/.local/state/zotero-cli`）或 Windows 的 `%LOCALAPPDATA%/zotero-cli`；
* 配置：`XDG_CONFIG_HOME/zotero-cli/config.toml`（`~/.config/...`）或
  Windows 的 `%APPDATA%/zotero-cli/config.toml`。

相对的 XDG/ZOT 路径环境变量值会被忽略（XDG 基础目录必须是绝对路径），
改用按用户的默认值，因此状态既不会随工作目录分散，也不会静默读取到
其他项目的配置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SETTINGS_DIRNAME = ".zot"

CONFIG_FILENAME = "config.toml"

STATE_DIRNAME = "state"

WORKSPACE_DIRNAME = ".workspace"

APP_DIRNAME = "zotero-cli"


def _absolute_env_path(name: str) -> Path | None:
    """从环境变量返回绝对且已展开 ~ 的路径；相对值会被忽略。"""
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else None


def _find_project_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _source_project_root() -> Path | None:
    return _find_project_root(Path(__file__).resolve())


def checkout_root() -> Path | None:
    """返回源码检出根目录；对已安装的包返回 None。"""
    return _source_project_root() or _find_project_root(Path.cwd().resolve())


def project_root(start: Path | None = None) -> Path:
    """返回检出根目录（配置发现回退：搜索 CWD 标记）。"""
    if start is not None:
        current = start.resolve()
        return _find_project_root(current) or current

    source_root = _source_project_root()
    if source_root is not None:
        return source_root

    current = Path.cwd().resolve()
    return _find_project_root(current) or current


def _installed_state_root() -> Path:
    override = _absolute_env_path("ZOT_STATE_DIR")
    if override is not None:
        return override
    if sys.platform == "win32":
        base = _absolute_env_path("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return base / APP_DIRNAME
    xdg_state = _absolute_env_path("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return xdg_state / APP_DIRNAME


def config_search_dir() -> Path:
    """存放 config.toml 的目录：源码检出为仓库本地，否则按用户。

    已安装的包绝不会从当前工作目录读取配置，因此在无关项目中运行 zot
    不会意外读取该项目的 .zot/config.toml（或其凭据）。
    """
    source_root = _source_project_root()
    if source_root is not None:
        return source_root / SETTINGS_DIRNAME
    if sys.platform == "win32":
        base = _absolute_env_path("APPDATA") or Path.home() / "AppData" / "Roaming"
        return base / APP_DIRNAME
    xdg_config = _absolute_env_path("XDG_CONFIG_HOME") or Path.home() / ".config"
    return xdg_config / APP_DIRNAME


def state_root() -> Path:
    """持久化运行时状态的根目录（缓存、幂等数据库、版本检查）。

    检出的仓库保持其历史位置（/.zot/state）；已安装的包使用按用户的确定性
    状态根目录，因此状态不会因 CWD 而分散。
    """
    root = checkout_root()
    if root is not None:
        return root / SETTINGS_DIRNAME / STATE_DIRNAME
    return _installed_state_root()


def data_root() -> Path:
    """仓库/工作区数据的基础目录：检出根目录，否则状态根目录。"""
    root = checkout_root()
    return root if root is not None else state_root()


def workspace_root() -> Path:
    """工作区的根目录：检出中为 /.workspace，安装后为状态根目录。"""
    root = checkout_root()
    if root is not None:
        return root / WORKSPACE_DIRNAME
    return state_root() / WORKSPACE_DIRNAME


def settings_dir(start: Path | None = None) -> Path:
    return project_root(start) / SETTINGS_DIRNAME


def config_file_path(start: Path | None = None) -> Path:
    if start is not None:
        return settings_dir(start) / CONFIG_FILENAME
    return config_search_dir() / CONFIG_FILENAME


def state_dir(start: Path | None = None) -> Path:
    """状态目录；显式传入 `start` 时保持历史性的仓库相对形式。"""
    if start is not None:
        return settings_dir(start) / STATE_DIRNAME
    return state_root()
