"""Tests for add-from-PDF feature."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core.pdf_extractor import PyMuPdfExtractor

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestExtractDoi:
    def test_extract_doi_found(self, tmp_path):
        with patch("zotero_cli.core.pdf_extractor.PyMuPdfExtractor.extract_text") as mock_extract:
            mock_extract.return_value = "Some text with DOI 10.1038/s41586-023-06139-9 in it"
            result = PyMuPdfExtractor().extract_doi(tmp_path / "dummy.pdf")
            assert result == "10.1038/s41586-023-06139-9"

    def test_extract_doi_not_found(self, tmp_path):
        with patch("zotero_cli.core.pdf_extractor.PyMuPdfExtractor.extract_text") as mock_extract:
            mock_extract.return_value = "No DOI in this text"
            result = PyMuPdfExtractor().extract_doi(tmp_path / "dummy.pdf")
            assert result is None

    def test_extract_doi_strips_trailing_punctuation(self, tmp_path):
        with patch("zotero_cli.core.pdf_extractor.PyMuPdfExtractor.extract_text") as mock_extract:
            mock_extract.return_value = "DOI: 10.1234/test.paper)."
            result = PyMuPdfExtractor().extract_doi(tmp_path / "dummy.pdf")
            assert result == "10.1234/test.paper"

    def test_extract_doi_multiple_returns_first(self, tmp_path):
        with patch("zotero_cli.core.pdf_extractor.PyMuPdfExtractor.extract_text") as mock_extract:
            mock_extract.return_value = "10.1234/first and 10.5678/second"
            result = PyMuPdfExtractor().extract_doi(tmp_path / "dummy.pdf")
            assert result == "10.1234/first"


class TestAddPdfCLI:
    def test_add_pdf_with_doi_override(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        runner = CliRunner()
        env = {
            "ZOT_DATA_DIR": str(FIXTURES_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with (
            patch("zotero_cli.commands.add.resolve_doi", return_value={"title": "T"}),
            patch("zotero_cli.commands.add.ZoteroWriter") as mock_writer_cls,
        ):
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer
            mock_writer.add_item.return_value = "NEW001"
            mock_writer.upload_attachment.return_value = "ATT001"
            result = runner.invoke(main, ["add", "--pdf", str(pdf), "--doi", "10.1234/test"], env=env)

        assert result.exit_code == 0
        mock_writer.add_item.assert_called_once_with(doi="10.1234/test", extra_fields={"title": "T"})
        data = json.loads(result.output)["data"]
        assert data["key"] == "NEW001"
        assert data["attachment_key"] == "ATT001"

    def test_add_pdf_no_doi_found(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        runner = CliRunner()
        env = {
            "ZOT_DATA_DIR": str(FIXTURES_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with patch("zotero_cli.core.pdf_extractor.get_extractor") as mock_get:
            mock_extractor = MagicMock()
            mock_extractor.extract_doi.return_value = None
            mock_get.return_value = mock_extractor
            result = runner.invoke(main, ["add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 3
        env_data = json.loads(result.output)
        assert env_data["error"]["code"] == "validation_error"

    def test_add_pdf_uses_configured_extractor(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        runner = CliRunner()
        env = {
            "ZOT_DATA_DIR": str(FIXTURES_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with (
            patch("zotero_cli.core.pdf_extractor.get_extractor") as mock_get,
            patch("zotero_cli.commands.add.ZoteroWriter") as mock_writer_cls,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_doi.return_value = "10.1234/test"
            mock_get.return_value = mock_extractor
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer
            mock_writer.add_item.return_value = "NEW001"
            mock_writer.upload_attachment.return_value = "ATT001"
            result = runner.invoke(main, ["add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 0
        mock_get.assert_called_once_with()
