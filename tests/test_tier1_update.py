"""Tests for the update command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core.writer import ZoteroWriteError, ZoteroWriter

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _invoke(args: list[str], json_output: bool = False):
    runner = CliRunner()
    base = ["--json"] if json_output else []
    env = {
        "ZOT_DATA_DIR": str(FIXTURES_DIR),
        "ZOT_LIBRARY_ID": "test_lib",
        "ZOT_API_KEY": "test_key",
        "ZOT_FORMAT": "table",
    }
    return runner.invoke(main, base + args, env=env)


def _parse_json_output(output: str) -> dict:
    cleaned = "\n".join(line for line in output.splitlines() if not line.lstrip().startswith('{"event"'))
    return json.loads(cleaned)


class TestUpdateCommand:
    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_title(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        result = _invoke(["update", "ATTN001", "--title", "New Title"])
        assert result.exit_code == 0
        mock_writer.update_item.assert_called_once_with("ATTN001", {"title": "New Title"})
        assert "Updated" in result.output

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_date(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        result = _invoke(["update", "ATTN001", "--date", "2025-01-01"])
        assert result.exit_code == 0
        mock_writer.update_item.assert_called_once_with("ATTN001", {"date": "2025-01-01"})

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_multiple_fields(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        result = _invoke(
            [
                "update",
                "ATTN001",
                "--title",
                "New Title",
                "--date",
                "2025-01-01",
                "--field",
                "abstractNote=New abstract",
            ]
        )
        assert result.exit_code == 0
        mock_writer.update_item.assert_called_once_with(
            "ATTN001",
            {"title": "New Title", "date": "2025-01-01", "abstractNote": "New abstract"},
        )

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_field_option(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        result = _invoke(["update", "ATTN001", "--field", "volume=42"])
        assert result.exit_code == 0
        mock_writer.update_item.assert_called_once_with("ATTN001", {"volume": "42"})

    def test_update_no_fields(self):
        result = _invoke(["update", "ATTN001"])
        assert result.exit_code != 0
        assert "No fields" in result.output

    def test_update_no_credentials(self):
        runner = CliRunner()
        env = {"ZOT_DATA_DIR": str(FIXTURES_DIR), "ZOT_LIBRARY_ID": "", "ZOT_API_KEY": "", "ZOT_FORMAT": "table"}
        result = runner.invoke(main, ["update", "ATTN001", "--title", "X"], env=env)
        assert result.exit_code != 0
        assert "credentials" in result.output.lower() or "config" in result.output.lower()

    def test_update_invalid_field_format(self):
        result = _invoke(["update", "ATTN001", "--field", "no_equals_sign"])
        assert result.exit_code != 0
        assert "Invalid" in result.output or "key=value" in result.output

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_json_output(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        result = _invoke(["update", "ATTN001", "--title", "New", "--add-tag", "update/metadata"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["key"] == "ATTN001"
        assert "fields" in data
        assert data["tags_added"] == ["update/metadata"]
        assert data["sync_required"] is True

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_api_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.update_item.side_effect = ZoteroWriteError("Item 'X' not found")
        result = _invoke(["update", "X", "--title", "Y"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_update_schema_includes_from_jsonl(self):
        result = _invoke(["schema", "update"], json_output=True)
        assert result.exit_code == 0
        env = json.loads(result.output)
        params = {p["name"]: p for p in env["data"]["params"]}
        assert "from_jsonl" in params
        assert "--from-jsonl" in params["from_jsonl"]["flags"]
        assert "add_tags" in params
        assert "--add-tag" in params["add_tags"]["flags"]

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_from_jsonl_dry_run(self, mock_writer_cls, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text(
            json.dumps({"key": "ATTN001", "fields": {"title": "New Title", "abstractNote": "Clean abstract"}}),
            encoding="utf-8",
        )
        result = _invoke(
            ["update", "--from-jsonl", str(path), "--add-tag", "update/metadata", "--dry-run"], json_output=True
        )
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["dry_run"] is True
        assert env["data"]["would"]["count"] == 1
        assert env["data"]["would"]["updates"][0]["key"] == "ATTN001"
        assert env["data"]["would"]["tags_to_add"] == ["update/metadata"]
        mock_writer_cls.assert_not_called()

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_from_jsonl_success(self, mock_writer_cls, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"key": "ATTN001", "fields": {"title": "New Title"}}),
                    json.dumps({"key": "BERT002", "fields": {"abstractNote": "Clean abstract"}}),
                ]
            ),
            encoding="utf-8",
        )
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        result = _invoke(["update", "--from-jsonl", str(path), "--add-tag", "update/metadata"], json_output=True)
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] is True
        assert len(env["data"]["succeeded"]) == 2
        assert env["data"]["failed"] == []
        assert env["data"]["tags_added"] == ["update/metadata"]
        assert mock_writer.update_item.call_count == 2
        assert mock_writer.add_tags.call_count == 2
        assert mock_writer.update_item.call_args_list[0].args == ("ATTN001", {"title": "New Title"})
        assert mock_writer.update_item.call_args_list[1].args == ("BERT002", {"abstractNote": "Clean abstract"})
        assert mock_writer.add_tags.call_args_list[0].args == ("ATTN001", ["update/metadata"])
        assert mock_writer.add_tags.call_args_list[1].args == ("BERT002", ["update/metadata"])

    @patch("zotero_cli.commands.update.ZoteroWriter")
    def test_update_from_jsonl_partial_failure(self, mock_writer_cls, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"key": "ATTN001", "fields": {"title": "New Title"}}),
                    json.dumps({"key": "BERT002", "fields": {"title": "Will fail"}}),
                ]
            ),
            encoding="utf-8",
        )
        mock_writer = MagicMock()
        mock_writer.update_item.side_effect = [None, ZoteroWriteError("Network error", code="network_error", retryable=True)]
        mock_writer_cls.return_value = mock_writer

        result = _invoke(["update", "--from-jsonl", str(path), "--add-tag", "update/metadata"], json_output=True)
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] == "partial"
        assert len(env["data"]["succeeded"]) == 1
        assert len(env["data"]["failed"]) == 1
        assert env["data"]["failed"][0]["key"] == "BERT002"
        assert env["data"]["failed"][0]["error"]["code"] == "network_error"
        assert mock_writer.add_tags.call_count == 1

    def test_update_from_jsonl_invalid_json(self, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text('{"key":"ATTN001","fields":{"title":"ok"}}\nnot-json\n', encoding="utf-8")
        result = _invoke(["update", "--from-jsonl", str(path)], json_output=True)
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "line 2" in env["error"]["message"]

    def test_update_from_jsonl_rejects_mixed_inline_fields(self, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text(json.dumps({"key": "ATTN001", "fields": {"title": "New Title"}}), encoding="utf-8")
        result = _invoke(["update", "--from-jsonl", str(path), "--title", "X"], json_output=True)
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "--from-jsonl" in env["error"]["message"]

    def test_update_from_jsonl_rejects_item_key_argument(self, tmp_path):
        path = tmp_path / "updates.jsonl"
        path.write_text(json.dumps({"key": "ATTN001", "fields": {"title": "New Title"}}), encoding="utf-8")
        result = _invoke(["update", "ATTN001", "--from-jsonl", str(path)], json_output=True)
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "ITEMKEY" in env["error"]["message"]


class TestWriterUpdateItem:
    @patch("zotero_cli.core.writer.zotero.Zotero")
    def test_update_item_calls_api(self, mock_zotero_cls):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        mock_zot.client = None
        mock_zot.item.return_value = {
            "data": {"key": "ABC", "title": "Old", "version": 1},
            "version": 1,
        }
        writer = ZoteroWriter(library_id="test", api_key="key")
        writer.update_item("ABC", {"title": "New Title"})
        mock_zot.update_item.assert_called_once()
        call_arg = mock_zot.update_item.call_args[0][0]
        assert call_arg["data"]["title"] == "New Title"

    @patch("zotero_cli.core.writer.zotero.Zotero")
    def test_update_item_not_found(self, mock_zotero_cls):
        from pyzotero.zotero_errors import ResourceNotFoundError

        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        mock_zot.client = None
        mock_zot.item.side_effect = ResourceNotFoundError
        writer = ZoteroWriter(library_id="test", api_key="key")
        with pytest.raises(ZoteroWriteError, match="not found"):
            writer.update_item("MISSING", {"title": "X"})
