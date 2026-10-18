"""Regression tests for bounded and resumable collection analysis."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import batch_analyze as batch
import pytest

from zotero_cli.core.notes.errors import NoteAnalysisError


@pytest.fixture
def batch_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    """Replace service boundaries while exercising real checkpoints and control flow."""
    reader = Mock()
    reader.get_collections.return_value = [SimpleNamespace(key="COL1", name="Inbox", children=[])]
    reader.get_collection_items.return_value = [SimpleNamespace(key="ITEM1", title="Paper")]
    reader.get_item.return_value = SimpleNamespace(tags=[])
    reader.get_pdf_attachments.return_value = [SimpleNamespace(key="PDF1")]
    analyze = Mock(return_value={"status": "ok", "note_key": "NOTE1"})
    monkeypatch.setattr(batch, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(batch, "load_ai_note_config", lambda: SimpleNamespace())
    monkeypatch.setattr(batch, "get_data_dir", lambda _config: tmp_path)
    monkeypatch.setattr(batch, "get_prefs_js_path", lambda _config: None)
    monkeypatch.setattr(batch, "resolve_library_id", lambda *_args: 1)
    monkeypatch.setattr(batch, "resolve_write_credentials", lambda _config: ("1", "fake"))
    monkeypatch.setattr(batch, "ZoteroReader", lambda *_args, **_kwargs: reader)
    monkeypatch.setattr(batch, "ZoteroWriter", Mock())
    monkeypatch.setattr(batch, "AiClient", Mock())
    monkeypatch.setattr(batch, "analyze_item", analyze)
    return SimpleNamespace(reader=reader, analyze=analyze, state=tmp_path / "state.json")


def test_limit_counts_failed_attempts(batch_runtime: SimpleNamespace) -> None:
    batch_runtime.reader.get_collection_items.return_value = [
        SimpleNamespace(key=f"ITEM{i}", title=f"Paper {i}") for i in range(3)
    ]
    batch_runtime.analyze.side_effect = NoteAnalysisError("provider unavailable")

    batch.run_batch("Inbox", limit=1, state_path=batch_runtime.state)

    assert batch_runtime.analyze.call_count == 1
    assert len(batch.load_state(batch_runtime.state)) == 1
    batch_runtime.reader.close.assert_called_once()


def test_uncertain_checkpoint_is_not_reanalyzed(batch_runtime: SimpleNamespace) -> None:
    batch.save_state(batch_runtime.state, {"ITEM1": {"status": "uncertain"}})

    batch.run_batch("Inbox", state_path=batch_runtime.state)

    batch_runtime.analyze.assert_not_called()
    assert batch.load_state(batch_runtime.state)["ITEM1"]["status"] == "uncertain"


def test_force_preview_preserves_success_checkpoint(batch_runtime: SimpleNamespace) -> None:
    batch.save_state(batch_runtime.state, {"ITEM1": {"status": "ok", "note_key": "NOTE1"}})
    original = batch_runtime.state.read_bytes()
    batch_runtime.analyze.return_value = {"status": "dry_run"}

    batch.run_batch("Inbox", force=True, dry_run=True, state_path=batch_runtime.state)

    batch_runtime.analyze.assert_called_once()
    assert batch_runtime.state.read_bytes() == original


def test_reader_closes_when_ai_setup_fails(batch_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch, "AiClient", Mock(side_effect=RuntimeError("invalid AI config")))

    with pytest.raises(RuntimeError, match="invalid AI config"):
        batch.run_batch("Inbox", state_path=batch_runtime.state)

    batch_runtime.reader.close.assert_called_once()


def test_invalid_checkpoint_is_not_silently_discarded(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text("{unfinished", encoding="utf-8")

    with pytest.raises(ValueError, match="checkpoint"):
        batch.load_state(state)

    assert state.read_text(encoding="utf-8") == "{unfinished"


def test_checkpoint_requires_item_mapping(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps([{"status": "ok"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="checkpoint"):
        batch.load_state(state)


def test_dry_run_does_not_create_a_checkpoint(batch_runtime: SimpleNamespace) -> None:
    assert not batch_runtime.state.exists()

    summary = batch.run_batch("Inbox", dry_run=True, state_path=batch_runtime.state)

    assert summary["dry_run"] is True
    assert not batch_runtime.state.exists()
    batch_runtime.reader.close.assert_called_once()


def test_save_state_temp_name_is_unique_per_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"
    seen: list[str] = []
    real_replace = Path.replace

    def capture(self: Path, target: Path) -> Path:
        seen.append(self.name)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", capture)
    batch.save_state(state, {"ITEM1": {"status": "ok"}})

    assert seen and seen[0].startswith("state.json.")
    assert seen[0].endswith(".tmp")
    assert str(os.getpid()) in seen[0]
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_state_removes_temp_when_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"

    def boom(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        batch.save_state(state, {"ITEM1": {"status": "ok"}})

    assert list(tmp_path.glob("*.tmp")) == []
    assert not state.exists()


def test_collection_argument_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(batch, "run_batch", run)
    monkeypatch.setattr(sys, "argv", ["batch_analyze.py", "--dry-run"])

    with pytest.raises(SystemExit) as error:
        batch.main()

    assert error.value.code == 2
    run.assert_not_called()


def test_apply_and_preview_cannot_be_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr(batch, "run_batch", run)
    monkeypatch.setattr(sys, "argv", ["batch_analyze.py", "--collection", "Inbox", "--apply", "--dry-run"])

    with pytest.raises(SystemExit) as error:
        batch.main()

    assert error.value.code == 2
    run.assert_not_called()


def test_collection_name_must_be_unambiguous() -> None:
    reader = Mock()
    reader.get_collections.return_value = [
        SimpleNamespace(key="COL1", name="Inbox", children=[]),
        SimpleNamespace(key="COL2", name="Inbox", children=[]),
    ]

    with pytest.raises(ValueError, match="ambiguous"):
        batch.resolve_collection_key(reader, "Inbox")

    assert batch.resolve_collection_key(reader, "COL2") == "COL2"


def test_preview_uncertain_does_not_skip_the_first_apply(batch_runtime: SimpleNamespace) -> None:
    batch_runtime.analyze.return_value = {"status": "uncertain"}
    batch.run_batch("Inbox", dry_run=True, state_path=batch_runtime.state)

    summary = batch.run_batch("Inbox", state_path=batch_runtime.state)

    assert summary["attempted"] == 1
    assert batch_runtime.analyze.call_count == 2
