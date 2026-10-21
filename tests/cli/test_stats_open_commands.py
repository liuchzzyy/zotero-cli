"""`stats` 与 `open` 子命令测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main


class TestStatsCmd:
    def test_stats_human(self, test_db_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main, ["zotero", "stats"], env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"}
        )
        assert result.exit_code == 0
        assert "Total items:" in result.output
        assert "Items by type:" in result.output
        assert "Collections" in result.output
        assert "Top tags" in result.output

    def test_stats_json(self, test_db_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main, ["--json", "zotero", "stats"], env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"}
        )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["meta"]["request_id"]
        data = envelope["data"]
        assert "total_items" in data
        assert "by_type" in data
        assert "top_tags" in data
        assert "collections" in data
        assert "pdf_attachments" in data
        assert "notes" in data
        assert data["total_items"] > 0


class TestOpenCmd:
    def test_open_nonexistent_item(self, test_db_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main, ["zotero", "open", "NONEXIST"], env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"}
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_open_no_pdf(self, test_db_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main, ["zotero", "open", "DEEP003"], env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"}
        )
        assert result.exit_code != 0
        assert "No PDF" in result.output

    @patch("zotero_cli.cli.commands.zotero.open_item._open_path")
    def test_open_url(self, mock_open, test_db_path: Path):
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["zotero", "open", "--url", "ATTN001"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 0
        assert "Opening" in result.output
        mock_open.assert_called_once()

    def test_open_url_no_url(self, test_db_path: Path):
        runner = CliRunner()
        # DEEP003 是图书，可能没有 URL——用它覆盖错误分支
        result = runner.invoke(
            main,
            ["zotero", "open", "--url", "DEEP003"],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code != 0


class TestGetStats:
    def test_get_stats(self, test_db_path: Path):
        from zotero_cli.zotero.reader import ZoteroReader

        reader = ZoteroReader(test_db_path)
        try:
            stats = reader.get_stats()
            assert stats["total_items"] > 0
            assert isinstance(stats["by_type"], dict)
            assert isinstance(stats["top_tags"], dict)
            assert isinstance(stats["collections"], dict)
            assert stats["pdf_attachments"] >= 0
            assert stats["notes"] >= 0
        finally:
            reader.close()


class TestOpenPath:
    """不经过 shell 调用；Windows 上使用 os.startfile。"""

    def test_win32_uses_os_startfile(self, monkeypatch):
        import os
        import sys

        from zotero_cli.cli.commands.zotero.open_item import _open_path

        opened: list[str] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", lambda path: opened.append(path), raising=False)
        with patch("zotero_cli.cli.commands.zotero.open_item.subprocess.run") as run:
            _open_path(r"C:\\papers\\paper.pdf")
        assert opened == [r"C:\\papers\\paper.pdf"]
        run.assert_not_called()

    def test_darwin_uses_open_binary(self, monkeypatch):
        import sys

        from zotero_cli.cli.commands.zotero.open_item import _open_path

        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("zotero_cli.cli.commands.zotero.open_item.subprocess.run") as run:
            _open_path("/tmp/paper.pdf")
        run.assert_called_once_with(["open", "/tmp/paper.pdf"], check=True)

    def test_linux_uses_xdg_open_binary(self, monkeypatch):
        import sys

        from zotero_cli.cli.commands.zotero.open_item import _open_path

        monkeypatch.setattr(sys, "platform", "linux")
        with patch("zotero_cli.cli.commands.zotero.open_item.subprocess.run") as run:
            _open_path("/tmp/paper.pdf")
        run.assert_called_once_with(["xdg-open", "/tmp/paper.pdf"], check=True)
