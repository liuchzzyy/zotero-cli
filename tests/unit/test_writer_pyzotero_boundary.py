"""Offline pyzotero-1.16 boundary contract tests for ZoteroWriter.

The pyzotero upgrade changes the client transport (httpx2), the timeout type,
the create/upload response shapes, and the item-template behavior. These tests
pin the ZoteroWriter side of that boundary with mocks only - no network, no
Zotero library access.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.core.zotero.writer import API_TIMEOUT, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError


@pytest.fixture
def mock_zotero() -> MagicMock:
    with patch("zotero_cli.core.zotero.writer.zotero.Zotero") as zotero_class:
        instance = MagicMock()
        instance.client = MagicMock()
        instance.endpoint = "https://api.zotero.org"
        instance.library_type = "users"
        instance.library_id = "123"
        instance.default_headers.return_value = {"Zotero-API-Key": "secret"}
        zotero_class.return_value = instance
        yield instance


@pytest.fixture
def writer(mock_zotero: MagicMock) -> ZoteroWriter:
    return ZoteroWriter("123", "secret")


# --- client timeout (pyzotero 1.16 / httpx2) --------------------------------


def test_writer_sets_numeric_timeout_on_httpx2_client(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    """pyzotero 1.16's httpx2 client rejects httpx.Timeout objects; use a float."""
    assert mock_zotero.client.timeout == API_TIMEOUT
    assert isinstance(mock_zotero.client.timeout, float)


def test_writer_honours_custom_numeric_timeout(mock_zotero: MagicMock) -> None:
    writer = ZoteroWriter("123", "secret", timeout=7.5)
    assert writer._zot.client.timeout == 7.5
    assert isinstance(mock_zotero.client.timeout, float)


def test_writer_tolerates_missing_client(mock_zotero: MagicMock) -> None:
    mock_zotero.client = None
    writer = ZoteroWriter("123", "secret")
    assert writer._zot is mock_zotero


# --- create_items response shapes -------------------------------------------


def test_add_note_reads_key_from_successful_map(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item_template.return_value = {"itemType": "note", "note": "", "tags": []}
    mock_zotero.create_items.return_value = {
        "success": {"0": "NEWKEY1"},
        "successful": {"0": {"key": "NEWKEY1", "version": 3}},
        "unchanged": {},
        "failed": {},
    }
    assert writer.add_note("PARENT1", "hello") == "NEWKEY1"


def test_add_note_reads_key_from_success_map(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item_template.return_value = {"itemType": "note", "note": ""}
    mock_zotero.create_items.return_value = {"success": {"0": "NEWKEY2"}, "failed": {}}
    assert writer.add_note("PARENT1", "hello") == "NEWKEY2"


def test_add_note_reads_key_from_unchanged_map(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item_template.return_value = {"itemType": "note", "note": ""}
    mock_zotero.create_items.return_value = {"success": {}, "unchanged": {"0": {"key": "NEWKEY3"}}}
    assert writer.add_note("PARENT1", "hello") == "NEWKEY3"


def test_add_note_surfaces_failed_map_as_nonretryable_api_error(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item_template.return_value = {"itemType": "note", "note": ""}
    mock_zotero.create_items.return_value = {"success": {}, "failed": {"0": {"message": "Invalid note payload"}}}
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.add_note("PARENT1", "hello")
    assert exc_info.value.code == "api_error"
    assert exc_info.value.retryable is False
    assert exc_info.value.committed is False
    assert "Invalid note payload" in str(exc_info.value)


@pytest.mark.parametrize("response", [None, {}, {"success": {}, "successful": {}, "failed": {}}, "not-a-dict"])
def test_add_note_rejects_unrecognised_response_shape(
    writer: ZoteroWriter, mock_zotero: MagicMock, response: object
) -> None:
    mock_zotero.item_template.return_value = {"itemType": "note", "note": ""}
    mock_zotero.create_items.return_value = response
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.add_note("PARENT1", "hello")
    assert exc_info.value.code == "api_error"
    assert exc_info.value.retryable is True
    assert exc_info.value.committed is True


def test_add_note_missing_parent_is_typed_not_found(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zotero.item_template.return_value = {"itemType": "note", "note": ""}
    mock_zotero.create_items.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.add_note("PARENT1", "hello")
    assert exc_info.value.code == "not_found"
    assert exc_info.value.committed is False


# --- attachment upload response shapes (pyzotero 1.16 Zupload result) -------


def _attachment_file(tmp_path: Path) -> Path:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


def test_upload_attachment_reads_success_list(writer: ZoteroWriter, mock_zotero: MagicMock, tmp_path: Path) -> None:
    pdf = _attachment_file(tmp_path)
    mock_zotero.attachment_simple.return_value = {
        "success": [{"key": "ATTKEY1", "filename": pdf.name}],
        "failure": [],
        "unchanged": [],
    }
    assert writer.upload_attachment("PARENT1", pdf) == "ATTKEY1"
    mock_zotero.attachment_simple.assert_called_once_with([str(pdf)], parentid="PARENT1")


def test_upload_attachment_reads_unchanged_list(writer: ZoteroWriter, mock_zotero: MagicMock, tmp_path: Path) -> None:
    pdf = _attachment_file(tmp_path)
    mock_zotero.attachment_simple.return_value = {
        "success": [],
        "failure": [],
        "unchanged": [{"key": "ATTKEY2"}],
    }
    assert writer.upload_attachment("PARENT1", pdf) == "ATTKEY2"


def test_upload_attachment_failure_reason_is_nonretryable(
    writer: ZoteroWriter, mock_zotero: MagicMock, tmp_path: Path
) -> None:
    pdf = _attachment_file(tmp_path)
    mock_zotero.attachment_simple.return_value = {
        "success": [],
        "unchanged": [],
        "failure": [{"filename": pdf.name, "error": {"message": "linkMode required"}}],
    }
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.upload_attachment("PARENT1", pdf)
    assert exc_info.value.code == "api_error"
    assert exc_info.value.retryable is False
    assert exc_info.value.committed is False
    assert "linkMode required" in str(exc_info.value)


def test_upload_attachment_bare_failure_is_retryable(
    writer: ZoteroWriter, mock_zotero: MagicMock, tmp_path: Path
) -> None:
    pdf = _attachment_file(tmp_path)
    mock_zotero.attachment_simple.return_value = {"success": [], "unchanged": [], "failure": [{"filename": pdf.name}]}
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.upload_attachment("PARENT1", pdf)
    assert exc_info.value.retryable is True
    assert exc_info.value.committed is True


@pytest.mark.parametrize("response", [None, {}])
def test_upload_attachment_empty_response_is_typed_error(
    writer: ZoteroWriter, mock_zotero: MagicMock, tmp_path: Path, response: object
) -> None:
    pdf = _attachment_file(tmp_path)
    mock_zotero.attachment_simple.return_value = response
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.upload_attachment("PARENT1", pdf)
    assert exc_info.value.code == "api_error"


def test_upload_attachment_missing_file_is_validation_error(writer: ZoteroWriter, tmp_path: Path) -> None:
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.upload_attachment("PARENT1", tmp_path / "missing.pdf")
    assert exc_info.value.code == "validation_error"
    assert exc_info.value.committed is False


# --- permanent delete vs trash restore (raw API boundary) -------------------


def test_delete_item_uses_pyzotero_delete_endpoint(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    item = {"key": "ITEM1", "version": 3, "data": {}}
    mock_zotero.item.return_value = item
    writer.delete_item("ITEM1")
    mock_zotero.delete_item.assert_called_once_with(item)


def test_delete_item_missing_item_is_typed_not_found(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zotero.item.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.delete_item("ITEM1")
    assert exc_info.value.code == "not_found"


def test_restore_from_trash_patches_deleted_zero_with_version_guard(
    writer: ZoteroWriter, mock_zotero: MagicMock
) -> None:
    mock_zotero.item.return_value = {"key": "ITEM1", "version": 7, "data": {}}
    response = MagicMock(status_code=204, text="", headers={})
    mock_zotero.client.patch.return_value = response
    writer.restore_from_trash("ITEM1")
    args, kwargs = mock_zotero.client.patch.call_args
    assert args[0] == "https://api.zotero.org/users/123/items/ITEM1"
    assert kwargs["json"] == {"deleted": 0}
    assert kwargs["headers"]["If-Unmodified-Since-Version"] == "7"
    assert kwargs["headers"]["Content-Type"] == "application/json"


def test_restore_from_trash_conflict_is_typed(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item.return_value = {"key": "ITEM1", "version": 7, "data": {}}
    mock_zotero.client.patch.return_value = MagicMock(status_code=412, text="precondition failed", headers={})
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.restore_from_trash("ITEM1")
    assert exc_info.value.code == "conflict"


def test_restore_from_trash_refuses_missing_version(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    mock_zotero.item.return_value = {"key": "ITEM1", "data": {}}
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.restore_from_trash("ITEM1")
    assert exc_info.value.code == "conflict"
    mock_zotero.client.patch.assert_not_called()


def test_restore_from_trash_missing_item_is_typed_not_found(writer: ZoteroWriter, mock_zotero: MagicMock) -> None:
    from pyzotero.zotero_errors import ResourceNotFoundError

    mock_zotero.item.side_effect = ResourceNotFoundError("missing")
    with pytest.raises(ZoteroWriteError) as exc_info:
        writer.restore_from_trash("ITEM1")
    assert exc_info.value.code == "not_found"
