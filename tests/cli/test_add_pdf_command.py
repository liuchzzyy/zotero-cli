"""从 PDF 添加条目的命令测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from tests.support import SAMPLE_DATA_DIR

from zotero_cli.ai_pdf import extract_doi_from_markdown
from zotero_cli.cli import main
from zotero_cli.config import PdfConfig
from zotero_cli.zotero.access.errors import ZoteroWriteError


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
            "ZOT_DATA_DIR": str(SAMPLE_DATA_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with (
            patch("zotero_cli.cli.commands.zotero.items.resolve_doi", return_value={"title": "T"}),
            patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as mock_writer_cls,
        ):
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer
            mock_writer.add_item.return_value = "NEW001"
            mock_writer.upload_attachment.return_value = "ATT001"
            result = runner.invoke(main, ["zotero", "add", "--pdf", str(pdf), "--doi", "10.1234/test"], env=env)

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
            "ZOT_DATA_DIR": str(SAMPLE_DATA_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with (
            patch("zotero_cli.config.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.ai_pdf.MinerUParseCache") as cache_cls,
        ):
            cache = cache_cls.return_value
            cache.get.return_value = None
            cache.ensure.return_value.markdown = "No DOI here"
            result = runner.invoke(main, ["zotero", "add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 3
        env_data = json.loads(result.output)
        assert env_data["error"]["code"] == "validation_error"

    def test_add_pdf_uses_mineru_markdown(self, tmp_path):
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        runner = CliRunner()
        env = {
            "ZOT_DATA_DIR": str(SAMPLE_DATA_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        with (
            patch("zotero_cli.config.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
            patch("zotero_cli.ai_pdf.MinerUParseCache") as cache_cls,
            patch("zotero_cli.cli.commands.zotero.items.resolve_doi", return_value={"title": "T"}),
            patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as mock_writer_cls,
        ):
            cache = cache_cls.return_value
            cache.get.return_value = None
            cache.ensure.return_value.markdown = "DOI 10.1234/test"
            mock_writer = MagicMock()
            mock_writer_cls.return_value = mock_writer
            mock_writer.add_item.return_value = "NEW001"
            mock_writer.upload_attachment.return_value = "ATT001"
            result = runner.invoke(main, ["zotero", "add", "--pdf", str(pdf)], env=env)

        assert result.exit_code == 0
        cache.ensure.assert_called_once_with(pdf)


def test_add_pdf_upload_failure_returns_partial_state(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with (
        patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter") as cls,
        patch("zotero_cli.cli.commands.zotero.items.resolve_doi", return_value={"title": "T"}),
    ):
        cls.return_value.add_item.return_value = "NEW001"
        cls.return_value.upload_attachment.side_effect = ZoteroWriteError("upload failed")
        result = CliRunner().invoke(
            main,
            ["--json", "zotero", "add", "--pdf", str(pdf), "--doi", "10.1234/test"],
            env={"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"},
        )
    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] == "partial"
    assert payload["data"]["key"] == "NEW001"
    assert payload["data"]["attachment_error"] == "upload failed"
    # next 是官方定义的顶层提示列表，绝不出现在 data 之内。
    assert "zot zotero attach NEW001" in payload["next"][0]
    assert "next" not in payload["data"]
    assert "error" not in payload
