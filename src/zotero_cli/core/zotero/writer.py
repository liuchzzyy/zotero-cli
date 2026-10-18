"""Zotero Web API write layer (pyzotero 1.16 / httpx2 based).

ZoteroWriter lives here; error translation lives in writer_errors and the
Extra-field short-note merge in short_note_merge.

Delete semantics: Zotero's Web API DELETE permanently erases an item. Use the
trash restore path (raw PATCH deleted=0) only for items that were trashed by
Zotero Desktop; CLI deletions are irreversible.
"""

from __future__ import annotations

from pathlib import Path

import httpx2
from pyzotero import zotero
from pyzotero.zotero_errors import PyZoteroError, ResourceNotFoundError

from zotero_cli.core.zotero.short_note_merge import merge_short_note_into_extra
from zotero_cli.core.zotero.writer_errors import (
    ZoteroWriteError,
    _friendly_api_error,
    error_for_http_status,
    response_retry_after,
)

SYNC_REMINDER = "Change saved. Run Zotero sync to update local database."

API_TIMEOUT = 30.0  # seconds


class ZoteroWriter:
    def __init__(self, library_id: str, api_key: str, library_type: str = "user", timeout: float = API_TIMEOUT) -> None:
        self._zot = zotero.Zotero(library_id, library_type, api_key)
        if self._zot.client is not None:
            # pyzotero 1.16 ships an httpx2 client whose timeout must be a
            # float/httpx2.Timeout; an httpx.Timeout object is rejected deep in
            # httpcore with "'Timeout' object cannot be interpreted as an integer".
            self._zot.client.timeout = float(timeout)

    # --- plumbing ---------------------------------------------------------

    @staticmethod
    def _network_error(exc: BaseException) -> ZoteroWriteError:
        return ZoteroWriteError(
            f"Network error: {exc}",
            code="network_error",
            retryable=True,
            committed=True,
        )

    @staticmethod
    def _response_error(response: httpx2.Response) -> ZoteroWriteError:
        try:
            body = response.text.strip()[:300]
        except Exception:  # noqa: BLE001 - malformed/deleted response body
            body = ""
        return error_for_http_status(
            response.status_code,
            body,
            retry_after=response_retry_after(response),
        )

    def _item_url(self, key: str) -> str:
        return f"{self._zot.endpoint}/{self._zot.library_type}/{self._zot.library_id}/items/{key}"

    def _write_headers(self, *, version: int | None = None, json_body: bool = False) -> dict[str, str]:
        headers = dict(self._zot.default_headers())
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _check_response(self, resp: dict) -> str:
        """Return the created key from a write response or raise a typed error.

        Zotero write responses may carry the created object under
        successful[0].key, a key map under success[0], or a keyed object under
        unchanged[0]. pyzotero 1.16 surfaces all three shapes.
        """
        if not isinstance(resp, dict):
            raise ZoteroWriteError("Unexpected API response", code="api_error", retryable=True, committed=True)

        success_key: str | None = None
        successful = resp.get("successful") or {}
        if isinstance(successful, dict):
            entry = successful.get("0")
            if isinstance(entry, dict) and entry.get("key"):
                success_key = str(entry["key"])
        if success_key is None:
            success = resp.get("success") or {}
            if isinstance(success, dict):
                key = success.get("0")
                if isinstance(key, str) and key:
                    success_key = key
        if success_key is None:
            unchanged = resp.get("unchanged") or {}
            if isinstance(unchanged, dict):
                entry = unchanged.get("0")
                if isinstance(entry, dict) and entry.get("key"):
                    success_key = str(entry["key"])

        failed = resp.get("failed") or {}
        if isinstance(failed, dict) and failed:
            if success_key is not None:
                # Conflicting response: the write may have landed while the
                # server also reports a failure. Treat it as an unknown outcome
                # so callers reconcile instead of blindly retrying.
                raise ZoteroWriteError(
                    "Unexpected API response",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            failure = failed.get("0") or {}
            message = failure.get("message") if isinstance(failure, dict) else None
            raise ZoteroWriteError(
                f"API error: {message or 'Unknown API error'}",
                code="api_error",
                retryable=False,
                committed=False,
            )
        if success_key is not None:
            return success_key
        raise ZoteroWriteError("Unexpected API response", code="api_error", retryable=True, committed=True)

    # --- notes and items --------------------------------------------------

    def add_note(self, parent_key: str, content: str) -> str:
        """Create a child note under an item; returns the new note key."""
        try:
            try:
                template = self._zot.item_template("note")
            except Exception:  # noqa: BLE001 - fall back to a minimal note template
                template = {"itemType": "note", "note": "", "tags": [], "collections": [], "relations": {}}
            template["note"] = content
            template["parentItem"] = parent_key
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except ResourceNotFoundError:
            raise ZoteroWriteError(
                f"Parent item '{parent_key}' not found", code="not_found", retryable=False, committed=False
            )
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
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

        extra_fields is merged into the item template before posting, used by
        callers that resolved DOI metadata via Crossref. URL-only items get the
        URL as their title unless the caller supplied one, so the new item is
        identifiable before any readback/sync.
        """
        if not doi and not url:
            raise ValueError("Either doi or url must be provided")
        try:
            if doi:
                template = self._zot.item_template("journalArticle")
                template["DOI"] = doi
                if extra_fields:
                    template.update(extra_fields)
            else:
                template = self._zot.item_template("webpage")
                template["url"] = url
                if extra_fields:
                    template.update(extra_fields)
                if not template.get("title"):
                    template["title"] = url
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
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
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
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
                    committed=False,
                )
            for field_name, value in fields.items():
                item["data"][field_name] = value
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def restore_from_trash(self, key: str) -> None:
        """Restore a Zotero Desktop trashed item by PATCHing deleted=0.

        pyzotero 1.16's update_item rejects the read-only deleted field, so use
        the raw Web API PATCH with the item's version as a precondition. This
        only works for items that still exist in the trash; CLI delete is
        permanent and cannot be restored.
        """
        try:
            item = self._zot.item(key)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e
        version = item.get("version")
        if not isinstance(version, int):
            raise ZoteroWriteError(
                f"Item '{key}' has no version; refusing an unguarded restore",
                code="conflict",
                retryable=False,
                committed=False,
            )
        client = self._zot.client
        if client is None:
            raise ZoteroWriteError("Zotero client is not initialized", code="api_error", retryable=True, committed=True)
        try:
            response = client.patch(
                self._item_url(key),
                headers=self._write_headers(version=version, json_body=True),
                json={"deleted": 0},
            )
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        if response.status_code >= 300:
            raise self._response_error(response)

    def upload_attachment(self, parent_key: str, file_path: Path) -> str:
        """Upload a file attachment to an existing item. Returns attachment key."""
        if not file_path.exists():
            raise ZoteroWriteError(
                f"File not found: {file_path}", code="validation_error", retryable=False, committed=False
            )
        try:
            resp = self._zot.attachment_simple([str(file_path)], parentid=parent_key)
            if not isinstance(resp, dict):
                raise ZoteroWriteError(
                    "Unexpected empty response from attachment upload",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            for field in ("success", "unchanged"):
                entries = resp.get(field) or []
                if entries:
                    key = entries[0].get("key")
                    if key:
                        return str(key)
            failures = resp.get("failure")
            if isinstance(failures, list) and failures:
                failure = failures[0] if isinstance(failures[0], dict) else {}
                reason = failure.get("error")
                if isinstance(reason, dict) and reason.get("message"):
                    raise ZoteroWriteError(
                        f"Attachment upload failed: {reason['message']}",
                        code="api_error",
                        retryable=False,
                        committed=False,
                    )
                raise ZoteroWriteError(
                    f"Attachment upload failed: {failure.get('message', 'Upload failed')}",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            raise ZoteroWriteError(
                "Unexpected empty response from attachment upload",
                code="api_error",
                retryable=True,
                committed=True,
            )
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_item(self, key: str) -> None:
        """Permanently delete an item via the Web API DELETE endpoint."""
        try:
            item = self._zot.item(key)
            self._zot.delete_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
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
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def remove_tags(self, key: str, tags: list[str]) -> None:
        """Remove tags from an item."""
        try:
            item = self._zot.item(key)
            item["data"]["tags"] = [t for t in item["data"].get("tags", []) if t["tag"] not in tags]
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    # --- collections ------------------------------------------------------

    def create_collection(self, name: str, parent_key: str | None = None) -> str:
        """Create a collection, optionally nested under a parent collection key."""
        try:
            payload = [{"name": name, "parentCollection": parent_key or False}]
            resp = self._zot.create_collections(payload)
            return self._check_response(resp)
        except ResourceNotFoundError:
            raise ZoteroWriteError(
                f"Parent collection '{parent_key}' not found", code="not_found", retryable=False, committed=False
            )
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def add_to_collection(self, item_key: str, collection_key: str) -> None:
        """Add an item to a collection without removing existing collection memberships."""
        try:
            self._zot.addto_collection(collection_key, self._zot.item(item_key))
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def move_to_collection(self, item_key: str, collection_key: str, source_collection_key: str | None = None) -> None:
        """Move an item to a collection.

        Without source_collection_key this preserves the historical behavior:
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
                    committed=False,
                )
            new_collections = [c for c in collections if c != source_collection_key]
            if collection_key not in new_collections:
                new_collections.append(collection_key)
            data["collections"] = new_collections
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def set_item_collections(self, item_key: str, collection_keys: list[str]) -> None:
        """Replace an item's collection membership with exactly the given keys."""
        try:
            item = self._zot.item(item_key)
            item["data"]["collections"] = sorted({str(key) for key in collection_keys if key})
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_collection(self, key: str) -> None:
        """Permanently delete a collection (nested collections are removed too)."""
        try:
            coll = self._zot.collection(key)
            self._zot.delete_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def rename_collection(self, key: str, new_name: str) -> None:
        """Rename a collection, preserving its parent and other fields."""
        try:
            coll = self._zot.collection(key)
            coll["data"]["name"] = new_name
            self._zot.update_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e
