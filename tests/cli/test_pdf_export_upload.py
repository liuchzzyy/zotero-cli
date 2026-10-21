"""PDF 导出和 Web API 上传命令测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tests.support import invoke_agent_cli

from zotero_cli.models import Attachment
from zotero_cli.zotero import idempotency
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def _pdf(path: Path, content: bytes = b"%PDF-1.7\ncontent") -> Path:
    path.write_bytes(content)
    return path


def _attachment(path: Path, key: str = "PDF00001") -> Attachment:
    return Attachment(key, "ATTN001", path.name, "application/pdf", path)


def test_export_copies_selected_pdf_without_changing_source(test_db_path, tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    destination = tmp_path / "exported.pdf"
    with patch("zotero_cli.cli.zotero.pdf.ZoteroReader") as reader:
        reader.return_value.get_pdf_attachments.return_value = [_attachment(source)]
        result = invoke_agent_cli(
            ["zotero", "pdf", "export", "ATTN001", str(destination)], env={"ZOT_DATA_DIR": str(test_db_path.parent)}
        )
    assert result.exit_code == 0, result.output
    assert destination.read_bytes() == source.read_bytes()
    assert json.loads(result.output)["data"]["attachment_key"] == "PDF00001"


def test_export_rejects_existing_symlink_and_multiple_selection(test_db_path, tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    destination = tmp_path / "destination.pdf"
    destination.symlink_to(source)
    attachments = [_attachment(source, "MAIN0001"), _attachment(source, "SUPP0001")]
    with patch("zotero_cli.cli.zotero.pdf.ZoteroReader") as reader:
        reader.return_value.get_pdf_attachments.return_value = attachments
        multiple = invoke_agent_cli(
            ["zotero", "pdf", "export", "ATTN001", str(tmp_path / "new.pdf")],
            env={"ZOT_DATA_DIR": str(test_db_path.parent)},
        )
        selected = invoke_agent_cli(
            ["zotero", "pdf", "export", "ATTN001", str(destination), "--attachment", "MAIN0001"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent)},
        )
    assert multiple.exit_code == 3
    assert json.loads(multiple.output)["error"]["code"] == "validation_error"
    assert selected.exit_code == 6
    assert destination.is_symlink()


def test_export_removes_partial_destination_on_copy_failure(test_db_path, tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    destination = tmp_path / "failed.pdf"
    with (
        patch("zotero_cli.cli.zotero.pdf.ZoteroReader") as reader,
        patch("zotero_cli.zotero.pdf.shutil.copyfileobj", side_effect=OSError("disk full")),
    ):
        reader.return_value.get_pdf_attachments.return_value = [_attachment(source)]
        result = invoke_agent_cli(
            ["zotero", "pdf", "export", "ATTN001", str(destination)], env={"ZOT_DATA_DIR": str(test_db_path.parent)}
        )
    assert result.exit_code == 1
    assert not destination.exists()
    assert source.read_bytes() == b"%PDF-1.7\ncontent"


def test_pdf_upload_dry_run_validates_without_web_write(tmp_path):
    pdf = _pdf(tmp_path / "paper.pdf")
    with patch("zotero_cli.cli.zotero.pdf.ZoteroWriter") as writer:
        result = invoke_agent_cli(["zotero", "pdf", "upload", "ATTN001", "--file", str(pdf), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    writer.assert_not_called()


def test_pdf_upload_replays_idempotent_result_and_blocks_unknown_retry(tmp_path):
    pdf = _pdf(tmp_path / "paper.pdf")
    env = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "secret"}
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.zotero.pdf.ZoteroWriter") as writer,
    ):
        idempotency.clear()
        writer.return_value.upload_attachment.return_value = "PDF00001"
        args = ["zotero", "pdf", "upload", "ATTN001", "--file", str(pdf), "--idempotency-key", "once"]
        first = invoke_agent_cli(args, env=env)
        second = invoke_agent_cli(args, env=env)
    assert first.exit_code == second.exit_code == 0
    assert writer.return_value.upload_attachment.call_count == 1
    assert json.loads(second.output)["data"]["attachment_key"] == "PDF00001"

    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.zotero.pdf.ZoteroWriter") as writer,
    ):
        idempotency.clear()
        writer.return_value.upload_attachment.side_effect = ZoteroWriteError(
            "connection lost", code="network_error", retryable=True
        )
        first = invoke_agent_cli(args, env=env)
        retry = invoke_agent_cli(args, env=env)
    assert first.exit_code == 5
    assert retry.exit_code == 6
    assert writer.return_value.upload_attachment.call_count == 1


def test_pdf_upload_rejects_non_pdf_without_web_write(tmp_path):
    file_path = _pdf(tmp_path / "not-a-pdf.pdf", b"text")
    with patch("zotero_cli.cli.zotero.pdf.ZoteroWriter") as writer:
        result = invoke_agent_cli(["zotero", "pdf", "upload", "ATTN001", "--file", str(file_path), "--dry-run"])
    assert result.exit_code == 3
    assert json.loads(result.output)["error"]["code"] == "validation_error"
    writer.assert_not_called()
