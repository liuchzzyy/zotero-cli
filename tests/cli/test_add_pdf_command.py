"""Tests for add-from-PDF feature."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from tests.support import FIXTURES_DIR

from zotero_cli.cli import main
from zotero_cli.config import PdfConfig
from zotero_cli.core.mineru import extract_doi_from_markdown


class TestExtractDoi:
    def test_extract_doi_found(self):
        result = extract_doi_from_markdown("Some text with DOI 10.1038/s41586-023-06139-9 in it")
        assert result == "10.1038/s41586-023-06139-9"

    def test_extract_doi_not_found(self):
        assert extract_doi_from_markdown("No DOI in this text") is None

    def test_extract_doi_strips_trailing_punctuation(self):
        assert extract_doi_from_markdown("DOI: 10.1234/test.paper).") == "10.1234/test.paper"

    def test_extract_doi_multiple_returns_first(self):
        assert extract_doi_from_markdown("10.1234/first and 10.5678/second") == "10.1234/first"


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
        with (
            patch("zotero_cli.core.mineru.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.config.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.core.mineru.MinerUParseCache") as cache_cls,
        ):
            cache = cache_cls.return_value
            cache.get.return_value = None
            cache.ensure.return_value.markdown = "No DOI here"
            result = runner.invoke(main, ["add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 3
        env_data = json.loads(result.output)
        assert env_data["error"]["code"] == "validation_error"

    def test_add_pdf_uses_mineru_markdown(self, tmp_path):
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
            patch("zotero_cli.core.mineru.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.config.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.core.mineru.MinerUParseCache") as cache_cls,
            patch("zotero_cli.commands.add.resolve_doi", return_value={"title": "T"}),
            patch("zotero_cli.commands.add.ZoteroWriter") as mock_writer_cls,
        ):
            cache = cache_cls.return_value
            cache.get.return_value = None
            cache.ensure.return_value.markdown = "DOI 10.1234/test"
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer
            mock_writer.add_item.return_value = "NEW001"
            mock_writer.upload_attachment.return_value = "ATT001"
            result = runner.invoke(main, ["add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 0
        cache.ensure.assert_called_once_with(pdf)
