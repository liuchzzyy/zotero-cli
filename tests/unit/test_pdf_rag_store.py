"""工作区领域层测试：名称校验、条目模型与落盘读写。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zotero_cli.pdf_rag.store import (
    PdfRag,
    PdfRagItem,
    delete_pdf_rag,
    list_pdf_rags,
    load_pdf_rag,
    mineru_cache_dir,
    pdf_rag_exists,
    save_pdf_rag,
    validate_name,
)


class TestValidateName:
    def test_valid_names(self):
        assert validate_name("llm-safety") is True
        assert validate_name("protein-folding") is True
        assert validate_name("topic1") is True
        assert validate_name("a") is True
        assert validate_name("my-long-pdf_rag-name") is True

    def test_invalid_names(self):
        assert validate_name("") is False
        assert validate_name("LLM-Safety") is False
        assert validate_name("has spaces") is False
        assert validate_name("under_score") is True
        assert validate_name("00_收件箱") is True
        assert validate_name("-leading") is False
        assert validate_name("trailing-") is False
        assert validate_name("double--dash") is False
        assert validate_name("has.dot") is False
        assert validate_name("trailing-newline\n") is False
        assert validate_name("mid\nline") is False


class TestPdfRagModel:
    def test_has_item(self):
        ws = PdfRag(
            name="test",
            created="2026-01-01",
            items=[
                PdfRagItem(key="ABC", title="Paper A", added="2026-01-01"),
            ],
        )
        assert ws.has_item("ABC") is True
        assert ws.has_item("XYZ") is False

    def test_add_item(self):
        ws = PdfRag(name="test", created="2026-01-01")
        assert ws.add_item("ABC", "Paper A") is True
        assert len(ws.items) == 1
        assert ws.items[0].key == "ABC"
        assert ws.items[0].title == "Paper A"

    def test_add_item_duplicate(self):
        ws = PdfRag(
            name="test",
            created="2026-01-01",
            items=[
                PdfRagItem(key="ABC", title="Paper A", added="2026-01-01"),
            ],
        )
        assert ws.add_item("ABC", "Paper A") is False
        assert len(ws.items) == 1

    def test_remove_item(self):
        ws = PdfRag(
            name="test",
            created="2026-01-01",
            items=[
                PdfRagItem(key="ABC", title="Paper A", added="2026-01-01"),
                PdfRagItem(key="DEF", title="Paper B", added="2026-01-01"),
            ],
        )
        assert ws.remove_item("ABC") is True
        assert len(ws.items) == 1
        assert ws.items[0].key == "DEF"

    def test_remove_item_not_found(self):
        ws = PdfRag(name="test", created="2026-01-01")
        assert ws.remove_item("XYZ") is False


class TestPdfRagIO:
    def test_mineru_cache_dir_uses_repo_state_dir(self, tmp_path):
        state_root = tmp_path / ".zot" / "state"
        with patch("zotero_cli.pdf_rag.store.state_dir", return_value=state_root):
            assert mineru_cache_dir() == state_root / "mineru"

    def test_save_and_load(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            ws = PdfRag(
                name="test-ws",
                created="2026-03-29T10:00:00+00:00",
                description="Test pdf_rag",
                items=[
                    PdfRagItem(key="ABC123", title="Paper A", added="2026-03-29T10:05:00+00:00"),
                    PdfRagItem(key="DEF456", title="Paper B", added="2026-03-29T10:06:00+00:00"),
                ],
            )
            save_pdf_rag(ws)
            loaded = load_pdf_rag("test-ws")

        assert loaded.name == "test-ws"
        assert loaded.created == "2026-03-29T10:00:00+00:00"
        assert loaded.description == "Test pdf_rag"
        assert len(loaded.items) == 2
        assert loaded.items[0].key == "ABC123"
        assert loaded.items[0].title == "Paper A"
        assert loaded.items[1].key == "DEF456"

    def test_load_nonexistent(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                load_pdf_rag("nonexistent")

    def test_list_pdf_rags(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            save_pdf_rag(PdfRag(name="ws-a", created="2026-01-01"))
            save_pdf_rag(PdfRag(name="ws-b", created="2026-01-02"))
            result = list_pdf_rags()
        assert len(result) == 2
        assert result[0].name == "ws-a"
        assert result[1].name == "ws-b"

    def test_list_empty(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = list_pdf_rags()
        assert result == []

    def test_delete_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            save_pdf_rag(PdfRag(name="doomed", created="2026-01-01"))
            assert pdf_rag_exists("doomed")
            delete_pdf_rag("doomed")
            assert not pdf_rag_exists("doomed")

    def test_delete_nonexistent(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                delete_pdf_rag("nonexistent")

    def test_save_with_special_chars_in_title(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            ws = PdfRag(
                name="test-special",
                created="2026-01-01",
                items=[
                    PdfRagItem(key="X1", title='Paper with "quotes" and \\backslash', added="2026-01-01"),
                ],
            )
            save_pdf_rag(ws)
            loaded = load_pdf_rag("test-special")
        assert loaded.items[0].title == 'Paper with "quotes" and \\backslash'
