"""`zotero relate` 子命令测试：相关条目列举与缺失条目的错误契约。"""

from __future__ import annotations

import json

from tests.support import invoke_cli as _invoke

_TABLE_ENV = {"ZOT_FORMAT": "table"}


def test_relate_lists_related_items():
    result = _invoke(["zotero", "relate", "ATTN001"], env=_TABLE_ENV)
    assert result.exit_code == 0
    assert "BERT002" in result.output


def test_relate_missing_item_is_not_found():
    result = _invoke(["zotero", "relate", "MISSINGKEY"], json_output=True, env=_TABLE_ENV)
    assert result.exit_code == 4
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "not_found"
