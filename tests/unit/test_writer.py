from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_writer_init(mock_zotero_cls):
    ZoteroWriter(library_id="123", api_key="abc")
    mock_zotero_cls.assert_called_once_with("123", "user", "abc")


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_note(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item_template.return_value = {"itemType": "note", "note": "", "parentItem": ""}
    mock_zot.create_items.return_value = {"successful": {"0": {"key": "N1"}}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    result = writer.add_note("PARENT1", "My note content")
    assert result == "N1"
    mock_zot.create_items.assert_called_once()


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_note_fallback_on_template_error(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item_template.side_effect = RuntimeError("400 Bad Request: itemType not provided")
    mock_zot.create_items.return_value = {"successful": {"0": {"key": "N2"}}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    result = writer.add_note("PARENT1", "Fallback note content")
    assert result == "N2"
    mock_zot.create_items.assert_called_once()
    created_payload = mock_zot.create_items.call_args[0][0][0]
    assert created_payload["itemType"] == "note"
    assert created_payload["note"] == "Fallback note content"
    assert created_payload["parentItem"] == "PARENT1"


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_tags(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item.return_value = {"key": "K1", "data": {"tags": [{"tag": "old"}]}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.add_tags("K1", ["new1", "new2"])
    mock_zot.update_item.assert_called_once()


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_delete_item(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item.return_value = {"key": "K1", "data": {"deleted": 0}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.delete_item("K1")
    mock_zot.delete_item.assert_called_once()


# --- Error-path tests ---


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_note_network_error(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item_template.return_value = {"itemType": "note", "note": "", "parentItem": ""}
    from httpx import ConnectError

    mock_zot.create_items.side_effect = ConnectError("Network unreachable")

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="Network"):
        writer.add_note("P1", "content")


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_item_read_error_is_retryable(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item_template.return_value = {"itemType": "journalArticle", "DOI": ""}
    from httpx import ReadError

    mock_zot.create_items.side_effect = ReadError("Connection reset")

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="Network error") as exc_info:
        writer.add_item(doi="10.1234/test")

    assert exc_info.value.code == "network_error"
    assert exc_info.value.retryable is True


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_add_note_api_failure(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item_template.return_value = {"itemType": "note", "note": "", "parentItem": ""}
    mock_zot.create_items.return_value = {"successful": {}, "failed": {"0": {"message": "Bad request"}}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="Bad request"):
        writer.add_note("P1", "content")


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_delete_item_not_found(mock_zotero_cls):
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item.side_effect = ResourceNotFoundError("Not found")

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="not found"):
        writer.delete_item("NONEXIST")


# --- Collection management tests ---


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_delete_collection(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.collection.return_value = {"key": "COL1", "version": 1}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.delete_collection("COL1")
    mock_zot.collection.assert_called_once_with("COL1")
    mock_zot.delete_collection.assert_called_once()


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_delete_collection_not_found(mock_zotero_cls):
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.collection.side_effect = ResourceNotFoundError("Not found")

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="not found"):
        writer.delete_collection("NONEXIST")


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_rename_collection(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.collection.return_value = {"key": "COL1", "data": {"name": "Old Name"}, "version": 1}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.rename_collection("COL1", "New Name")
    mock_zot.collection.assert_called_once_with("COL1")
    mock_zot.update_collection.assert_called_once()
    # Verify the name was updated in the payload
    call_args = mock_zot.update_collection.call_args[0][0]
    assert call_args["data"]["name"] == "New Name"


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_rename_collection_not_found(mock_zotero_cls):
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.collection.side_effect = ResourceNotFoundError("Not found")

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError, match="not found"):
        writer.rename_collection("NONEXIST", "New Name")


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_move_to_collection_without_source_adds_membership(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item.return_value = {"key": "K1", "data": {"collections": ["SRC"]}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.move_to_collection("K1", "DEST")

    mock_zot.addto_collection.assert_called_once_with("DEST", mock_zot.item.return_value)
    mock_zot.update_item.assert_not_called()


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_move_to_collection_with_source_rewrites_membership(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    item = {"key": "K1", "data": {"collections": ["SRC", "KEEP"]}}
    mock_zot.item.return_value = item

    writer = ZoteroWriter(library_id="123", api_key="abc")
    writer.move_to_collection("K1", "DEST", source_collection_key="SRC")

    assert item["data"]["collections"] == ["KEEP", "DEST"]
    mock_zot.update_item.assert_called_once_with(item)


@patch("zotero_cli.core.zotero.writer.zotero.Zotero")
def test_move_to_collection_source_missing_is_validation_error(mock_zotero_cls):
    mock_zot = MagicMock()
    mock_zotero_cls.return_value = mock_zot
    mock_zot.item.return_value = {"key": "K1", "data": {"collections": ["OTHER"]}}

    writer = ZoteroWriter(library_id="123", api_key="abc")
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.move_to_collection("K1", "DEST", source_collection_key="SRC")

    assert exc_info.value.code == "validation_error"
