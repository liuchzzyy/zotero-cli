"""基础 PDF 附件命令不依赖 MinerU，也不会修改用户文件。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.zotero.models import Attachment
from zotero_cli.zotero.pdf import select_pdf_attachment


def _invoke(args: list[str], test_db_path: Path):
    return CliRunner().invoke(
        main,
        ["--json", "zotero", "pdf", *args],
        env={"ZOT_DATA_DIR": str(test_db_path.parent)},
    )


def test_list_pdf_attachments_without_mineru(test_db_path):
    with patch("zotero_cli.ai_pdf.MinerUParseCache") as mineru:
        result = _invoke(["list", "ATTN001"], test_db_path)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["attachments"]
    mineru.assert_not_called()


def test_missing_item_returns_not_found(test_db_path):
    result = _invoke(["path", "MISSING"], test_db_path)
    assert result.exit_code == 4
    assert json.loads(result.output)["error"]["code"] == "not_found"


def test_ambiguous_pdf_selection_requires_key():
    attachments = [
        Attachment("MAIN0001", "PARENT", "main.pdf", "application/pdf"),
        Attachment("SUPP0001", "PARENT", "supp.pdf", "application/pdf"),
    ]
    try:
        select_pdf_attachment(attachments)
    except ValueError as error:
        assert "--attachment" in str(error)
    else:
        raise AssertionError("多个 PDF 必须显式选择")
    assert select_pdf_attachment(attachments, "SUPP0001") is attachments[1]


def test_path_requires_explicit_attachment_for_multiple_pdfs(test_db_path):
    attachments = [
        Attachment("MAIN0001", "ATTN001", "main.pdf", "application/pdf", Path("/missing/main.pdf")),
        Attachment("SUPP0001", "ATTN001", "supp.pdf", "application/pdf", Path("/missing/supp.pdf")),
    ]
    with patch("zotero_cli.cli.commands.zotero.pdf.ZoteroReader") as reader_type:
        reader_type.return_value.get_pdf_attachments.return_value = attachments
        result = _invoke(["path", "ATTN001"], test_db_path)
    assert result.exit_code == 3
    assert json.loads(result.output)["error"]["code"] == "validation_error"


def test_path_reports_missing_file(test_db_path):
    with patch("zotero_cli.cli.commands.zotero.pdf.ZoteroReader") as reader_type:
        reader_type.return_value.get_pdf_attachments.return_value = [
            Attachment("MAIN0001", "ATTN001", "main.pdf", "application/pdf", Path("/missing/main.pdf"))
        ]
        result = _invoke(["path", "ATTN001"], test_db_path)
    assert result.exit_code == 4
    assert json.loads(result.output)["error"]["code"] == "not_found"


def test_path_reports_selected_pdf(test_db_path, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-test")
    with patch("zotero_cli.cli.commands.zotero.pdf.ZoteroReader") as reader_type:
        reader_type.return_value.get_pdf_attachments.return_value = [
            Attachment("MAIN0001", "ATTN001", "paper.pdf", "application/pdf", pdf_path)
        ]
        result = _invoke(["path", "ATTN001"], test_db_path)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["path"] == str(pdf_path)
