"""验证 ai-note2 集合批处理的续跑和结果分类。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zotero_cli.ai_notes2 import batch

COLLECTIONS = [
    {"key": "PARENT", "name": "40 | 项目集合", "children": [{"key": "COL1", "name": "400 | 灵感思考", "children": []}]}
]
ITEMS = [
    {"key": "ART1", "item_type": "journalArticle", "title": "Paper one"},
    {"key": "ART2", "item_type": "journalArticle", "title": "Paper two"},
    {"key": "NOTE1", "item_type": "note", "title": "A note"},
]


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    calls: list[str] = []
    replies: dict[str, tuple[int, dict] | Exception] = {}

    def fake_zot(args: list[str], timeout: float | None = None) -> tuple[int, dict, str]:
        if args[:3] == ["zotero", "collection", "list"]:
            return 0, {"ok": True, "data": COLLECTIONS}, ""
        if args[:3] == ["zotero", "collection", "items"]:
            return 0, {"ok": True, "data": ITEMS}, ""
        key = args[1]
        calls.append(key)
        reply = replies.get(key, (0, {"ok": True, "data": {"status": "skipped"}}))
        if isinstance(reply, Exception):
            raise reply
        return reply[0], reply[1], ""

    monkeypatch.setattr(batch, "zot_json", fake_zot)
    return SimpleNamespace(calls=calls, replies=replies, out=tmp_path / "out")


def report(runtime: SimpleNamespace) -> dict:
    return json.loads((runtime.out / "latest.json").read_text("utf-8"))


def state(runtime: SimpleNamespace) -> dict:
    return json.loads((runtime.out / "state.json").read_text("utf-8"))["items"]


def test_only_journal_articles_are_queued(runtime: SimpleNamespace) -> None:
    assert batch.main(["--collection", "400 | 灵感思考", "--out-dir", str(runtime.out)]) == 0
    assert runtime.calls == []


def test_dry_run_is_not_treated_as_finished(runtime: SimpleNamespace) -> None:
    runtime.replies["ART1"] = (0, {"ok": True, "data": {"status": "dry_run"}})
    argv = ["--collection", "COL1", "--dry-run", "--limit", "1", "--out-dir", str(runtime.out)]

    batch.main(argv)
    batch.main(argv)

    assert runtime.calls == ["ART1", "ART1"]
    assert report(runtime)["counts"]["dry_run"] == 1
    assert report(runtime)["counts"]["deferred"] == 1


def test_failure_does_not_stop_later_items(runtime: SimpleNamespace) -> None:
    runtime.replies["ART1"] = (1, {"ok": False, "error": {"code": "generation_invalid", "message": "bad"}})
    runtime.replies["ART2"] = (0, {"ok": True, "data": {"status": "ok", "note_key": "N2", "run_number": 1}})

    assert batch.main(["--collection", "COL1", "--apply", "--out-dir", str(runtime.out)]) == 1

    assert report(runtime)["counts"] == {
        "ok": 1,
        "dry_run": 0,
        "skipped": 0,
        "failed": 1,
        "timeout": 0,
        "deferred": 0,
        "retry_pending": 0,
    }
    assert report(runtime)["items"]["ART2"]["note_key"] == "N2"
    assert (runtime.out / "runs" / "ART1.json").exists()


def test_success_is_not_rerun(runtime: SimpleNamespace) -> None:
    """已完成的条目不重跑；缺少旧笔记的条目仍需复查。"""
    runtime.replies["ART1"] = (0, {"ok": True, "data": {"status": "ok", "note_key": "N1"}})
    argv = ["--collection", "COL1", "--apply", "--out-dir", str(runtime.out)]

    batch.main(argv)
    batch.main(argv)

    assert runtime.calls == ["ART1", "ART2", "ART2"]
    assert report(runtime)["counts"]["ok"] == 1
    assert report(runtime)["counts"]["skipped"] == 1


def test_timeout_parks_item_for_reconcile(runtime: SimpleNamespace) -> None:
    runtime.replies["ART1"] = subprocess.TimeoutExpired("zot", 1)

    assert batch.main(["--collection", "COL1", "--apply", "--out-dir", str(runtime.out)]) == 1

    assert state(runtime)["ART1"]["status"] == "timeout"
    assert state(runtime)["ART2"]["status"] == "skipped"
