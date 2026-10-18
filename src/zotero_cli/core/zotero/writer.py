"""Zotero Web API write layer.

ZoteroWriter lives here; error translation lives in writer_errors and the
Extra-field short-note merge in short_note_merge.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from httpx import HTTPError as HttpxHttpError
from pyzotero import zotero
from pyzotero.zotero_errors import PyZoteroError, ResourceNotFoundError

from zotero_cli.core.zotero.short_note_merge import merge_short_note_into_extra
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError, _friendly_api_error

SYNC_REMINDER = "Change saved. Run Zotero sync to update local database."

API_TIMEOUT = 30.0  # seconds


class ZoteroWriter:
    def __init__(self, library_id: str, api_key: str, library_type: str = "user", timeout: float = API_TIMEOUT) -> None:
        self._zot = zotero.Zotero(library_id, library_type, api_key)
        if self._zot.client is not None:
            self._zot.client.timeout = httpx.Timeout(timeout)

    def _check_response(self, resp: dict) -> str:
        """Check create response, return key or raise error."""
        if resp.get("successful") and "0" in resp["successful"]:
            return str(resp["successful"]["0"]["key"])
        failed = resp.get("failed", {})
        if failed:
            msg = failed.get("0", {}).get("message", "Unknown API error")
            raise ZoteroWriteError(f"API error: {msg}")
        raise ZoteroWriteError("Unexpected API response", code="api_error", retryable=True)

    def add_note(self, parent_key: str, content: str) -> str:
        """Create a child note under an item; returns the new note key."""
        try:
            try:
                template = self._zot.item_template("note")
            except Exception:
                template = {"itemType": "note", "note": "", "tags": [], "collections": [], "relations": {}}
            template["note"] = content
            template["parentItem"] = parent_key
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Parent item '{parent_key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def add_item(
        self,
        doi: str | None = None,
        url: str | None = None,
        *,
        extra_fields: dict[str, object] | None = None,
    ) -> str:
        """Create a Zotero item from a DOI or URL.

        `extra_fields` is merged into the item template before posting, used
        by callers that resolved DOI metadata via Crossref (see
        `core/zotero/metadata.py`) — Zotero's Web API does not auto-resolve
        DOIs on its own, so without this the created item is a bare shell.
        """
        if not doi and not url:
            raise ValueError("Either doi or url must be provided")
        try:
            if doi:
                template = self._zot.item_template("journalArticle")
                template["DOI"] = doi
                if extra_fields:
                    template.update(extra_fields)
                resp = self._zot.create_items([template])
                return self._check_response(resp)
            template = self._zot.item_template("webpage")
            template["url"] = url
            if extra_fields:
                template.update(extra_fields)
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def update_short_note(self, key: str, short_note: str) -> None:
        """Merge a short-note line into the item's Extra field via the Web API."""
        try:
            item = self._zot.item(key)
            current = item["data"].get("extra") or ""
            item["data"]["extra"] = merge_short_note_into_extra(current, short_note)
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def update_item(self, key: str, fields: dict[str, str], *, expected_version: int | None = None) -> None:
        """Update fields, optionally rejecting changes since a derived payload was prepared."""
        try:
            item = self._zot.item(key)
            if expected_version is not None and item.get("version") != expected_version:
                raise ZoteroWriteError(
                    f"Item '{key}' changed since it was read; refusing a stale update",
                    code="conflict",
                    retryable=False,
                )
            for field_name, value in fields.items():
                item["data"][field_name] = value
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def restore_from_trash(self, key: str) -> None:
        """Restore an item from trash by clearing its deleted flag."""
        try:
            item = self._zot.item(key)
            item["data"]["deleted"] = 0
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def upload_attachment(self, parent_key: str, file_path: Path) -> str:
        """Upload a file attachment to an existing item. Returns attachment key."""
        if not file_path.exists():
            raise ZoteroWriteError(f"File not found: {file_path}", code="validation_error", retryable=False)
        try:
            resp = self._zot.attachment_simple([str(file_path)], parentid=parent_key)
            if resp.get("success"):
                return str(resp["success"][0]["key"])
            if resp.get("unchanged"):
                return str(resp["unchanged"][0]["key"])
            if resp.get("failure"):
                msg = resp["failure"][0].get("message", "Upload failed")
                raise ZoteroWriteError(f"Attachment upload failed: {msg}", code="api_error", retryable=True)
            raise ZoteroWriteError("Unexpected empty response from attachment upload", code="api_error", retryable=True)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_item(self, key: str) -> None:
        """Move an item to the Zotero trash."""
        try:
            item = self._zot.item(key)
            self._zot.delete_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def add_tags(self, key: str, tags: list[str]) -> None:
        """Add tags to an item, keeping existing tag names."""
        try:
            item = self._zot.item(key)
            existing = [t["tag"] for t in item["data"].get("tags", [])]
            new_tags = [{"tag": t} for t in set(existing + tags)]
            item["data"]["tags"] = new_tags
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def remove_tags(self, key: str, tags: list[str]) -> None:
        """Remove tags from an item."""
        try:
            item = self._zot.item(key)
            item["data"]["tags"] = [t for t in item["data"].get("tags", []) if t["tag"] not in tags]
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def create_collection(self, name: str, parent_key: str | None = None) -> str:
        """Create a collection, optionally nested under a parent collection key."""
        try:
            payload = [{"name": name, "parentCollection": parent_key or False}]
            resp = self._zot.create_collections(payload)
            return self._check_response(resp)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def add_to_collection(self, item_key: str, collection_key: str) -> None:
        """Add an item to a collection without removing existing collection memberships."""
        try:
            self._zot.addto_collection(collection_key, self._zot.item(item_key))
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def move_to_collection(self, item_key: str, collection_key: str, source_collection_key: str | None = None) -> None:
        """Move an item to a collection.

        Without ``source_collection_key`` this preserves the historical behavior:
        add the item to the target collection and keep all existing memberships.
        When a source collection is provided, update the item membership list so
        the source is removed and the target is present.
        """
        if source_collection_key is None:
            self.add_to_collection(item_key, collection_key)
            return
        try:
            item = self._zot.item(item_key)
            data = item.setdefault("data", {})
            collections = [str(c) for c in data.get("collections", []) if c]
            if source_collection_key not in collections:
                raise ZoteroWriteError(
                    f"Item '{item_key}' is not in source collection '{source_collection_key}'",
                    code="validation_error",
                    retryable=False,
                )
            new_collections = [c for c in collections if c != source_collection_key]
            if collection_key not in new_collections:
                new_collections.append(collection_key)
            data["collections"] = new_collections
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_collection(self, key: str) -> None:
        """Delete a collection."""
        try:
            coll = self._zot.collection(key)
            self._zot.delete_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def rename_collection(self, key: str, new_name: str) -> None:
        """Rename a collection, preserving its parent and other fields."""
        try:
            coll = self._zot.collection(key)
            coll["data"]["name"] = new_name
            self._zot.update_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False)
        except HttpxHttpError as e:
            raise ZoteroWriteError(f"Network error: {e}", code="network_error", retryable=True) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e
