"""仓库级测试隔离：环境变量、配置/状态目录与真实网络访问。

本文件同时是根目录 `conftest.py` 的替代：pytest 只从 rootdir 加载 conftest，
这里位于 `tests/` 之下，因此由 `pyproject.toml` 的 `testpaths = ["tests"]`
保证所有测试都在其作用域内。
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import NoReturn

import pytest
from click.testing import CliRunner

from tests.support import FIXTURES_DIR, REPO_ROOT
from zotero_cli.config import AppConfig


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic and disconnected from the user's live services."""
    for name in tuple(os.environ):
        if name.startswith("ZOT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ZOT_DATA_DIR", str(FIXTURES_DIR))
    monkeypatch.setenv("ZOT_FORMAT", "table")
    for name in (
        "ZOT_LIBRARY_ID",
        "ZOT_API_KEY",
        "ZOT_MINERU_TOKEN",
        "ZOT_EMBEDDING_URL",
        "ZOT_EMBEDDING_KEY",
        "ZOT_EMBEDDING_MODEL",
        "ZOT_RERANK_URL",
        "ZOT_RERANK_KEY",
        "ZOT_RERANK_MODEL",
    ):
        monkeypatch.setenv(name, "")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Derive test tier markers from the directory structure."""
    for item in items:
        path = Path(str(item.path))
        if "unit" in path.parts:
            item.add_marker(pytest.mark.unit)
        elif "cli" in path.parts:
            item.add_marker(pytest.mark.cli)
        elif "integration" in path.parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def test_db_path() -> Path:
    return FIXTURES_DIR / "zotero.sqlite"


@pytest.fixture
def test_config(test_db_path: Path) -> AppConfig:
    return AppConfig(data_dir=str(test_db_path.parent))


@pytest.fixture
def test_data_dir() -> Path:
    return FIXTURES_DIR


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
    from zotero_cli.cli import version_check
    from zotero_cli.workspace import store as workspace
    from zotero_cli.zotero import idempotency

    # ZOT_* 已在 isolate_test_environment 中清空，这里只隔离落盘位置。
    isolated_config = tmp_path / ".zot" / "config.toml"
    monkeypatch.setattr(config.paths, "config_file_path", lambda start=None: isolated_config)
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setattr(workspace, "workspace_root", lambda: tmp_path / ".workspace")
    monkeypatch.setattr(workspace, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setattr(version_check, "_cache_file", lambda: tmp_path / ".zot" / "state" / "version-check.json")
