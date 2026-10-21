"""幂等键标识的是同一文库内的同一次请求。"""

import json
from unittest.mock import patch

from tests.support import invoke_agent_cli

from zotero_cli.zotero import idempotency
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def test_update_same_key_different_payload_or_library_is_not_cached(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.update.ZoteroWriter") as writer,
    ):
        idempotency.clear()
        common = ["zotero", "update", "ABC", "--idempotency-key", "same"]
        env = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}
        a = invoke_agent_cli(common + ["--title", "first"], env=env)
        b = invoke_agent_cli(common + ["--title", "second"], env=env)
        c = invoke_agent_cli(common + ["--title", "first"], env={**env, "ZOT_LIBRARY_ID": "456"})
        d = invoke_agent_cli(common + ["--title", "first"], env=env)
    assert all(x.exit_code == 0 for x in (a, b, c, d))
    assert writer.return_value.update_item.call_count == 3
    assert json.loads(b.output)["data"]["fields"]["title"] == "second"
    assert json.loads(c.output)["meta"]["request_id"] != json.loads(a.output)["meta"]["request_id"]
    assert json.loads(d.output)["meta"]["request_id"] == json.loads(a.output)["meta"]["request_id"]


# --- 每个幂等写入都必须在落笔前预留 ---

ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}


def test_update_unknown_outcome_blocks_retry_with_conflict(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.update.ZoteroWriter") as writer,
    ):
        writer.return_value.update_item.side_effect = ZoteroWriteError(
            "network lost after submit", code="network_error", retryable=True
        )
        first = invoke_agent_cli(["zotero", "update", "ABC", "--title", "x", "--idempotency-key", "k"], env=ENV)
        retry = invoke_agent_cli(["zotero", "update", "ABC", "--title", "x", "--idempotency-key", "k"], env=ENV)
    assert first.exit_code == 5
    assert json.loads(first.output)["error"]["retryable"] is False
    assert retry.exit_code == 6
    assert json.loads(retry.output)["error"]["code"] == "conflict"
    assert writer.return_value.update_item.call_count == 1


def test_update_definite_rejection_releases_the_reservation(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.update.ZoteroWriter") as writer,
    ):
        writer.return_value.update_item.side_effect = ZoteroWriteError(
            "Item 'ABC' not found", code="not_found", retryable=False
        )
        first = invoke_agent_cli(["zotero", "update", "ABC", "--title", "x", "--idempotency-key", "k"], env=ENV)
        retry = invoke_agent_cli(["zotero", "update", "ABC", "--title", "x", "--idempotency-key", "k"], env=ENV)
    assert first.exit_code == 4
    assert retry.exit_code == 4
    assert writer.return_value.update_item.call_count == 2


def test_add_success_is_replayed_without_a_second_write(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter") as writer,
    ):
        writer.return_value.add_item.return_value = "NEW00001"
        args = ["zotero", "add", "--doi", "10.1000/x", "--no-resolve", "--idempotency-key", "k"]
        first = invoke_agent_cli(args, env=ENV)
        retry = invoke_agent_cli(args, env=ENV)
    assert first.exit_code == 0, first.output
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.output)["data"]["key"] == "NEW00001"
    assert writer.return_value.add_item.call_count == 1


def test_add_unknown_outcome_blocks_retry_with_conflict(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.add.ZoteroWriter") as writer,
    ):
        writer.return_value.add_item.side_effect = ZoteroWriteError(
            "timeout after POST", code="network_error", retryable=True
        )
        args = ["zotero", "add", "--doi", "10.1000/x", "--no-resolve", "--idempotency-key", "k"]
        first = invoke_agent_cli(args, env=ENV)
        retry = invoke_agent_cli(args, env=ENV)
    assert first.exit_code == 5
    assert retry.exit_code == 6
    assert writer.return_value.add_item.call_count == 1


def test_attach_success_is_replayed_without_a_second_upload(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.attachments.ZoteroWriter") as writer,
    ):
        writer.return_value.upload_attachment.return_value = "ATT00001"
        args = ["zotero", "attach", "ABC", "--file", str(pdf), "--idempotency-key", "k"]
        first = invoke_agent_cli(args, env=ENV)
        retry = invoke_agent_cli(args, env=ENV)
    assert first.exit_code == 0, first.output
    assert json.loads(retry.output)["data"]["attachment_key"] == "ATT00001"
    assert json.loads(first.output)["next"] == ["zot zotero read ABC"]
    assert writer.return_value.upload_attachment.call_count == 1


def test_attach_dry_run_does_not_write_or_require_credentials(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with patch("zotero_cli.cli.commands.zotero.attachments.ZoteroWriter") as writer:
        result = invoke_agent_cli(["zotero", "attach", "ABC", "--file", str(pdf), "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["data"]["would"]["size_bytes"] == pdf.stat().st_size
    writer.assert_not_called()


def test_attach_unknown_outcome_blocks_retry_with_conflict(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.attachments.ZoteroWriter") as writer,
    ):
        writer.return_value.upload_attachment.side_effect = ZoteroWriteError(
            "Connection lost", code="network_error", retryable=True
        )
        args = ["zotero", "attach", "ABC", "--file", str(pdf), "--idempotency-key", "once"]
        first = invoke_agent_cli(args, env=ENV)
        retry = invoke_agent_cli(args, env=ENV)
    assert first.exit_code == 5, first.output
    assert json.loads(first.output)["error"]["retryable"] is False
    assert retry.exit_code == 6, retry.output
    assert json.loads(retry.output)["error"]["code"] == "conflict"
    writer.return_value.upload_attachment.assert_called_once()


def test_delete_unknown_outcome_blocks_retry_with_conflict(tmp_path):
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.delete.ZoteroWriter") as writer,
    ):
        writer.return_value.delete_item.side_effect = ZoteroWriteError(
            "connection reset", code="network_error", retryable=True
        )
        args = ["zotero", "delete", "ABC", "--yes", "--idempotency-key", "k"]
        first = invoke_agent_cli(args, env=ENV)
        retry = invoke_agent_cli(args, env=ENV)
    assert first.exit_code == 5  # network_error is a typed exit code
    assert retry.exit_code == 6
    assert json.loads(retry.output)["error"]["code"] == "conflict"
    assert writer.return_value.delete_item.call_count == 1
