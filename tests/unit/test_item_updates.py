"""条目更新领域逻辑测试：字段解析、JSONL 批量解析与带标签更新。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.items import (
    UpdateInputError,
    _load_updates_from_jsonl,
    _parse_inline_fields,
    update_item_with_tags,
)


def test_parse_inline_fields_combines_title_date_and_custom() -> None:
    fields = _parse_inline_fields(title="T", date="2026", field=("publisher=P", "volume=1"))
    assert fields == {"title": "T", "date": "2026", "publisher": "P", "volume": "1"}


def test_parse_inline_fields_rejects_missing_equal_sign() -> None:
    with pytest.raises(UpdateInputError, match="Invalid field format"):
        _parse_inline_fields(title=None, date=None, field=("invalid_field",))


def test_load_updates_from_jsonl_parses_valid_rows(tmp_path: Path) -> None:
    jsonl = tmp_path / "updates.jsonl"
    jsonl.write_text('{"key": "K1", "fields": {"title": "New"}}\n', encoding="utf-8")
    rows = _load_updates_from_jsonl(jsonl)
    assert len(rows) == 1
    assert rows[0] == {"line": 1, "key": "K1", "fields": {"title": "New"}}


def test_load_updates_from_jsonl_rejects_empty_file(tmp_path: Path) -> None:
    jsonl = tmp_path / "empty.jsonl"
    jsonl.write_text("\n\n", encoding="utf-8")
    with pytest.raises(UpdateInputError, match="empty"):
        _load_updates_from_jsonl(jsonl)


def test_update_item_with_tags_calls_both_when_tags_present() -> None:
    writer = MagicMock()
    update_item_with_tags(writer, "KEY01", {"title": "T"}, tags=["t1", "t2"])
    writer.update_item.assert_called_once_with("KEY01", {"title": "T"})
    writer.add_tags.assert_called_once_with("KEY01", ["t1", "t2"])


def test_update_item_with_tags_skips_add_tags_when_no_tags() -> None:
    writer = MagicMock()
    update_item_with_tags(writer, "KEY01", {"title": "T"}, tags=[])
    writer.update_item.assert_called_once_with("KEY01", {"title": "T"})
    writer.add_tags.assert_not_called()


def test_update_item_with_tags_does_not_tag_if_update_fails() -> None:
    writer = MagicMock()
    writer.update_item.side_effect = ZoteroWriteError("update failed")
    with pytest.raises(ZoteroWriteError, match="update failed"):
        update_item_with_tags(writer, "KEY01", {"title": "T"}, tags=["t1"])
    writer.add_tags.assert_not_called()


def test_tag_failure_after_update_preserves_committed_state() -> None:
    writer = MagicMock()
    writer.add_tags.side_effect = ZoteroWriteError("tag rejected", code="auth_invalid", committed=False)
    with pytest.raises(ZoteroWriteError) as caught:
        update_item_with_tags(writer, "KEY01", {"title": "Changed"}, tags=["done"])
    writer.update_item.assert_called_once_with("KEY01", {"title": "Changed"})
    assert caught.value.committed is True
    assert caught.value.retryable is False
    assert caught.value.code == "auth_invalid"
