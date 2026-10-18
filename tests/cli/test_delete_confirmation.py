"""H-05: destructive delete requires explicit confirmation."""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.support import invoke_cli as _run

ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}


def test_delete_without_yes_or_dry_run_requires_confirmation() -> None:
    with patch("zotero_cli.commands.items.delete.ZoteroWriter") as writer:
        result = _run(["--json", "delete", "ABC123"], env=ENV)
    assert result.exit_code == 3
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "confirmation_required"
    writer.assert_not_called()


def test_no_interaction_never_implies_yes_for_delete() -> None:
    with patch("zotero_cli.commands.items.delete.ZoteroWriter") as writer:
        result = _run(["--json", "--no-interaction", "delete", "ABC123"], env=ENV)
    assert result.exit_code == 3
    env = json.loads(result.output)
    assert env["error"]["code"] == "confirmation_required"
    writer.assert_not_called()


def test_delete_dry_run_does_not_require_yes() -> None:
    with patch("zotero_cli.commands.items.delete.ZoteroWriter") as writer:
        result = _run(["--json", "delete", "ABC123", "--dry-run"], env=ENV)
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["dry_run"] is True
    assert env["data"]["would_delete"] == ["ABC123"]
    writer.assert_not_called()


def test_delete_with_yes_executes() -> None:
    with patch("zotero_cli.commands.items.delete.ZoteroWriter") as writer:
        result = _run(["--json", "delete", "ABC123", "--yes"], env=ENV)
    assert result.exit_code == 0, result.output
    writer.return_value.delete_item.assert_called_once_with("ABC123")
