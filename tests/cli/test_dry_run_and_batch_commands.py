"""试运行、分页、批量操作与写入错误在命令层的表现。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from tests.support import invoke_cli as _invoke

from zotero_cli.cli import main
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.writer import SYNC_REMINDER

WRITE_ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "abc"}


def _parse_json_output(output: str) -> dict:
    cleaned = "\n".join(line for line in output.splitlines() if not line.lstrip().startswith('{"event"'))
    return json.loads(cleaned)


# --- Dry-run tests ---


class TestDryRun:
    def test_delete_dry_run_prints_text_preview(self):
        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "delete", "K1", "--dry-run"], env=WRITE_ENV)
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert "K1" in result.output

    def test_delete_dry_run_no_api_needed(self):
        """缺少 API 凭据时 `--dry-run` 仍应可用。"""
        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "delete", "K1", "--dry-run"])
        # 试运行会绕过鉴权检查，因此退出码为 0 并输出预览。
        assert result.exit_code == 0
        # 文本模式下的预览行，JSON envelope 模式下不出现。
        assert "[dry-run]" in result.output or "would_delete" in result.output

    def test_collection_reorganize_dry_run(self, tmp_path):
        plan = {
            "collections": [
                {"name": "Topic A", "items": ["K1", "K2"]},
                {"name": "Sub B", "parent": "Topic A", "items": ["K3"]},
            ]
        }
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--no-json", "zotero", "collection", "reorganize", str(plan_file), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "[dry-run] Would create collection 'Topic A'" in result.output
        assert "[dry-run]   Would move K1 -> 'Topic A'" in result.output
        assert "[dry-run]   Would move K2 -> 'Topic A'" in result.output
        assert "[dry-run] Would create collection 'Sub B' (under 'Topic A')" in result.output
        assert "2 collections to create" in result.output

    def test_collection_delete_dry_run(self):
        # 仅预览时使用本地快照展示结果。
        result = _invoke(["zotero", "collection", "delete", "COLML01", "--dry-run"])
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert "COLML01" in result.output

    def test_collection_delete_dry_run_json_envelope(self):
        result = _invoke(["zotero", "collection", "delete", "COLML01", "--dry-run"], json_output=True)
        assert result.exit_code == 0
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["dry_run"] is True
        assert env["data"]["key"] == "COLML01"
        assert env["data"]["would_delete"] is True

    def test_collection_delete_dry_run_previews_cascade(self):
        result = _invoke(["zotero", "collection", "delete", "NOSUCHKEY", "--dry-run"], json_output=True)
        assert result.exit_code == 0
        env = json.loads(result.stdout)
        assert env["dry_run"] is True
        assert env["data"]["descendant_count"] == 0

    def test_collection_reorganize_dry_run_json_envelope(self, tmp_path):
        plan = {
            "collections": [
                {"name": "Topic A", "items": ["K1", "K2"]},
                {"name": "Sub B", "parent": "Topic A", "items": ["K3"]},
            ]
        }
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "collection", "reorganize", str(plan_file), "--dry-run"],
        )

        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["dry_run"] is True
        assert env["data"]["total_collections"] == 2
        assert env["data"]["total_items"] == 3

    def test_tag_add_dry_run(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "K1", "--add", "newtag", "--dry-run"],
            env=WRITE_ENV,
        )
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert "newtag" in result.output

    def test_tag_remove_dry_run(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "K1", "--remove", "oldtag", "--dry-run"],
            env=WRITE_ENV,
        )
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert "oldtag" in result.output

    def test_tag_add_dry_run_json_envelope(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--json", "zotero", "tag", "K1", "--add", "newtag", "--dry-run"], env=WRITE_ENV)
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["dry_run"] is True
        assert env["data"]["would_add"] == "newtag"


class TestCollectionJsonOutput:
    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_create_json_success_envelope(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.create_collection.return_value = "NEWCOL"

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "collection", "create", "New Col", "--parent", "PARENT"],
            env=WRITE_ENV,
        )

        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] is True
        assert env["data"] == {
            "key": "NEWCOL",
            "name": "New Col",
            "parent_key": "PARENT",
            "sync_required": True,
        }
        assert SYNC_REMINDER not in result.output
        mock_writer.create_collection.assert_called_once_with("New Col", parent_key="PARENT")

    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_move_json_success_envelope(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "collection", "move", "ITEM1", "DESTCOL", "--from", "SRCCOL"],
            env=WRITE_ENV,
        )

        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] is True
        assert env["data"] == {
            "item_key": "ITEM1",
            "collection_key": "DESTCOL",
            "source_collection_key": "SRCCOL",
            "action": "moved",
            "sync_required": True,
        }
        assert SYNC_REMINDER not in result.output
        mock_writer.move_to_collection.assert_called_once_with("ITEM1", "DESTCOL", source_collection_key="SRCCOL")

    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_delete_json_success_envelope(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(main, ["--json", "zotero", "collection", "delete", "COL1", "--yes"], env=WRITE_ENV)

        assert result.exit_code == 0
        env = _parse_json_output(result.stdout)
        assert env["ok"] is True
        assert env["data"] == {
            "key": "COL1",
            "deleted": True,
            "descendant_collections": [],
            "descendant_count": 0,
            "sync_required": True,
        }
        assert SYNC_REMINDER not in result.output
        mock_writer.delete_collection.assert_called_once_with("COL1")

    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_rename_json_success_envelope(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(main, ["--json", "zotero", "collection", "rename", "COL1", "New Name"], env=WRITE_ENV)

        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] is True
        assert env["data"] == {"key": "COL1", "new_name": "New Name", "sync_required": True}
        assert SYNC_REMINDER not in result.output
        mock_writer.rename_collection.assert_called_once_with("COL1", "New Name")

    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_reorganize_json_success_envelope(self, mock_writer_cls, tmp_path):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.create_collection.side_effect = ["COLA", "COLB"]
        plan = {
            "collections": [
                {"name": "Topic A", "items": ["K1", "K2"]},
                {"name": "Sub B", "parent": "Topic A", "items": ["K3"]},
            ]
        }
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))

        runner = CliRunner()
        result = runner.invoke(
            main, ["--json", "zotero", "collection", "reorganize", str(plan_file), "--yes"], env=WRITE_ENV
        )

        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["ok"] is True
        assert env["data"]["summary"] == {
            "collections_requested": 2,
            "collections_created": 2,
            "items_moved": 3,
            "failures": 0,
        }
        assert env["data"]["created_collections"][1]["parent_key"] == "COLA"
        assert env["data"]["items_moved"][0]["item_key"] == "K1"
        assert env["data"]["failed"] == []
        assert env["data"]["sync_required"] is True
        assert SYNC_REMINDER not in result.output
        assert mock_writer.create_collection.call_count == 2
        assert mock_writer.set_item_collections.call_count == 3


# --- Shell 补全测试 ---


class TestCompletions:
    def test_completions_bash(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "bash"])
        assert result.exit_code == 0
        assert result.output.strip()
        assert "zot" in result.output

    def test_completions_zsh(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "zsh"])
        assert result.exit_code == 0
        assert result.output.strip()
        assert "zot" in result.output

    def test_completions_fish(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "fish"])
        assert result.exit_code == 0
        assert result.output.strip()
        assert "zot" in result.output

    def test_completions_invalid_shell(self):
        runner = CliRunner()
        result = runner.invoke(main, ["completions", "powershell"])
        assert result.exit_code != 0
        assert "Invalid value" in result.output


# --- 偏移与分页测试 ---


class TestOffset:
    def test_search_with_offset(self, test_db_path):
        from zotero_cli.zotero.access.reader import ZoteroReader

        reader = ZoteroReader(test_db_path)
        all_results = reader.search("", limit=100)
        offset_results = reader.search("", limit=100, offset=1)
        reader.close()
        assert len(offset_results.items) == len(all_results.items) - 1

    def test_search_offset_beyond_total(self, test_db_path):
        from zotero_cli.zotero.access.reader import ZoteroReader

        reader = ZoteroReader(test_db_path)
        result = reader.search("", limit=100, offset=99999)
        reader.close()
        assert len(result.items) == 0
        assert result.total > 0  # total count is still accurate

    def test_summarize_all_with_offset(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "summary", "--offset", "1"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        # `summary` 批量模式先输出 NDJSON 进度事件，再输出最终的
        # envelope JSON 文档；解析最后一段即可。
        last_envelope_start = result.output.rfind("\n{\n")
        envelope_text = result.output[last_envelope_start + 1 :] if last_envelope_start >= 0 else result.output
        data = json.loads(envelope_text)["data"]
        assert isinstance(data, list)

    def test_summarize_all_full_includes_writable_fields(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "--detail", "full", "zotero", "summary", "--limit", "1"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["meta"]["detail"] == "full"
        assert len(env["data"]) == 1
        item = env["data"][0]
        assert "writable_fields" in item
        assert item["writable_fields"]["title"] == item["title"]
        assert "DOI" not in item["writable_fields"]
        assert "pages" not in item["writable_fields"]
        assert "ISSN" not in item["writable_fields"]
        assert "extra" not in item["writable_fields"]

    def test_summarize_all_minimal_omits_extra_fields(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "--detail", "minimal", "zotero", "summary", "--limit", "1"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["meta"]["detail"] == "minimal"
        item = env["data"][0]
        assert set(item.keys()) == {"key", "title", "authors", "date"}

    def test_summarize_all_exclude_tag_filters_results(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "summary", "--exclude-tag", "transformer", "--limit", "100"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["meta"]["excluded_tags"] == ["transformer"]
        assert env["meta"]["filtered_total"] >= env["meta"]["count"]
        assert all("transformer" not in item.get("tags", []) for item in env["data"])

    def test_summarize_all_exclude_collection_key_filters_results(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "summary", "--exclude-collection-key", "COLTR02", "--limit", "100"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        env = _parse_json_output(result.output)
        assert env["meta"]["excluded_collection_keys"] == ["COLTR02"]
        assert env["meta"]["filtered_total"] >= env["meta"]["count"]
        assert all("COLTR02" not in item.get("collections", []) for item in env["data"])


# --- 批量操作测试 ---


class TestBatchDelete:
    @patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter")
    def test_delete_multiple_keys(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "delete", "K1", "K2", "K3", "--yes"],
            env=WRITE_ENV,
        )
        assert result.exit_code == 0
        assert mock_writer.delete_item.call_count == 3
        assert "K1" in result.output
        assert "K2" in result.output
        assert "K3" in result.output

    def test_delete_multiple_dry_run(self):
        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "delete", "K1", "K2", "--dry-run"])
        assert result.exit_code == 0
        assert "[dry-run]" in result.output
        assert "K1" in result.output
        assert "K2" in result.output

    @patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter")
    def test_delete_partial_failure(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.delete_item.side_effect = [None, ZoteroWriteError("Not found"), None]

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "delete", "K1", "K2", "K3", "--yes"],
            env=WRITE_ENV,
        )
        assert result.exit_code != 0
        assert "K1" in result.output
        assert "Not found" in result.output
        assert "K3" in result.output
        # 存在失败项时不应打印同步提醒
        assert SYNC_REMINDER not in result.output


class TestBatchTag:
    @patch("zotero_cli.cli.commands.zotero.tags.ZoteroWriter")
    def test_tag_add_multiple_keys(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "K1", "K2", "K3", "--add", "newtag"],
            env=WRITE_ENV,
        )
        assert result.exit_code == 0
        assert mock_writer.add_tags.call_count == 3
        assert "K1" in result.output
        assert "K2" in result.output
        assert "K3" in result.output

    @patch("zotero_cli.cli.commands.zotero.tags.ZoteroWriter")
    def test_tag_remove_multiple_keys(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "K1", "K2", "--remove", "oldtag"],
            env=WRITE_ENV,
        )
        assert result.exit_code == 0
        assert mock_writer.remove_tags.call_count == 2

    def test_tag_multiple_dry_run(self):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "K1", "K2", "--add", "t", "--dry-run"],
        )
        assert result.exit_code == 0
        assert result.output.count("[dry-run]") == 2

    def test_tag_view_multiple_keys(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "tag", "ATTN001", "BERT002"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        assert "ATTN001" in result.output
        assert "BERT002" in result.output

    def test_tag_view_json_envelope(self, test_db_path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--json", "zotero", "tag", "ATTN001"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"][0]["key"] == "ATTN001"
        assert "transformer" in env["data"][0]["tags"]


# --- 超时测试 ---


# --- 命令层对 ZoteroWriteError 的映射 ---


class TestWriteErrorInCommands:
    @patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter")
    def test_add_write_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.add_item.side_effect = ZoteroWriteError("API error: Bad request")

        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "add", "--doi", "10.1234/test", "--no-resolve"], env=WRITE_ENV)
        assert result.exit_code != 0
        assert "Bad request" in result.output

    @patch("zotero_cli.cli.commands.zotero.items.ZoteroWriter")
    def test_delete_write_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.delete_item.side_effect = ZoteroWriteError("Item 'K1' not found")

        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "delete", "K1", "--yes"], env=WRITE_ENV)
        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("zotero_cli.cli.commands.zotero.tags.ZoteroWriter")
    def test_tag_add_write_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.add_tags.side_effect = ZoteroWriteError("Item 'K1' not found")

        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "tag", "K1", "--add", "t"], env=WRITE_ENV)
        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("zotero_cli.cli.commands.zotero.notes.ZoteroWriter")
    def test_note_write_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.add_note.side_effect = ZoteroWriteError("Network error: timeout")

        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "note", "K1", "--add", "text"], env=WRITE_ENV)
        assert result.exit_code != 0
        assert "timeout" in result.output

    @patch("zotero_cli.cli.commands.zotero.collections.ZoteroWriter")
    def test_collection_create_write_error(self, mock_writer_cls):
        mock_writer = MagicMock()
        mock_writer_cls.return_value = mock_writer
        mock_writer.create_collection.side_effect = ZoteroWriteError("Network error")

        runner = CliRunner()
        result = runner.invoke(main, ["zotero", "collection", "create", "Test"], env=WRITE_ENV)
        assert result.exit_code != 0
        assert "Network error" in result.output
