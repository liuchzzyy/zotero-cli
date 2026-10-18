"""Repository-wide safety boundaries, including standalone tool tests."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import NoReturn

import pytest


@pytest.fixture(autouse=True)
def block_live_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require every service test to replace its network boundary explicitly."""

    def reject_connection(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("Live network access disabled in tests; mock the service boundary")

    monkeypatch.setattr(socket.socket, "connect", reject_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_connection)


@pytest.fixture(autouse=True)
def isolate_runtime_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep default config, retry caches and workspaces away from real research state."""
    from zotero_cli import config
    from zotero_cli.core import workspace_store as workspace
    from zotero_cli.core.zotero import idempotency

    for name in tuple(os.environ):
        if name.startswith("ZOT_"):
            monkeypatch.delenv(name)
    isolated_config = tmp_path / ".zot" / "config.toml"
    monkeypatch.setattr(config.paths, "config_file_path", lambda start=None: isolated_config)
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setattr(workspace, "project_root", lambda: tmp_path)
    monkeypatch.setattr(workspace, "state_dir", lambda *_args: tmp_path / ".zot" / "state")
