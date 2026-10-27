"""Offline contracts for the scheduled collection guard; never call live services."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import scheduled_analyze as sa

from zotero_cli.ai_notes.errors import NoteAnalysisError


def test_empty_collection_never_calls_analysis(tmp_path: Path) -> None:
    reader, api, analyze = MagicMock(), MagicMock(), MagicMock()
    reader.get_collection_items.return_value = []
    api.everything.return_value = []
    result = sa.run_collection(reader, api, analyze, "COL", tmp_path / "state.json", apply=True)
    assert result["total"] == 0
    assert result["processed"] == 0
    analyze.assert_not_called()
    assert not (tmp_path / "state.json").exists()


def test_completed_tags_are_checked_remotely_before_pdf_or_analysis(tmp_path: Path) -> None:
    reader, api, analyze = MagicMock(), MagicMock(), MagicMock()
    reader.get_collection_items.return_value = [SimpleNamespace(key="ITEM", item_type="journalArticle", title="Paper")]
    api.everything.return_value = []
    for tags in (("ai/noted", "ai/keywords"), ("ai/uncertain",)):
        api.item.return_value = {
            "data": {
                "key": "ITEM",
                "itemType": "journalArticle",
                "collections": ["COL"],
                "tags": [{"tag": tag} for tag in tags],
            }
        }
        result = sa.run_collection(reader, api, analyze, "COL", tmp_path / "state.json", apply=True)
        assert result["total"] == 1
        assert result["skipped"] == [{"key": "ITEM", "reason": "tagged", "tags": [tags[0]]}]
    reader.get_pdf_attachments.assert_not_called()
    analyze.assert_not_called()


def test_unmarked_item_is_analyzed_once_and_verified_before_checkpoint(tmp_path: Path) -> None:
    reader, api = MagicMock(), MagicMock()
    reader.get_collection_items.return_value = [SimpleNamespace(key="ITEM", item_type="journalArticle", title="Paper")]
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"offline fixture, never parsed")
    reader.get_pdf_attachments.return_value = [SimpleNamespace(path=pdf)]
    before = {
        "key": "ITEM",
        "itemType": "journalArticle",
        "title": "Paper",
        "DOI": "10.1/test",
        "collections": ["COL"],
        "tags": [],
        "extra": "translated-title: 标题",
    }
    after = {
        **before,
        "tags": [{"tag": "ai/noted"}, {"tag": "ai/keywords"}],
        "extra": before["extra"] + "\nshort-note: 测试 | 疑问：对照不足",
    }
    api.item.side_effect = [{"data": before}, {"data": after}]
    note = {
        "key": "NOTE",
        "data": {
            "key": "NOTE",
            "parentItem": "ITEM",
            "itemType": "note",
            "note": "<h1>AI条目分析 - Paper</h1><h2>证据</h2><p>有效内容</p><table><tr><td>值</td></tr></table>",
        },
    }
    api.everything.side_effect = [[], [], [note]]
    analyze = MagicMock(return_value={"status": "ok", "note_key": "NOTE", "short_note": "ok"})
    state_path = tmp_path / "state.json"
    result = sa.run_collection(reader, api, analyze, "COL", state_path, apply=True)
    assert result["processed"] == 1 and not result["failed"]
    analyze.assert_called_once_with("ITEM")
    assert sa.load_state(state_path)["ITEM"]["verified"] is True


def test_generated_note_with_failed_keywords_is_verified_as_degraded(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    before = api.item.return_value["data"]
    after = {**before, "tags": [{"tag": "ai/noted"}, {"tag": "ai/no_keywords"}]}
    api.item.side_effect = [{"data": before}, {"data": after}]
    note = {
        "key": "NOTE",
        "data": {
            "key": "NOTE",
            "parentItem": "ITEM",
            "itemType": "note",
            "note": "<h1>AI条目分析 - Paper</h1><h2>证据</h2><p>有效内容</p>",
        },
    }
    api.everything.side_effect = [[], [], [note]]
    analyze.return_value = {
        "status": "ok",
        "note_key": "NOTE",
        "short_note": "failed",
        "short_note_error": "model timeout",
    }

    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)

    assert result["failed"] == []
    assert result["processed"] == 1
    assert result["degraded"] == [{"key": "ITEM", "note_key": "NOTE", "short_note_error": "model timeout"}]
    assert sa.load_state(path)["ITEM"]["verified"] is True
    assert sa.load_state(path)["ITEM"]["short_note"] == "failed"


def test_written_short_note_without_keyword_tag_is_reported_as_degraded(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    before = api.item.return_value["data"]
    after = {**before, "tags": [{"tag": "ai/noted"}], "extra": "short-note: 测试 | 疑问：对照不足"}
    api.item.side_effect = [{"data": before}, {"data": after}]
    note = {
        "key": "NOTE",
        "data": {
            "key": "NOTE",
            "parentItem": "ITEM",
            "itemType": "note",
            "note": "<h1>AI条目分析 - Paper</h1><h2>证据</h2><p>有效内容</p>",
        },
    }
    api.everything.side_effect = [[], [], [note]]
    analyze.return_value = {
        "status": "ok",
        "note_key": "NOTE",
        "short_note": "failed",
        "short_note_error": "ai/keywords tag write failed",
    }

    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)

    assert result["failed"] == []
    assert result["degraded"][0]["key"] == "ITEM"
    assert sa.load_state(path)["ITEM"]["verified"] is True


def fixture_item(tmp_path: Path) -> tuple[MagicMock, MagicMock, MagicMock, Path]:
    reader, api, analyze = MagicMock(), MagicMock(), MagicMock()
    reader.get_collection_items.return_value = [SimpleNamespace(key="ITEM", item_type="journalArticle", title="Paper")]
    api.item.return_value = {
        "data": {"key": "ITEM", "itemType": "journalArticle", "title": "Paper", "collections": ["COL"], "tags": []}
    }
    api.everything.return_value = []
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"offline test fixture")
    reader.get_pdf_attachments.return_value = [SimpleNamespace(path=pdf)]
    return reader, api, analyze, tmp_path / "state.json"


def test_existing_ai_note_without_tag_does_not_duplicate(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    api.everything.side_effect = [
        [],
        [{"key": "NOTE", "data": {"itemType": "note", "note": "<h1>AI条目分析 - Paper</h1>"}}],
    ]
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["skipped"][0]["reason"] == "existing_ai_note"
    analyze.assert_not_called()


def test_dry_run_only_inventories_and_never_parses_or_writes(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=False)
    assert result["candidates"] == ["ITEM"]
    assert not path.exists()
    analyze.assert_not_called()


def test_missing_pdf_skips_without_poisoning_checkpoint(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    reader.get_pdf_attachments.return_value = [SimpleNamespace(path=tmp_path / "missing.pdf")]
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["skipped"][0]["reason"] == "no_local_pdf"
    assert not path.exists()
    analyze.assert_not_called()


def test_unknown_write_outcome_is_not_retried(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = RuntimeError("write result unknown")
    first = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    second = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert first["failed"] and not second["failed"]
    assert second["blocked"] == [{"key": "ITEM", "error": "prior_write_outcome_uncertain; reconcile before retry"}]
    assert sa.load_state(path)["ITEM"]["committed"] is True
    analyze.assert_called_once_with("ITEM")


def test_remote_error_is_failure_not_empty_collection(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    api.item.side_effect = RuntimeError("offline")
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["total"] == 1 and result["failed"]
    analyze.assert_not_called()


def test_per_item_failure_does_not_fail_completed_batch() -> None:
    assert sa.batch_exit_code({"failed": [{"key": "BAD", "error": "bad PDF"}], "processed": 1}) == 0


def test_one_bad_item_does_not_stop_later_items(tmp_path: Path) -> None:
    reader, api, analyze = MagicMock(), MagicMock(), MagicMock()
    reader.get_collection_items.return_value = [
        SimpleNamespace(key=key, item_type="journalArticle", title=key) for key in ("BAD", "GOOD")
    ]
    api.everything.return_value = []
    api.item.side_effect = [
        {"data": {"collections": ["COL"], "tags": []}},
        {"data": {"collections": ["COL"], "tags": []}},
        {"data": {"collections": ["COL"], "tags": [{"tag": "ai/uncertain"}]}},
    ]
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"offline fixture")
    reader.get_pdf_attachments.return_value = [SimpleNamespace(path=pdf)]
    analyze.side_effect = [RuntimeError("bad item"), {"status": "uncertain"}]

    result = sa.run_collection(reader, api, analyze, "COL", tmp_path / "state.json", apply=True)

    assert [row["key"] for row in result["failed"]] == ["BAD"]
    assert result["uncertain"] == 1
    assert analyze.call_count == 2


def test_previous_failed_item_is_deferred_behind_fresh_items(tmp_path: Path) -> None:
    reader, api, analyze = MagicMock(), MagicMock(), MagicMock()
    reader.get_collection_items.return_value = [
        SimpleNamespace(key=key, item_type="journalArticle", title=key) for key in ("AAA", "BBB")
    ]
    api.everything.return_value = []
    api.item.return_value = {"data": {"collections": ["COL"], "tags": []}}
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"offline fixture")
    reader.get_pdf_attachments.return_value = [SimpleNamespace(path=pdf)]
    path = tmp_path / "state.json"
    sa.save_state(path, {"AAA": {"status": "failed", "attempts": 1}})
    analyze.side_effect = NoteAnalysisError("bad data", code="validation_error")

    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)

    assert result["candidates"] == ["BBB", "AAA"]
    assert analyze.call_count == 2


def test_removed_member_does_not_get_a_note(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    api.item.return_value["data"]["collections"] = ["OTHER"]
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["skipped"][0]["reason"] == "not_live_member"
    analyze.assert_not_called()


def test_remote_only_member_waits_for_local_sync(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    reader.get_collection_items.return_value = []
    api.everything.side_effect = [[{"key": "ITEM", "data": {"itemType": "journalArticle"}}], []]
    result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["skipped"][0]["reason"] == "not_synced_locally"
    analyze.assert_not_called()


def test_bad_readback_keeps_in_progress_to_prevent_duplicate_note(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.return_value = {"status": "ok", "note_key": "NOT_FOUND"}
    first = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    second = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert first["failed"] and not second["failed"]
    assert second["blocked"] == [{"key": "ITEM", "error": "prior_write_outcome_uncertain; reconcile before retry"}]
    assert sa.load_state(path)["ITEM"]["status"] == "in_progress"
    analyze.assert_called_once_with("ITEM")


def test_repeated_deterministic_failure_is_parked_and_stops_failing_runs(tmp_path: Path) -> None:
    # The new pipeline fails closed for input it cannot fit (structured text over
    # max_extracted_chars, or more main figures than the token budget allows).
    # Retrying that nightly would fail every run forever.
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = NoteAnalysisError(
        "required main images exceed model budget", code="validation_error", parkable=True
    )
    runs = [sa.run_collection(reader, api, analyze, "COL", path, apply=True) for _ in range(sa.MAX_ATTEMPTS)]
    assert all(run["failed"] for run in runs[:-1])
    assert runs[-1]["failed"] == [] and runs[-1]["blocked"][0]["attempts"] == sa.MAX_ATTEMPTS
    assert sa.load_state(path)["ITEM"]["status"] == sa.PARKED_STATUS

    settled = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert settled["failed"] == [] and settled["blocked"] == []
    assert settled["skipped"][0]["reason"] == f"checkpoint_{sa.PARKED_STATUS}"
    assert analyze.call_count == sa.MAX_ATTEMPTS


def test_parked_item_is_reattempted_only_when_asked(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = NoteAnalysisError("structured text too long", code="validation_error", parkable=True)
    for _ in range(sa.MAX_ATTEMPTS):
        sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert sa.load_state(path)["ITEM"]["status"] == sa.PARKED_STATUS

    analyze.side_effect = None
    analyze.return_value = {"status": "already_analyzed"}
    sa.run_collection(reader, api, analyze, "COL", path, apply=True, retry_parked=True)
    assert analyze.call_count == sa.MAX_ATTEMPTS + 1


def test_committed_failure_is_never_parked(tmp_path: Path) -> None:
    # A write whose outcome is unknown must stay retry-blocked, never become
    # "unanalyzable": the note may already exist on the server.
    reader, api, analyze, path = fixture_item(tmp_path)
    error = NoteAnalysisError("write result unknown", code="runtime_error", committed=True)
    analyze.side_effect = error
    for _ in range(sa.MAX_ATTEMPTS + 1):
        sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    entry = sa.load_state(path)["ITEM"]
    assert entry["status"] == "failed" and entry["committed"] is True
    assert analyze.call_count == 1


def test_transient_noncommitted_failure_is_not_parked(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = NoteAnalysisError("provider timeout", code="network_error", retryable=True)
    for _ in range(sa.MAX_ATTEMPTS + 1):
        result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
        assert result["failed"] and not result["blocked"]
    assert sa.load_state(path)["ITEM"]["status"] == "failed"
    assert analyze.call_count == sa.MAX_ATTEMPTS + 1


def test_non_input_validation_failure_is_not_parked(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = NoteAnalysisError("model request rejected", code="validation_error")
    for _ in range(sa.MAX_ATTEMPTS):
        result = sa.run_collection(reader, api, analyze, "COL", path, apply=True)
    assert result["failed"] and not result["blocked"]


def test_transient_failures_do_not_count_toward_input_parking(tmp_path: Path) -> None:
    reader, api, analyze, path = fixture_item(tmp_path)
    analyze.side_effect = [
        NoteAnalysisError("timeout", code="network_error", retryable=True),
        NoteAnalysisError("too many images", code="validation_error", parkable=True),
        NoteAnalysisError("too many images", code="validation_error", parkable=True),
        NoteAnalysisError("too many images", code="validation_error", parkable=True),
    ]
    runs = [sa.run_collection(reader, api, analyze, "COL", path, apply=True) for _ in range(4)]
    assert all(run["failed"] for run in runs[:3])
    assert runs[-1]["blocked"]
    assert sa.load_state(path)["ITEM"]["budget_attempts"] == sa.MAX_ATTEMPTS
