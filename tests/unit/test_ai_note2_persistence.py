"""ai-note2 发布、回读与对账恢复。"""

from __future__ import annotations

import pytest

from zotero_cli.ai_notes2 import persistence
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.zotero.access.errors import ZoteroWriteError

pytestmark = pytest.mark.unit

ITEM = "ITEM1"


class FakeWriter:
    def __init__(self, *, create_error=None, read_error=None, tag_error=None, tags=()):
        self.create_error = create_error
        self.read_error = read_error
        self.tag_error = tag_error
        self.tags = list(tags)
        self.created: list[str] = []
        self.tag_calls: list[list[str]] = []

    def add_note(self, parent_key, content):
        if self.create_error:
            raise self.create_error
        self.created.append(content)
        return "NOTE1"

    def read_item(self, key):
        if self.read_error and key == "NOTE1":
            raise self.read_error
        if key == "NOTE1":
            return {"data": {"itemType": "note", "parentItem": ITEM, "note": self.created[-1]}}
        return {"data": {"tags": [{"tag": tag} for tag in self.tags]}}

    def add_tags(self, key, tags):
        if self.tag_error:
            raise self.tag_error
        self.tag_calls.append(tags)
        self.tags.extend(tags)


def _run(monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "ledger_root", lambda: tmp_path / "ledger")
    return persistence.reserve_run("user:1:ITEM1", input_digest="a" * 64, idempotency_key=None, force=False)


def test_verified_publish_reads_back_note_and_tag(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter()
    result = persistence.publish_document(
        writer,
        scope="user:1:ITEM1",
        run_number=reservation.run_number,
        item_key=ITEM,
        html_note="ITEM1-0001-0001 内容",
    )
    assert result.verified and result.tagged
    assert writer.tag_calls == [["ai/evidence"]]
    assert len(writer.created) == 1


def test_existing_tag_is_not_duplicated(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter(tags=["ai/evidence", "keep"])
    result = persistence.publish_document(
        writer,
        scope="user:1:ITEM1",
        run_number=reservation.run_number,
        item_key=ITEM,
        html_note="ITEM1-0001-0001 内容",
    )
    assert result.verified
    assert writer.tag_calls == []
    assert writer.tags == ["ai/evidence", "keep"]


def test_rejected_creation_releases_committed_and_allows_retry(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter(create_error=ZoteroWriteError("bad", code="validation_error", committed=False))
    with pytest.raises(AiNote2Error) as error:
        persistence.publish_document(
            writer, scope="user:1:ITEM1", run_number=reservation.run_number, item_key=ITEM, html_note="x"
        )
    assert error.value.committed is False
    assert writer.created == []


def test_unknown_creation_requires_reconcile_and_blocks_new_run(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter(create_error=ZoteroWriteError("timeout", code="network_error", retryable=True, committed=True))
    with pytest.raises(AiNote2Error) as error:
        persistence.publish_document(
            writer, scope="user:1:ITEM1", run_number=reservation.run_number, item_key=ITEM, html_note="x"
        )
    assert error.value.committed is True
    with pytest.raises(AiNote2Error) as blocked:
        persistence.reserve_run("user:1:ITEM1", input_digest="a" * 64, idempotency_key=None, force=False)
    assert blocked.value.hint_key == "pending_mutation"


def test_note_created_but_tag_failure_keeps_key_and_never_recreates(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter(tag_error=ZoteroWriteError("boom", code="network_error", committed=True))
    with pytest.raises(AiNote2Error) as error:
        persistence.publish_document(
            writer,
            scope="user:1:ITEM1",
            run_number=reservation.run_number,
            item_key=ITEM,
            html_note="ITEM1-0001-0001 内容",
        )
    assert error.value.committed is True
    assert error.value.hint_key == "tag_unknown"
    assert len(writer.created) == 1
    latest = persistence.latest_run("user:1:ITEM1")
    assert latest["note_key"] == "NOTE1"
    assert latest["status"] == persistence.STATUS_RECONCILE


def test_readback_mismatch_is_not_treated_as_success(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter()
    with pytest.raises(AiNote2Error) as error:
        persistence.publish_document(
            writer,
            scope="user:1:ITEM1",
            run_number=reservation.run_number,
            item_key=ITEM,
            html_note="没有运行标识",
        )
    assert error.value.committed is True
    assert error.value.hint_key == "publish_mismatch"


def test_verified_run_requires_force_for_new_run(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    persistence.update_run(
        "user:1:ITEM1",
        reservation.run_number,
        status=persistence.STATUS_VERIFIED,
        committed=True,
        data={"note_key": "NOTE1"},
    )
    with pytest.raises(AiNote2Error) as blocked:
        persistence.reserve_run("user:1:ITEM1", input_digest="a" * 64, idempotency_key=None, force=False)
    assert blocked.value.hint_key == "pending_mutation"
    forced = persistence.reserve_run("user:1:ITEM1", input_digest="a" * 64, idempotency_key=None, force=True)
    assert forced.run_number == 2


def test_reconcile_only_adds_missing_tag_without_creating_note(monkeypatch, tmp_path):
    reservation = _run(monkeypatch, tmp_path)
    writer = FakeWriter()
    writer.add_note(ITEM, "ITEM1-0001-0001 内容")
    persistence.update_run(
        "user:1:ITEM1",
        reservation.run_number,
        status=persistence.STATUS_RECONCILE,
        note_key="NOTE1",
        committed=True,
    )
    result = persistence.publish_document(
        writer,
        scope="user:1:ITEM1",
        run_number=reservation.run_number,
        item_key=ITEM,
        html_note="",
        reconciled_note_key="NOTE1",
    )
    assert result.verified
    assert len(writer.created) == 1
    assert writer.tag_calls == [["ai/evidence"]]


def test_idempotency_conflict_on_different_input(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path)
    persistence.reserve_run("user:1:ITEM1", input_digest="a" * 64, idempotency_key="K1", force=True)
    with pytest.raises(AiNote2Error) as error:
        persistence.reserve_run("user:1:ITEM1", input_digest="b" * 64, idempotency_key="K1", force=True)
    assert error.value.hint_key == "idempotency_conflict"
