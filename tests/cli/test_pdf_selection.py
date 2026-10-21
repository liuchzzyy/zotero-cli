"""Selecting a specific child PDF must not silently use the first attachment."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.zotero.models import Attachment


def test_parent_lists_pdf_attachments_without_parsing():
    first = Attachment("MAIN001", "PARENT", "paper.pdf", "application/pdf", Path("/main/paper.pdf"))
    second = Attachment("SUPP001", "PARENT", "paper.pdf", "application/pdf", Path("/supp/paper.pdf"))
    with (
        patch("zotero_cli.cli.commands.zotero.pdf.ZoteroReader") as reader_cls,
        patch("zotero_cli.ai_pdf.MinerUParseCache") as cache_cls,
    ):
        reader_cls.return_value.get_pdf_attachments.return_value = [first, second]
        result = CliRunner().invoke(main, ["--json", "zotero", "pdf", "list", "PARENT"])
    assert result.exit_code == 0, result.output
    assert [x["key"] for x in json.loads(result.output)["data"]["attachments"]] == ["MAIN001", "SUPP001"]
    cache_cls.assert_not_called()


def test_specific_child_pdf_is_selected_even_with_same_filename(tmp_path):
    first_path = tmp_path / "main" / "paper.pdf"
    second_path = tmp_path / "supp" / "paper.pdf"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_bytes(b"main")
    second_path.write_bytes(b"SI")
    first = Attachment("MAIN001", "PARENT", "paper.pdf", "application/pdf", first_path)
    second = Attachment("SUPP001", "PARENT", "paper.pdf", "application/pdf", second_path)
    with (
        patch("zotero_cli.cli.commands.ai_pdf.ZoteroReader") as reader_cls,
        patch("zotero_cli.cli.commands.ai_pdf.MinerUParseCache") as cache_cls,
    ):
        reader_cls.return_value.get_pdf_attachments.return_value = [first, second]
        parsed = cache_cls.return_value.lookup.return_value[0]
        parsed.markdown = "SI text"
        parsed.fingerprint = "fingerprint"
        parsed.model_version = "vlm"
        parsed.root = tmp_path
        cache_cls.return_value.lookup.return_value = (parsed, "pdf-fingerprint")
        result = CliRunner().invoke(main, ["--json", "ai-pdf", "PARENT", "--attachment", "SUPP001"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["markdown"] == "SI text"
    assert json.loads(result.output)["data"]["attachment_key"] == "SUPP001"
    cache_cls.return_value.lookup.assert_called_once_with(second_path)


def test_ambiguous_parent_pdf_does_not_pick_first():
    first = Attachment("MAIN001", "PARENT", "paper.pdf", "application/pdf", Path("/main/paper.pdf"))
    second = Attachment("SUPP001", "PARENT", "paper.pdf", "application/pdf", Path("/supp/paper.pdf"))
    with patch("zotero_cli.cli.commands.ai_pdf.ZoteroReader") as reader_cls:
        reader_cls.return_value.get_pdf_attachments.return_value = [first, second]
        result = CliRunner().invoke(main, ["--json", "ai-pdf", "PARENT"])
    assert result.exit_code == 3
    assert json.loads(result.output)["ok"] is False
