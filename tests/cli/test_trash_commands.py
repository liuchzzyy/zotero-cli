"""Tests for trash management (list and restore)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from tests.support import FIXTURES_DIR
from tests.support import invoke_cli as _invoke

from zotero_cli.core.reader import ZoteroReader
from zotero_cli.core.writer import ZoteroWriteError, ZoteroWriter


class TestTrashReader:
    def test_get_trash_items_returns_trashed(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            items = reader.get_trash_items(limit=50)
            assert len(items) >= 1
            keys = [i.key for i in items]
            assert "TRSH007" in keys
        finally:
            reader.close()

    def test_get_trash_items_excludes_non_trashed(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            items = reader.get_trash_items(limit=50)
            keys = [i.key for i in items]
            assert "ATTN001" not in keys
        finally:
            reader.close()

    def test_get_trash_items_respects_limit(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            items = reader.get_trash_items(limit=0)
            assert len(items) == 0
        finally:
            reader.close()

    def test_get_trash_items_has_full_item_data(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            items = reader.get_trash_items(limit=50)
            trashed = [i for i in items if i.key == "TRSH007"][0]
            assert trashed.title == "Old Survey of Neural Networks"
            assert trashed.item_type == "journalArticle"
        finally:
            reader.close()


class TestTrashWriter:
    @patch("zotero_cli.core.writer.zotero.Zotero")
    def test_restore_from_trash(self, mock_zotero_cls):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        mock_zot.item.return_value = {"key": "K1", "data": {"deleted": 1}}
        writer = ZoteroWriter(library_id="123", api_key="abc")
        writer.restore_from_trash("K1")
        mock_zot.update_item.assert_called_once()
        call_args = mock_zot.update_item.call_args[0][0]
        assert call_args["data"]["deleted"] == 0

    @patch("zotero_cli.core.writer.zotero.Zotero")
    def test_restore_not_found(self, mock_zotero_cls):
        from pyzotero.zotero_errors import ResourceNotFoundError

        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        mock_zot.item.side_effect = ResourceNotFoundError("Not found")
        writer = ZoteroWriter(library_id="123", api_key="abc")
        with pytest.raises(ZoteroWriteError, match="not found"):
            writer.restore_from_trash("MISSING")


class TestTrashCLI:
    def test_trash_list(self):
        result = _invoke(["trash", "list"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        keys = [i["key"] for i in data]
        assert "TRSH007" in keys

    def test_trash_list_table(self):
        result = _invoke(["trash", "list"])
        assert result.exit_code == 0
        assert "TRSH007" in result.output

    def test_empty_trash_list_uses_json_envelope(self):
        result = _invoke(["trash", "list", "--limit", "0"], json_output=True)
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"] == []
        assert env["meta"]["count"] == 0
