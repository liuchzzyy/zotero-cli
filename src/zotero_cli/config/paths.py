"""Configuration and state paths.

Source checkouts keep configuration and runtime state inside the repository
(/.zot, /.workspace). Installed wheels resolve both deterministically per user
instead of depending on the current working directory:

* state: absolute `ZOT_STATE_DIR`, else `XDG_STATE_HOME/zotero-cli`
  (`~/.local/state/zotero-cli`) or Windows `%LOCALAPPDATA%/zotero-cli`;
* config: `XDG_CONFIG_HOME/zotero-cli/config.toml` (`~/.config/...`) or
  Windows `%APPDATA%/zotero-cli/config.toml`.

Relative XDG/ZOT path env values are ignored (XDG base directories must be
absolute) and the per-user default is used instead, so state never fragments
by working directory nor silently picks up a foreign project's config.
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
    """Return an absolute, ~-expanded path from an env var; relative values are ignored."""
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
    """Return the source-checkout root, or None for an installed package."""
    return _source_project_root() or _find_project_root(Path.cwd().resolve())


def project_root(start: Path | None = None) -> Path:
    """Return the checkout root (config discovery fallback: CWD marker search)."""
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
    """Directory holding config.toml: repo-local for a source checkout, else per-user.

    Installed packages never read a config from the current working directory, so
    running zot inside an unrelated project cannot pick up that project's
    .zot/config.toml (or its credentials).
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
    """Root for persistent runtime state (caches, idempotency DB, version check).

    Checkouts keep their historical location (/.zot/state); installed packages use
    the deterministic per-user state root so state never fragments by CWD.
    """
    root = checkout_root()
    if root is not None:
        return root / SETTINGS_DIRNAME / STATE_DIRNAME
    return _installed_state_root()


def data_root() -> Path:
    """Base for repository/workspace data: checkout root, else the state root."""
    root = checkout_root()
    return root if root is not None else state_root()


def workspace_root() -> Path:
    """Root for workspaces: /.workspace in a checkout, state root when installed."""
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
    """State directory; an explicit `start` keeps the historical repo-relative form."""
    if start is not None:
        return settings_dir(start) / STATE_DIRNAME
    return state_root()
