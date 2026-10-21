"""批量写入领域服务的行为测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.items import delete_items, restore_items
from zotero_cli.zotero.tags import update_tags


def test_update_tags_reports_each_completed_operation_in_order() -> None:
    writer = MagicMock()
    reports: list[tuple[dict[str, object], bool]] = []

    result = update_tags(
        writer,
        ["K1"],
        add_tag="added",
        remove_tag="removed",
        report=lambda row, is_error: reports.append((row, is_error)),
    )

    assert result.succeeded == [
        {"key": "K1", "tags_added": ["added"]},
        {"key": "K1", "tags_removed": ["removed"]},
    ]
    assert result.failed == []
    assert reports == [(row, False) for row in result.succeeded]
    writer.add_tags.assert_called_once_with("K1", ["added"])
    writer.remove_tags.assert_called_once_with("K1", ["removed"])


def test_restore_items_collects_typed_failure_and_reports_it() -> None:
    writer = MagicMock()
    writer.restore_from_trash.side_effect = [None, ZoteroWriteError("gone", code="not_found")]
    reports: list[tuple[dict[str, object], bool]] = []

    result = restore_items(writer, ["OK", "GONE"], report=lambda row, is_error: reports.append((row, is_error)))

    assert result.restored == ["OK"]
    assert result.failed[0]["key"] == "GONE"
    assert result.failed[0]["code"] == "not_found"
    assert reports == [({"key": "OK"}, False), (result.failed[0], True)]


def test_delete_items_marks_definite_rejection_for_idempotency() -> None:
    writer = MagicMock()
    writer._zot.request = None
    writer.delete_item.side_effect = ZoteroWriteError("gone", code="not_found")

    result = delete_items(writer, ["GONE"], idempotency_key="key")

    assert result.deleted == []
    assert result.rejected_count == 1
    assert result.failed == [
        {
            "key": "GONE",
            "code": "not_found",
            "message": "gone",
            "retryable": False,
            "committed": False,
            "definitely_rejected": True,
        }
    ]
