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
    from zotero_cli.core import version_check
    from zotero_cli.core.zotero import idempotency
    from zotero_cli.workspace import store as workspace

    for name in tuple(os.environ):
        if name.startswith("ZOT_"):
            monkeypatch.delenv(name)
    isolated_config = tmp_path / ".zot" / "config.toml"
    monkeypatch.setattr(config.paths, "config_file_path", lambda start=None: isolated_config)
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setattr(workspace, "workspace_root", lambda: tmp_path / ".workspace")
    monkeypatch.setattr(workspace, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setattr(version_check, "_cache_file", lambda: tmp_path / ".zot" / "state" / "version-check.json")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag standalone batch-helper tests collected from tool/ as `tools`."""
    for item in items:
        if "tool" in Path(str(item.path)).parts:
            item.add_marker(pytest.mark.tools)
