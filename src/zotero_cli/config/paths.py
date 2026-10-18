"""Configuration and state paths.

Checkouts keep configuration and runtime state inside the repository (/.zot,
/.workspace). Installed wheels have no checkout marker, so persistent state is
resolved deterministically per user instead of depending on the current working
directory: `ZOT_STATE_DIR` first, then `XDG_STATE_HOME/zotero-cli`
(`~/.local/state/zotero-cli`) or Windows `%LOCALAPPDATA%/zotero-cli`.
Configuration discovery keeps the historical checkout/CWD behavior.
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
    override = os.environ.get("ZOT_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return Path(base).expanduser() / APP_DIRNAME if base else Path.home() / "AppData" / "Local" / APP_DIRNAME
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg_state).expanduser() if xdg_state else Path.home() / ".local" / "state"
    return base / APP_DIRNAME


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
    return settings_dir(start) / CONFIG_FILENAME


def state_dir(start: Path | None = None) -> Path:
    """State directory; an explicit `start` keeps the historical repo-relative form."""
    if start is not None:
        return settings_dir(start) / STATE_DIRNAME
    return state_root()
