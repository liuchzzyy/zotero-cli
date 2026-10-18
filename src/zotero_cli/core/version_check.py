"""Check PyPI for newer versions of zotero-cli.

Hardening rules (M-22/L-10 follow-up):
* no network call or cache write unless the caller opted in (the CLI only calls
  this in interactive TTY mode, and ZOT_NO_UPDATE_CHECK=1 disables it entirely);
* the cache path is resolved lazily through config.state_dir() so state-root
  changes are honoured and tests never write into the checkout;
* the update notice is interactive-only: piped/JSON/--no-interaction runs skip
  the check entirely, so agents and scripts never pay its latency;
* every failure mode is non-fatal: a version check must never break a command.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from zotero_cli import config as _config

CACHE_FILENAME = "version-check.json"
_CHECK_INTERVAL = 86400  # 24 hours
_PYPI_URL = "https://pypi.org/pypi/zotero-cli/json"
_TIMEOUT = 3  # seconds


def update_check_disabled() -> bool:
    """Return True when the user opted out of the PyPI update check."""
    value = os.environ.get("ZOT_NO_UPDATE_CHECK", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _cache_file() -> Path:
    """Resolve the cache file lazily so relocatable state roots are respected."""
    return _config.state_dir() / CACHE_FILENAME


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse version string into tuple for comparison."""
    return tuple(int(x) for x in v.strip().split(".") if x.isdigit())


def upgrade_command(executable: str | None = None) -> str:
    """Return the upgrade command appropriate to how this install was placed.

    Detects uv tool and pipx by inspecting sys.executable; falls back to
    a plain pip install -U (which is correct for pip/conda/system installs).
    """
    exe = (executable if executable is not None else sys.executable).replace("\\", "/")
    if "/uv/tools/" in exe:
        return "uv tool upgrade zotero-cli"
    if "/pipx/venvs/" in exe:
        return "pipx upgrade zotero-cli"
    return "pip install -U zotero-cli"


def check_for_update(current_version: str) -> str | None:
    """Check if a newer version is available on PyPI.

    Returns the latest version string if newer, or None. Uses a file-based cache
    to avoid hitting PyPI on every invocation. Disabled entirely when
    ZOT_NO_UPDATE_CHECK is set. Never raises.
    """
    if update_check_disabled():
        return None
    try:
        cache_file = _cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)

        # Check cache first
        if cache_file.exists():
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cache.get("checked_at", 0) < _CHECK_INTERVAL:
                latest = cache.get("latest_version", "")
                if latest and _parse_version(latest) > _parse_version(current_version):
                    return str(latest)
                return None

        # Fetch from PyPI
        with urlopen(_PYPI_URL, timeout=_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read())
        latest = data["info"]["version"]

        # Update cache (best effort; a read-only state dir must not break the CLI)
        try:
            cache_file.write_text(
                json.dumps({"latest_version": latest, "checked_at": time.time()}),
                encoding="utf-8",
            )
        except OSError:
            pass

        if _parse_version(latest) > _parse_version(current_version):
            return str(latest)
        return None
    except Exception:
        return None
