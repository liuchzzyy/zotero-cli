"""破坏性删除必须获得显式确认。"""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.support import invoke_cli as _run

ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}


def test_delete_without_yes_or_dry_run_requires_confirmation() -> None:
    with patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer:
        result = _run(["--json", "zotero", "delete", "ABC123"], env=ENV)
    assert result.exit_code == 3
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "confirmation_required"
    writer.assert_not_called()


def test_no_interaction_never_implies_yes_for_delete() -> None:
    with patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer:
        result = _run(["--json", "--no-interaction", "zotero", "delete", "ABC123"], env=ENV)
    assert result.exit_code == 3
    env = json.loads(result.output)
    assert env["error"]["code"] == "confirmation_required"
    writer.assert_not_called()


def test_delete_dry_run_does_not_require_yes() -> None:
    with patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer:
        result = _run(["--json", "zotero", "delete", "ABC123", "--dry-run"], env=ENV)
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["dry_run"] is True
    assert env["data"]["would_delete"] == ["ABC123"]
    writer.assert_not_called()


def test_delete_with_yes_executes() -> None:
    with patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer:
        result = _run(["--json", "zotero", "delete", "ABC123", "--yes"], env=ENV)
    assert result.exit_code == 0, result.output
    writer.return_value.delete_item.assert_called_once_with("ABC123")
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["data"]["permanent"] is True
    assert payload["data"]["permanently_deleted"] == ["ABC123"]
    # 永久删除不得宣称可用回收站/恢复流程。
    assert "next" not in payload
    assert "trash" not in result.output


def test_delete_help_says_permanent_and_never_advertises_trash_empty() -> None:
    result = _run(["zotero", "delete", "--help"])
    assert result.exit_code == 0
    text = result.output.lower()
    assert "永久" in text
    assert "trash empty" not in text
    assert "move to trash" not in text


def test_delete_dry_run_payload_is_explicitly_permanent() -> None:
    result = _run(["--json", "zotero", "delete", "ABC123", "--dry-run"], env=ENV)
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["data"]["permanent"] is True
    assert payload["data"]["would_delete"] == ["ABC123"]


def test_delete_partial_failure_is_typed_partial_envelope() -> None:
    from zotero_cli.zotero.access.errors import ZoteroWriteError

    with patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer:
        writer.return_value.delete_item.side_effect = [None, ZoteroWriteError("gone", code="not_found")]
        result = _run(["--json", "zotero", "delete", "K1", "K2", "--yes"], env=ENV)
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["ok"] == "partial"
    assert payload["data"]["permanent"] is True
    assert payload["data"]["permanently_deleted"] == ["K1"]
    assert payload["data"]["failed"][0]["key"] == "K2"
    assert payload["data"]["failed"][0]["code"] == "not_found"


def test_delete_definite_rejection_releases_idempotency_reservation(tmp_path) -> None:
    from unittest.mock import patch as patch_obj

    from zotero_cli.zotero.access.errors import ZoteroWriteError

    with (
        patch_obj("zotero_cli.zotero.access.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as writer,
    ):
        writer.return_value.delete_item.side_effect = ZoteroWriteError("gone", code="not_found")
        args = ["--json", "zotero", "delete", "ABC", "--yes", "--idempotency-key", "k"]
        first = _run(args, env=ENV)
        second = _run(args, env=ENV)
    assert first.exit_code == 4
    assert second.exit_code == 4
    assert writer.return_value.delete_item.call_count == 2
