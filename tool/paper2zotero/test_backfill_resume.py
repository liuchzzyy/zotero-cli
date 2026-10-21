"""Behavioral regressions for staged title translation and safe resume."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import backfill_titles as backfill
import pytest


def test_partial_write_resumes_remaining_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    items = {
        "A": {"key": "A", "data": {"title": "Paper A", "extra": ""}},
        "B": {"key": "B", "data": {"title": "Paper B", "extra": ""}},
    }
    api = Mock()
    api.top.side_effect = lambda **_kwargs: list(items.values())
    api.everything.side_effect = lambda result: result
    api.item.side_effect = lambda key: items[key]
    attempts: list[str] = []

    def update(key: str, fields: dict[str, str]) -> None:
        attempts.append(key)
        if key == "B" and attempts.count("B") == 1:
            raise backfill.BackfillError("temporary API failure")
        items[key]["data"].update(fields)

    writer = Mock()
    writer.update_item.side_effect = update
    translate = Mock(side_effect=lambda rows: {row["key"]: f"中文标题 {row['key']}" for row in rows})
    monkeypatch.setattr(backfill, "translate_batch", translate)
    state_path = tmp_path / "state.json"

    with pytest.raises(backfill.BackfillError, match="temporary API failure"):
        backfill.run_backfill(api, writer, state_path)
    first_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert first_state[0]["status"] == "updated"

    result = backfill.run_backfill(api, writer, state_path)

    assert result["updated"] == 1
    assert attempts == ["A", "B", "B"]
    assert translate.call_count == 1
    assert all(row["status"] == "updated" for row in json.loads(state_path.read_text(encoding="utf-8")))


def test_staging_checkpoints_each_completed_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [{"key": "A", "title": "Paper A"}, {"key": "B", "title": "Paper B"}]
    state = tmp_path / "state.json"
    translate = Mock(side_effect=[{"A": "中文标题甲"}, backfill.BackfillError("model unavailable")])
    monkeypatch.setattr(backfill, "translate_batch", translate)

    with pytest.raises(backfill.BackfillError, match="model unavailable"):
        backfill.stage_translations(rows, state, batch_size=1)

    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved[0]["title_zh"] == "中文标题甲"
    translate.side_effect = [{"B": "中文标题乙"}]
    completed = backfill.stage_translations(rows, state, batch_size=1)
    assert [row["title_zh"] for row in completed] == ["中文标题甲", "中文标题乙"]
    assert translate.call_count == 3


def test_ambiguous_collection_name_is_rejected() -> None:
    api = Mock()
    api.collection.side_effect = ValueError("not a key")
    api.collections.return_value = [
        {"key": "COL1", "data": {"name": "Inbox"}},
        {"key": "COL2", "data": {"name": "Inbox"}},
    ]
    api.everything.side_effect = lambda result: result

    with pytest.raises(backfill.BackfillError, match="多个|ambiguous"):
        backfill.resolve_collection_items(api, "Inbox")

    api.collection_items.assert_not_called()


def test_stale_completed_status_does_not_skip_a_currently_missing_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps([{"key": "A", "title": "Paper A", "doi": "", "title_zh": "中文标题甲", "status": "updated"}]),
        encoding="utf-8",
    )
    item = {"key": "A", "data": {"title": "Paper A", "extra": ""}}
    api = Mock()
    api.top.return_value = [item]
    api.everything.side_effect = lambda result: result
    api.item.side_effect = lambda _key: item
    writer = Mock()
    writer.update_item.side_effect = lambda _key, fields: item["data"].update(fields)
    translate = Mock()
    monkeypatch.setattr(backfill, "translate_batch", translate)

    result = backfill.run_backfill(api, writer, state_path)

    assert result["updated"] == 1
    writer.update_item.assert_called_once_with("A", {"extra": "translated-title: 中文标题甲"})
    translate.assert_not_called()


def test_title_backfill_does_not_overwrite_a_concurrent_extra_edit() -> None:
    from zotero_cli.zotero.access.errors import ZoteroWriteError
    from zotero_cli.zotero.access.writer import ZoteroWriter

    original = {"key": "A", "version": 17, "data": {"title": "Paper A", "extra": "funder: keep"}}
    edited = {"key": "A", "version": 18, "data": {"title": "Paper A", "extra": "funder: keep\nuser: new"}}
    api = Mock()
    api.item.side_effect = [original, edited, original]
    writer = object.__new__(ZoteroWriter)
    writer._zot = api

    with pytest.raises(ZoteroWriteError) as error:
        backfill.update_one(api, writer, {"key": "A", "title": "Paper A"}, "中文标题甲")

    assert error.value.code == "conflict"
    api.update_item.assert_not_called()
