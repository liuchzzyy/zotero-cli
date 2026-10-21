"""增量索引必须同时跟踪笔记与元数据的变化，而不只是 PDF 文件。"""

from dataclasses import replace
from unittest.mock import patch

from tests.support import invoke_cli, isolate_pdf_rag_backend

from zotero_cli.pdf_rag.index import RagIndex
from zotero_cli.zotero.access.reader import ZoteroReader


def test_metadata_change_reindexes_without_force(tmp_path):
    original = ZoteroReader.get_item
    title = ["Original title"]

    def changed_item(reader, key):
        item = original(reader, key)
        return replace(item, title=title[0]) if item else None

    with isolate_pdf_rag_backend(tmp_path), patch.object(ZoteroReader, "get_item", changed_item):
        invoke_cli(["pdf-rag", "new", "metadata-regression"])
        invoke_cli(["pdf-rag", "add", "metadata-regression", "ATTN001"])
        first = invoke_cli(["pdf-rag", "index", "metadata-regression", "--no-embed"])
        title[0] = "Changed title"
        second = invoke_cli(["pdf-rag", "index", "metadata-regression", "--no-embed"])
    assert first.exit_code == second.exit_code == 0
    assert "Indexed" in second.output
    idx = RagIndex(tmp_path / "metadata-regression" / "pdf_rag.sqlite")
    try:
        text = "\n".join(c["content"] for c in idx.get_all_chunks())
        assert "Changed title" in text
        assert "Original title" not in text
    finally:
        idx.close()


def test_note_change_reindexes_without_force(tmp_path):
    notes = [("NOTE1", "old marker")]
    with (
        isolate_pdf_rag_backend(tmp_path),
        patch("zotero_cli.cli.commands.pdf_rag.indexing._collect_note_refs", side_effect=lambda *_: notes),
    ):
        invoke_cli(["pdf-rag", "new", "notes-regression"])
        invoke_cli(["pdf-rag", "add", "notes-regression", "ATTN001"])
        first = invoke_cli(["pdf-rag", "index", "notes-regression", "--no-embed"])
        notes[:] = [("NOTE1", "new marker")]
        second = invoke_cli(["pdf-rag", "index", "notes-regression", "--no-embed"])
    assert first.exit_code == second.exit_code == 0
    assert "Indexed" in second.output
    idx = RagIndex(tmp_path / "notes-regression" / "pdf_rag.sqlite")
    try:
        text = "\n".join(c["content"] for c in idx.get_all_chunks())
        assert "new marker" in text
        assert "old marker" not in text
    finally:
        idx.close()
