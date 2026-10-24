"""ai-note2 输入冻结与跳过语义。"""

from __future__ import annotations

import pytest

from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import collect_inputs, first_heading, is_legacy_ai_note, is_note2_output
from zotero_cli.zotero.models import Creator, Item, Note

pytestmark = pytest.mark.unit


def _item(key: str = "ITEM1") -> Item:
    return Item(
        key=key,
        item_type="journalArticle",
        title="Synthetic paper",
        creators=[Creator("A", "Author", "author")],
        abstract=None,
        date="2024",
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )


class Reader:
    def __init__(self, notes, attachments=()):
        self._notes = notes
        self._attachments = list(attachments)
        self._library_id = 1
        self.pdf_calls = 0

    def get_item(self, key):
        return _item(key)

    def get_notes(self, key):
        return self._notes

    def get_attachments(self, key):
        return list(self._attachments)

    def get_pdf_attachments(self, key):
        self.pdf_calls += 1
        return list(self._attachments)


def test_first_heading_only_accepts_leading_heading():
    assert first_heading("# AI条目分析 - T\n\nbody") == "AI条目分析 - T"
    assert first_heading("<h1>AI条目分析 - T</h1>") == "AI条目分析 - T"
    assert first_heading("正文提到 AI条目分析 - T") == ""


def test_legacy_and_note2_prefixes_are_distinct():
    assert is_legacy_ai_note("# AI条目分析 - T\nx")
    assert not is_legacy_ai_note("# 普通笔记\nAI条目分析 - T")
    assert is_note2_output("# 框架分析 - T\nx")
    assert not is_note2_output("# AI条目分析 - T\nx")


def test_no_ai_note_skips_without_touching_pdfs():
    reader = Reader([Note("N1", "ITEM1", "# 普通笔记\n没有 AI 前缀")])
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(reader, "ITEM1")
    assert error.value.hint_key == "no_ai_note"
    assert reader.pdf_calls == 0


def test_multiple_legacy_notes_require_explicit_choice():
    notes = [
        Note("N1", "ITEM1", "# AI条目分析 - A\n内容甲"),
        Note("N2", "ITEM1", "# AI条目分析 - B\n内容乙"),
    ]
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(Reader(notes), "ITEM1")
    assert error.value.code == "conflict"
    assert "N1" in str(error.value) and "N2" in str(error.value)


def test_explicit_note_must_belong_and_must_not_be_note2_output():
    notes = [Note("N1", "ITEM1", "# 框架分析 - T\n本功能输出")]
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(Reader(notes), "ITEM1", source_note_key="N1")
    assert error.value.hint_key == "source_note_invalid"
    with pytest.raises(AiNote2Error):
        collect_inputs(Reader(notes), "ITEM1", source_note_key="MISSING")


def test_note_only_item_is_rejected_before_pdf_scan():
    reader = Reader([])
    reader.get_item = lambda key: Item(
        key=key,
        item_type="note",
        title="",
        creators=[],
        abstract=None,
        date=None,
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(reader, "ITEM1")
    assert error.value.hint_key == "not_parent_item"
    assert reader.pdf_calls == 0


def test_missing_pdf_file_is_an_actionable_error(tmp_path):
    class Attachment:
        key = "PDF1"
        content_type = "application/pdf"
        path = tmp_path / "missing.pdf"
        filename = "missing.pdf"

    reader = Reader([Note("N1", "ITEM1", "# AI条目分析 - T\n内容")], [Attachment()])
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(reader, "ITEM1")
    assert error.value.hint_key == "pdf_missing"
