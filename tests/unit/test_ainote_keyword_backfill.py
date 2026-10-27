"""集合运行中的关键词回填：缺 ai/keywords 的已完成条目只补关键词，不重跑分析。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from zotero_cli.ai_notes.errors import NoteAnalysisError

TOOL_DIR = Path(__file__).resolve().parents[2] / "tool" / "ainote"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOL_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _batch(tmp_path, monkeypatch, *, tags, analyze):
    module = _load("batch_analyze")
    reader = MagicMock()
    reader.get_collections.return_value = [MagicMock(key="COLL0001", name="collection", children=[])]
    reader.get_collection_items.return_value = [MagicMock(key="ITEM0001", title="paper")]
    reader.get_item.return_value = MagicMock(tags=tags)
    reader.get_pdf_attachments.return_value = [MagicMock()]
    for name, value in (
        ("load_config", lambda: MagicMock()),
        ("load_ai_note_config", lambda: MagicMock()),
        ("get_data_dir", lambda cfg: tmp_path),
        ("get_prefs_js_path", lambda cfg: None),
        ("resolve_library_id", lambda db, cfg: "1"),
        ("resolve_write_credentials", lambda cfg: ("1", "key")),
        ("ZoteroReader", lambda *a, **k: reader),
        ("ZoteroWriter", lambda *a, **k: MagicMock()),
        ("AiClient", lambda cfg: MagicMock()),
    ):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "analyze_item", analyze)
    state_path = tmp_path / "state.json"
    summary = module.run_batch("COLL0001", state_path=state_path)
    return summary, json.loads(state_path.read_text()) if state_path.exists() else {}


def test_missing_keywords_is_backfilled_without_full_analysis(tmp_path, monkeypatch):
    calls = []

    def analyze(**kwargs):
        calls.append(kwargs)
        return {"status": "short_note_only", "short_note": "ok"}

    summary, state = _batch(tmp_path, monkeypatch, tags=["ai/noted"], analyze=analyze)
    assert [c.get("short_note_only") for c in calls] == [True]
    assert summary["keyword_backfilled"] == 1
    assert summary["notes_created"] == 0
    assert state["ITEM0001"]["status"] == "keyword_backfilled"


def test_complete_item_with_keywords_is_skipped(tmp_path, monkeypatch):
    calls = []

    def analyze(**kwargs):
        calls.append(kwargs)
        return {"status": "ok"}

    summary, _ = _batch(tmp_path, monkeypatch, tags=["ai/noted", "ai/keywords"], analyze=analyze)
    assert calls == []
    assert summary["skipped"] == 1


def test_no_keywords_marks_blocked_after_attempt_limit(tmp_path, monkeypatch):
    calls = []

    def analyze(**kwargs):
        calls.append(kwargs)
        raise NoteAnalysisError("关键词生成失败", code="runtime_error")

    summary, state = _batch(tmp_path, monkeypatch, tags=["ai/noted", "ai/no_keywords"], analyze=analyze)
    assert summary["keyword_failed"] == 1
    assert state["ITEM0001"]["keyword_attempts"] == 1

    # 第二次尝试仍失败：达到上限，记为 keyword_blocked
    summary2, state2 = _batch(tmp_path, monkeypatch, tags=["ai/noted", "ai/no_keywords"], analyze=analyze)
    assert len(calls) == 2
    assert summary2["keyword_failed"] == 1
    assert state2["ITEM0001"]["status"] == "keyword_blocked"
    assert state2["ITEM0001"]["keyword_attempts"] == 2

    # 第三次运行不再调用模型，直接报告 blocked
    summary3, _ = _batch(tmp_path, monkeypatch, tags=["ai/noted", "ai/no_keywords"], analyze=analyze)
    assert len(calls) == 2
    assert summary3["keyword_blocked"] == 1


def test_scheduler_backfills_keywords_before_tagged_skip(tmp_path):
    _load("batch_analyze")  # scheduled_analyze 以脚本目录方式导入同目录模块
    module = _load("scheduled_analyze")
    item_data = {
        "key": "ITEM0001",
        "itemType": "journalArticle",
        "collections": ["COLL0001"],
        "tags": [{"tag": "ai/noted"}],
        "extra": "translated-title: 标题",
        "title": "paper",
        "DOI": "",
    }
    calls = []

    class Api:
        def collection_items(self, collection, limit=100):
            return [{"key": "ITEM0001", "data": item_data}]

        def everything(self, result):
            return list(result)

        def item(self, key):
            return {"data": item_data}

        def children(self, key, limit=100):
            return []

    def analyze(key, *, short_note_only=False):
        calls.append(short_note_only)
        assert short_note_only is True
        item_data["tags"] = [{"tag": "ai/noted"}, {"tag": "ai/keywords"}]
        item_data["extra"] = "translated-title: 标题\nshort-note: A | B | C | 疑问：D"
        return {"status": "short_note_only", "short_note": "ok"}

    class Reader:
        def get_collection_items(self, collection):
            return [SimpleNamespace(key="ITEM0001", item_type="journalArticle")]

    state_path = tmp_path / "sched.json"
    summary = module.run_collection(Reader(), Api(), analyze, "COLL0001", state_path, apply=True, budget_seconds=60)
    assert calls == [True]
    assert [entry["key"] for entry in summary["keyword_backfilled"]] == ["ITEM0001"]
    assert json.loads(state_path.read_text())["ITEM0001"]["status"] == "keyword_backfilled"
    assert summary["skipped"] == []
