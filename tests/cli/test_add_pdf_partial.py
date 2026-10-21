import json
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def test_add_pdf_upload_failure_returns_partial_state(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with (
        patch("zotero_cli.cli.zotero.add.ZoteroWriter") as cls,
        patch("zotero_cli.cli.zotero.add.resolve_doi", return_value={"title": "T"}),
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
    # next is the official top-level hint list, never inside data.
    assert "zot zotero attach NEW001" in payload["next"][0]
    assert "next" not in payload["data"]
    assert "error" not in payload
