"""Tests that PDF extraction does not silently switch extractors."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core.pdf_errors import PdfExtractionError

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _invoke(args):
    runner = CliRunner()
    env = {"ZOT_DATA_DIR": str(FIXTURES_DIR), "ZOT_FORMAT": "table"}
    return runner.invoke(main, args, env=env)


class TestMinerUWithoutFallback:
    def test_mineru_failure_is_reported_without_pymupdf_fallback(self):
        mock_mineru = MagicMock()
        mock_mineru.extract_text.side_effect = PdfExtractionError("MinerU failed")

        mock_pymupdf = MagicMock()

        def get_extractor_side_effect(name):
            if name == "mineru":
                return mock_mineru
            return mock_pymupdf

        with patch("zotero_cli.core.pdf_cache.PdfCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache_cls.return_value = mock_cache

            with patch("zotero_cli.commands.pdf.get_extractor", side_effect=get_extractor_side_effect):
                result = _invoke(["pdf", "ATTN001", "--extractor", "mineru"])

        assert result.exit_code == 1, result.output
        assert "MinerU failed" in result.output
        mock_mineru.extract_text.assert_called_once()
        mock_pymupdf.extract_text.assert_not_called()
        mock_cache.put.assert_not_called()

    def test_mineru_failure_with_page_range_is_not_retried_with_pymupdf(self):
        mock_mineru = MagicMock()
        mock_mineru.extract_text.side_effect = PdfExtractionError("MinerU failed")

        mock_pymupdf = MagicMock()

        def get_extractor_side_effect(name):
            if name == "mineru":
                return mock_mineru
            return mock_pymupdf

        with patch("zotero_cli.core.pdf_cache.PdfCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache_cls.return_value = mock_cache

            with patch("zotero_cli.commands.pdf.get_extractor", side_effect=get_extractor_side_effect):
                result = _invoke(["pdf", "ATTN001", "--extractor", "mineru", "--pages", "1-5"])

        assert result.exit_code == 1, result.output
        assert "MinerU failed" in result.output
        mock_mineru.extract_text.assert_called_once()
        mock_pymupdf.extract_text.assert_not_called()

    def test_pymupdf_failure_is_reported_when_pymupdf_is_explicitly_selected(self):
        mock_pymupdf = MagicMock()
        mock_pymupdf.extract_text.side_effect = PdfExtractionError("PyMuPDF failed")

        with patch("zotero_cli.core.pdf_cache.PdfCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.get.return_value = None
            mock_cache_cls.return_value = mock_cache

            with patch("zotero_cli.commands.pdf.get_extractor", return_value=mock_pymupdf):
                result = _invoke(["pdf", "ATTN001", "--extractor", "pymupdf"])

        assert result.exit_code == 1, result.output
        assert "PyMuPDF failed" in result.output
        mock_pymupdf.extract_text.assert_called_once()
