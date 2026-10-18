"""Tests for trash management (list and restore)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from tests.support import FIXTURES_DIR
from tests.support import invoke_cli as _invoke

from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError

_WRITE_ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}


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
    @patch("zotero_cli.core.zotero.writer.zotero.Zotero")
    def test_restore_from_trash(self, mock_zotero_cls):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        mock_zot.endpoint = "https://api.zotero.org"
        mock_zot.library_type = "users"
        mock_zot.library_id = "123"
        mock_zot.default_headers.return_value = {"Authorization": "Bearer abc"}
        mock_zot.item.return_value = {"key": "K1", "version": 19, "data": {"deleted": 1}}
        mock_zot.client.patch.return_value = MagicMock(status_code=204, text="")
        writer = ZoteroWriter(library_id="123", api_key="abc")
        writer.restore_from_trash("K1")
        mock_zot.client.patch.assert_called_once()
        args, kwargs = mock_zot.client.patch.call_args
        assert args[0] == "https://api.zotero.org/users/123/items/K1"
        assert kwargs["json"] == {"deleted": 0}
        assert kwargs["headers"]["If-Unmodified-Since-Version"] == "19"
        assert kwargs["headers"]["Authorization"] == "Bearer abc"
        # RESTORE must not go through pyzotero update_item (it rejects 'deleted').
        mock_zot.update_item.assert_not_called()

    @patch("zotero_cli.core.zotero.writer.zotero.Zotero")
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


class TestTrashRestoreCLI:
    def test_restore_success_emits_one_ok_envelope(self):
        with patch("zotero_cli.commands.items.trash.ZoteroWriter") as writer_cls:
            result = _invoke(["trash", "restore", "KEY1"], json_output=True, env=_WRITE_ENV)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["restored"] == ["KEY1"]
        assert payload["data"]["sync_required"] is True
        writer_cls.return_value.restore_from_trash.assert_called_once_with("KEY1")

    def test_restore_not_found_is_typed_and_nonzero(self):
        with patch("zotero_cli.commands.items.trash.ZoteroWriter") as writer_cls:
            writer_cls.return_value.restore_from_trash.side_effect = ZoteroWriteError(
                "Item 'GONE' not found", code="not_found", retryable=False
            )
            result = _invoke(["trash", "restore", "GONE"], json_output=True, env=_WRITE_ENV)
        assert result.exit_code == 4, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "not_found"
        assert "permanent" in payload["error"]["hint"]

    def test_restore_mixed_results_are_partial_and_nonzero(self):
        with patch("zotero_cli.commands.items.trash.ZoteroWriter") as writer_cls:
            writer_cls.return_value.restore_from_trash.side_effect = [
                None,
                ZoteroWriteError("conflict", code="conflict", retryable=False),
            ]
            result = _invoke(["trash", "restore", "OK1", "BAD1"], json_output=True, env=_WRITE_ENV)
        assert result.exit_code == 6, result.output
        payload = json.loads(result.output)
        assert payload["ok"] == "partial"
        assert payload["data"]["restored"] == ["OK1"]
        assert payload["data"]["failed"][0]["key"] == "BAD1"
        assert payload["data"]["failed"][0]["code"] == "conflict"

    def test_restore_network_error_is_retryable_exit5(self):
        with patch("zotero_cli.commands.items.trash.ZoteroWriter") as writer_cls:
            writer_cls.return_value.restore_from_trash.side_effect = ZoteroWriteError(
                "network", code="network_error", retryable=True, committed=True
            )
            result = _invoke(["trash", "restore", "KEY1"], json_output=True, env=_WRITE_ENV)
        assert result.exit_code == 5, result.output
        payload = json.loads(result.output)
        assert payload["error"]["retryable"] is False
        assert payload["error"]["committed"] is True

    def test_restore_dry_run_makes_no_write(self):
        with patch("zotero_cli.commands.items.trash.ZoteroWriter") as writer_cls:
            result = _invoke(["trash", "restore", "KEY1", "--dry-run"], json_output=True, env=_WRITE_ENV)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_restore"] == ["KEY1"]
        writer_cls.assert_not_called()
