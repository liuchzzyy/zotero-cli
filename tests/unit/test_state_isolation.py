"""Protect user state from the test harness itself."""

from __future__ import annotations

import os
from pathlib import Path

from zotero_cli import config
from zotero_cli.core.zotero.idempotency import _db_path
from zotero_cli.workspace.store import workspaces_dir


def test_default_test_paths_are_not_the_checkout_state() -> None:
    checkout = Path(__file__).resolve().parents[2]
    assert config.paths.config_file_path() != checkout / ".zot" / "config.toml"
    assert checkout not in _db_path().parents
    assert checkout not in workspaces_dir().parents
    assert config.project_root() == checkout


def test_all_inherited_ai_settings_are_cleared() -> None:
    assert "ZOT_AI_API_KEY" not in os.environ
    assert "ZOT_AI_BASE_URL" not in os.environ
