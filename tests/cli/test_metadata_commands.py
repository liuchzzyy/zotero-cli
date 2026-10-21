"""`metadata` 子命令测试：摘要素材生成与批量处理。"""

from __future__ import annotations

import json

from tests.support import invoke_cli as _invoke

_TABLE_ENV = {"ZOT_FORMAT": "table"}


def test_summarize_prints_title_and_creators():
    result = _invoke(["metadata", "summarize", "ATTN001"], env=_TABLE_ENV)
    assert result.exit_code == 0
    assert "Attention Is All You Need" in result.output
    assert "Vaswani" in result.output


def test_summarize_json_envelope_has_request_id():
    result = _invoke(["metadata", "summarize", "ATTN001"], json_output=True, env=_TABLE_ENV)
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["meta"]["request_id"]
    assert envelope["data"]["title"] == "Attention Is All You Need"


def test_summarize_all_emits_single_list_envelope():
    result = _invoke(["metadata", "summarize-all", "--limit", "2"], json_output=True, env=_TABLE_ENV)
    assert result.exit_code == 0, result.output
    # stdout 只有一个 envelope，结构化进度统一写到 stderr。
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["meta"]["count"] == 2
    assert isinstance(envelope["data"], list)
