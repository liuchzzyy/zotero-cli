"""`ai-note2` 命令：跳过、dry-run、local-only、错误映射与选项转发。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from tests.support import invoke_cli

from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.execution import Execution

pytestmark = pytest.mark.cli


def _run(args, env=None):
    base = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "abc", "ZOT_FORMAT": ""}
    base.update(env or {})
    return invoke_cli(args, env=base)


def test_no_ai_note_skips_without_model_or_write_credentials():
    skipped = Execution(
        data={
            "status": "skipped",
            "reason": "no_ai_note",
            "item_key": "ITEM1",
            "message": "没有旧 AI 分析笔记",
        }
    )
    with patch("zotero_cli.cli.commands.ai_notes2.execute_ai_note2", return_value=skipped) as execute:
        result = _run(["ai-note2", "ITEM1"], env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""})
    assert result.exit_code == 0
    assert json.loads(result.output)["data"]["status"] == "skipped"
    assert execute.call_args.kwargs["key"] == "ITEM1"


def test_dry_run_forwards_options_and_prints_preview():
    execution = Execution(
        data={
            "status": "dry_run",
            "item_key": "ITEM1",
            "source_note_key": "NOTE1",
            "documents": [{"attachment_key": "PDF1", "aliases": [], "pdf_role": "MS", "role_basis": "唯一 PDF"}],
            "blocks": 3,
            "characters": 100,
            "budget": {"projection_bytes": 120, "within_budget": True},
            "cache_note": "缓存缺失时仍可能调用 MinerU",
        }
    )
    with patch("zotero_cli.cli.commands.ai_notes2.execute_ai_note2", return_value=execution) as execute:
        result = _run(["ai-note2", "ITEM1", "--dry-run", "--source-note", "NOTE1", "--attachment", "PDF1"])
    assert result.exit_code == 0
    kwargs = execute.call_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["source_note"] == "NOTE1"
    assert kwargs["attachments"] == ["PDF1"]
    assert json.loads(result.output)["data"]["status"] == "dry_run"


def test_local_only_forwards_flag_and_reports_artifacts():
    execution = Execution(
        data={
            "status": "local_only",
            "item_key": "ITEM1",
            "run_number": 1,
            "object_count": 12,
            "model": "test-model",
            "repair_used": False,
            "output_dir": "/tmp/out",
            "files": {},
        }
    )
    with patch("zotero_cli.cli.commands.ai_notes2.execute_ai_note2", return_value=execution) as execute:
        result = _run(["ai-note2", "ITEM1", "--local-only"])
    assert result.exit_code == 0
    assert execute.call_args.kwargs["local_only"] is True
    assert json.loads(result.output)["data"]["status"] == "local_only"


def test_committed_error_is_not_retryable_and_keeps_hint():
    error = AiNote2Error("note 已创建，但标签失败", code="network_error", committed=True, hint_key="tag_unknown")
    with patch("zotero_cli.cli.commands.ai_notes2.execute_ai_note2", side_effect=error):
        result = _run(["ai-note2", "ITEM1"])
    assert result.exit_code != 0
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["retryable"] is False
    assert "只补" in envelope["error"]["hint"]
    assert envelope["error"]["context"] == "ai-note2"


def test_group_library_is_forwarded():
    execution = Execution(data={"status": "skipped", "reason": "no_ai_note", "item_key": "ITEM1"})
    with patch("zotero_cli.cli.commands.ai_notes2.execute_ai_note2", return_value=execution) as execute:
        result = _run(["--library", "group:42", "ai-note2", "ITEM1"])
    assert result.exit_code == 0
    assert execute.call_args.kwargs["library_type"] == "group"
    assert execute.call_args.kwargs["group_id"] == "42"
