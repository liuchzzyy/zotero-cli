from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.support import FIXTURES_DIR, REPO_ROOT
from zotero_cli.config import AppConfig


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic and disconnected from the user's live services."""
    monkeypatch.setenv("ZOT_DATA_DIR", str(FIXTURES_DIR))
    monkeypatch.setenv("ZOT_FORMAT", "table")
    for name in (
        "ZOT_LIBRARY_ID",
        "ZOT_API_KEY",
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
