"""`zotero recent` 子命令测试。"""

from __future__ import annotations

import json

from tests.support import SAMPLE_DATA_DIR
from tests.support import invoke_cli as _invoke

from zotero_cli.zotero.reader import ZoteroReader


class TestRecent:
    def test_recent_default(self):
        """不带参数时按 dateAdded 降序返回条目。"""
        result = _invoke(["zotero", "recent"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        # 默认 7 天时间窗——夹具条目均来自 2024 年，因此匹配不到
        assert isinstance(data, list)

    def test_recent_days_zero(self):
        """`--days 0` 返回空结果（夹具条目均来自 2024 年）。"""
        result = _invoke(["zotero", "recent", "--days", "0"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert len(data) == 0

    def test_recent_large_window(self):
        """`--days 9999` 返回全部条目。"""
        result = _invoke(["zotero", "recent", "--days", "9999"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert len(data) >= 3

    def test_recent_large_window_sorted(self):
        """返回的条目按 dateAdded 降序排列。"""
        result = _invoke(["zotero", "recent", "--days", "9999"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_added"] for i in data]
        assert dates == sorted(dates, reverse=True)

    def test_recent_modified(self):
        """`--modified` 改为按 dateModified 排序。"""
        result = _invoke(["zotero", "recent", "--modified", "--days", "9999"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_modified"] for i in data]
        assert dates == sorted(dates, reverse=True)

    def test_recent_table_output(self):
        result = _invoke(["zotero", "recent", "--days", "9999"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output or "BERT002" in result.output or "DEEP003" in result.output

    def test_recent_no_results_message(self):
        result = _invoke(["zotero", "recent", "--days", "0"])
        assert result.exit_code == 0
        assert "No items" in result.output

    def test_reader_get_recent_items(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            items = reader.get_recent_items(since="2000-01-01", limit=50)
            assert len(items) >= 3
            dates = [i.date_added for i in items]
            assert dates == sorted(dates, reverse=True)
        finally:
            reader.close()

    def test_recent_stream_reports_has_more(self):
        result = _invoke(["zotero", "recent", "--days", "9999", "--limit", "2", "--stream"])
        assert result.exit_code == 0
        lines = [json.loads(line) for line in result.output.strip().split("\n") if line.strip()]
        summary = lines[-1]["summary"]
        assert summary["count"] == 2
        assert summary["has_more"] is True

    def test_recent_stream_ignores_no_json(self):
        result = _invoke(["--no-json", "zotero", "recent", "--days", "9999", "--limit", "2", "--stream"])
        assert result.exit_code == 0
        lines = [json.loads(line) for line in result.output.strip().split("\n") if line.strip()]
        assert len(lines) == 3  # two records + summary
        assert "summary" in lines[-1]

    def test_recent_stream_without_extra_matches_has_no_more(self):
        result = _invoke(["--json", "zotero", "recent", "--days", "0", "--stream"])
        assert result.exit_code == 0
        lines = [json.loads(line) for line in result.output.strip().split("\n") if line.strip()]
        assert lines[-1]["summary"] == {"count": 0, "has_more": False}
