"""基于夹具数据库的端到端集成测试。"""

import json

from click.testing import CliRunner
from tests.support import invoke_cli

from zotero_cli import __version__
from zotero_cli.cli import main


def _run(args, test_db_path, json_out=False):
    return invoke_cli(args, json_output=json_out, env={"ZOT_DATA_DIR": str(test_db_path.parent)})


def test_full_read_workflow(test_db_path):
    """检索 → 阅读 → 笔记 → 导出 → 摘要 → 相关条目。"""
    # Search
    r = _run(["zotero", "search", "transformer"], test_db_path, json_out=True)
    assert r.exit_code == 0
    # `search` 返回代理 envelope：{"ok": True, "data": [...]}。
    items = json.loads(r.output)["data"]
    assert len(items) >= 1
    key = items[0]["key"]

    # Read
    r = _run(["zotero", "read", key], test_db_path, json_out=True)
    assert r.exit_code == 0
    detail = json.loads(r.output)["data"]
    assert detail["title"]

    # Notes
    r = _run(["zotero", "note", key], test_db_path)
    assert r.exit_code == 0

    # Export
    r = _run(["zotero", "export", key], test_db_path)
    assert r.exit_code == 0
    assert "@" in r.output  # BibTeX

    # Summarize
    r = _run(["metadata", "summarize", key], test_db_path)
    assert r.exit_code == 0
    assert "Title:" in r.output

    # Relate
    r = _run(["zotero", "relate", key], test_db_path)
    assert r.exit_code == 0


def test_collection_workflow(test_db_path):
    """先列出集合，再列出该集合内的条目。"""
    r = _run(["zotero", "collection", "list"], test_db_path, json_out=True)
    assert r.exit_code == 0
    colls = json.loads(r.output)
    assert len(colls) >= 1

    r = _run(["zotero", "collection", "items", "COLML01"], test_db_path, json_out=True)
    assert r.exit_code == 0
    items = json.loads(r.output)
    assert len(items) >= 2


def test_version():
    runner = CliRunner()
    r = runner.invoke(main, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.output
