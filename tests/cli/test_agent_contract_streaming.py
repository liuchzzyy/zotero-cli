"""流式契约：NDJSON 记录、summary 行与 schema 元数据。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import invoke_agent_cli as _run

from zotero_cli.output.exit_codes import EXIT_OK


def _parse_ndjson(s: str) -> list[dict]:
    return [json.loads(line) for line in s.strip().split("\n") if line.strip()]


class TestNDJSONStream:
    def test_search_stream_emits_ndjson(self):
        result = _run(["zotero", "search", "attention", "--stream"])
        assert result.exit_code == EXIT_OK
        lines = _parse_ndjson(result.output)
        assert len(lines) >= 1
        # 最后一行是 summary
        last = lines[-1]
        assert "summary" in last
        assert last["summary"]["has_more"] is False
        assert last["summary"]["count"] == len(lines) - 1

    def test_list_stream_emits_ndjson(self):
        result = _run(["zotero", "list", "--stream"])
        assert result.exit_code == EXIT_OK
        lines = _parse_ndjson(result.output)
        last = lines[-1]
        assert "summary" in last
        for line in lines[:-1]:
            assert line["ok"] is True
            assert "data" in line

    def test_stream_summary_has_meta(self):
        result = _run(["zotero", "list", "--stream", "--limit", "2"])
        lines = _parse_ndjson(result.output)
        last = lines[-1]
        assert "meta" in last
        assert "request_id" in last["meta"]

    def test_stream_ignores_no_json_flag(self):
        # --stream 请求 NDJSON 机器契约，优先级高于 --no-json。
        result = _run(["--no-json", "zotero", "list", "--stream", "--limit", "2"])
        assert result.exit_code == EXIT_OK
        lines = _parse_ndjson(result.output)
        assert len(lines) >= 2
        assert "summary" in lines[-1]


class TestSchemaMetadata:
    def test_schema_has_safety_tier(self):
        env = json.loads(_run(["schema", "zotero", "delete"]).output)
        assert env["data"]["safety_tier"] == "destructive"

    def test_schema_read_cmd_tier(self):
        env = json.loads(_run(["schema", "zotero", "search"]).output)
        assert env["data"]["safety_tier"] == "read"

    def test_schema_write_cmd_tier(self):
        env = json.loads(_run(["schema", "zotero", "add"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_tag_cmd_tier_is_write(self):
        env = json.loads(_run(["schema", "zotero", "tag"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_nested_collection_create_is_write(self):
        env = json.loads(_run(["schema", "zotero", "collection", "create"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_nested_collection_delete_is_destructive(self):
        env = json.loads(_run(["schema", "zotero", "collection", "delete"]).output)
        assert env["data"]["safety_tier"] == "destructive"

    def test_schema_nested_trash_restore_is_write(self):
        env = json.loads(_run(["schema", "zotero", "trash", "restore"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_nested_collection_list_stays_read(self):
        env = json.loads(_run(["schema", "zotero", "collection", "list"]).output)
        assert env["data"]["safety_tier"] == "read"

    # 会改动本地状态的嵌套 config/workspace 命令都已完成分级。
    def test_schema_nested_config_init_is_write(self):
        env = json.loads(_run(["schema", "config", "init"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_nested_config_profile_set_is_write(self):
        env = json.loads(_run(["schema", "config", "profile", "set"]).output)
        assert env["data"]["safety_tier"] == "write"

    def test_schema_nested_config_cache_clear_is_destructive(self):
        env = json.loads(_run(["schema", "config", "cache", "clear"]).output)
        assert env["data"]["safety_tier"] == "destructive"

    def test_schema_nested_workspace_mutations_are_write(self):
        for subcommand in ("new", "add", "remove", "import", "index", "reindex", "embed"):
            env = json.loads(_run(["schema", "workspace", subcommand]).output)
            assert env["data"]["safety_tier"] == "write", subcommand

    def test_schema_nested_workspace_delete_is_destructive(self):
        env = json.loads(_run(["schema", "workspace", "delete"]).output)
        assert env["data"]["safety_tier"] == "destructive"

    def test_schema_nested_workspace_reads_stay_read(self):
        for subcommand in ("list", "show", "search", "query"):
            env = json.loads(_run(["schema", "workspace", subcommand]).output)
            assert env["data"]["safety_tier"] == "read", subcommand

    def test_schema_collection_move_has_source_option(self):
        env = json.loads(_run(["schema", "zotero", "collection", "move"]).output)
        flags = {flag for param in env["data"]["params"] for flag in param.get("flags", [])}
        assert "--from" in flags

    def test_schema_since_and_deprecated_fields(self):
        env = json.loads(_run(["schema", "zotero", "search"]).output)
        assert "since" in env["data"]
        assert "deprecated" in env["data"]
        assert env["data"]["deprecated"] is False


class TestEmitProgress:
    def test_emit_progress_writes_ndjson_to_stderr(self):
        import sys
        from io import StringIO

        from zotero_cli.output.envelope import request_scope
        from zotero_cli.output.streaming import emit_progress

        captured = StringIO()
        old = sys.stderr
        sys.stderr = captured
        try:
            with request_scope():
                emit_progress("start", phase="x", total=10)
                emit_progress("progress", phase="x", done=5, total=10)
                emit_progress("complete", phase="x", done=10, total=10)
        finally:
            sys.stderr = old
        lines = [json.loads(line) for line in captured.getvalue().strip().split("\n")]
        assert len(lines) == 3
        assert lines[0]["event"] == "start"
        assert lines[1]["done"] == 5
        assert lines[2]["event"] == "complete"
        # 所有记录携带相同的 request_id
        rids = {line["request_id"] for line in lines}
        assert len(rids) == 1
        # elapsed_ms 单调不减
        elapsed = [line["elapsed_ms"] for line in lines]
        assert elapsed == sorted(elapsed)


class TestFulltextLimitedMeta:
    """v130 有界全文扫描：暴露预算标志而不是丢弃它。"""

    def test_list_json_meta_reports_fulltext_limited(self):
        fake = SimpleNamespace(items=[], total=0, fulltext_limited=True)
        with patch("zotero_cli.cli.commands.zotero.items.ZoteroReader") as reader_cls:
            reader_cls.return_value.search.return_value = fake
            result = _run(["--json", "zotero", "list"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.output)
        assert env["meta"]["fulltext_limited"] is True

    def test_list_stream_summary_reports_fulltext_limited(self):
        fake = SimpleNamespace(items=[], total=0, fulltext_limited=True)
        with patch("zotero_cli.cli.commands.zotero.items.ZoteroReader") as reader_cls:
            reader_cls.return_value.search.return_value = fake
            result = _run(["zotero", "list", "--stream"])
        assert result.exit_code == EXIT_OK, result.output
        summary = _parse_ndjson(result.output)[-1]
        assert summary["meta"]["fulltext_limited"] is True
