"""Exercise writer payloads and errors through an offline Zotero HTTP boundary."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from pyzotero import zotero
from pyzotero.zotero_errors import PyZoteroError, UnsupportedParamsError, UserNotAuthorisedError

from zotero_cli.core.zotero import writer as writer_module
from zotero_cli.core.zotero.short_note_merge import merge_short_note_into_extra
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError

ITEM_KEY = "ITEM0001"
COLLECTION_KEY = "COLL0001"
CREATED_KEY = "NEW00001"


class OfflineZotero:
    """Serve deterministic API documents; reject every unrecognised request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.item: dict[str, Any] = {
            "key": ITEM_KEY,
            "version": 17,
            "library": {"type": "user", "id": 123},
            "links": {},
            "meta": {},
            "data": {
                "itemType": "journalArticle",
                "title": "Original title",
                "DOI": "10.1234/original",
                "extra": "funder: Example\ntranslated-title: 标题",
                "tags": [{"tag": "keep", "type": 1}, {"tag": "remove", "type": 0}],
                "collections": ["SOURCE01", "KEEP0001"],
                "deleted": 1,
            },
        }
        self.collection: dict[str, Any] = {
            "key": COLLECTION_KEY,
            "version": 9,
            "data": {"name": "Original collection", "parentCollection": "PARENT01"},
        }
        self.create_response: dict[str, Any] = {"successful": {"0": {"key": CREATED_KEY}}}
        self.failure: Exception | None = None
        self.get_status = 200
        self.write_status = 204
        self.create_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        path = request.url.path
        if request.method == "GET" and self.get_status != 200:
            return httpx.Response(self.get_status, text="Access denied or resource missing")
        if request.method == "GET" and path == "/items/new":
            item_type = request.url.params["itemType"]
            template: dict[str, Any] = {"itemType": item_type, "tags": [], "collections": [], "relations": {}}
            if item_type == "attachment":
                template["linkMode"] = request.url.params["linkMode"]
            return httpx.Response(200, json=template)
        if request.method == "GET" and path == "/itemFields":
            return httpx.Response(200, json=[{"field": field} for field in self.item["data"]])
        if request.method == "GET" and path == f"/users/123/items/{ITEM_KEY}":
            return httpx.Response(200, json=self.item)
        if request.method == "GET" and path == f"/users/123/collections/{COLLECTION_KEY}":
            return httpx.Response(200, json=self.collection)
        if request.method == "POST" and path in {"/users/123/items", "/users/123/collections"}:
            return httpx.Response(self.create_status, json=self.create_response)
        if request.method == "POST" and path == f"/users/123/items/{CREATED_KEY}/file":
            return httpx.Response(200, json={"exists": 1})
        if request.method in {"PATCH", "PUT", "DELETE"} and path in {
            f"/users/123/items/{ITEM_KEY}",
            f"/users/123/collections/{COLLECTION_KEY}",
        }:
            return httpx.Response(self.write_status, text="Version conflict" if self.write_status != 204 else "")
        raise AssertionError(f"Unexpected offline request: {request.method} {request.url}")

    def writes(self, method: str) -> list[httpx.Request]:
        return [request for request in self.requests if request.method == method]


@pytest.fixture
def offline_writer(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[ZoteroWriter, OfflineZotero]]:
    api = OfflineZotero()
    with httpx.Client(transport=httpx.MockTransport(api), trust_env=False) as client:
        remote = zotero.Zotero("123", "user", "offline-test-key", client=client)
        constructor = Mock(return_value=remote)
        monkeypatch.setattr(writer_module.zotero, "Zotero", constructor)
        writer = ZoteroWriter("123", "offline-test-key", timeout=4.5)
        constructor.assert_called_once_with("123", "user", "offline-test-key")
        yield writer, api


def invoke_write(writer: ZoteroWriter, operation: str, file_path: Path) -> object:
    """Use real public methods, sharing only their input fixtures."""
    calls: dict[str, Callable[[], object]] = {
        "add_note": lambda: writer.add_note(ITEM_KEY, "<p>New note</p>"),
        "add_item": lambda: writer.add_item(doi="10.1234/new"),
        "update_short_note": lambda: writer.update_short_note(ITEM_KEY, "New summary"),
        "update_item": lambda: writer.update_item(ITEM_KEY, {"title": "New title"}),
        "restore_from_trash": lambda: writer.restore_from_trash(ITEM_KEY),
        "upload_attachment": lambda: writer.upload_attachment(ITEM_KEY, file_path),
        "delete_item": lambda: writer.delete_item(ITEM_KEY),
        "add_tags": lambda: writer.add_tags(ITEM_KEY, ["new"]),
        "remove_tags": lambda: writer.remove_tags(ITEM_KEY, ["remove"]),
        "create_collection": lambda: writer.create_collection("New collection"),
        "add_to_collection": lambda: writer.add_to_collection(ITEM_KEY, "TARGET01"),
        "move_to_collection": lambda: writer.move_to_collection(ITEM_KEY, "TARGET01", "SOURCE01"),
        "delete_collection": lambda: writer.delete_collection(COLLECTION_KEY),
        "rename_collection": lambda: writer.rename_collection(COLLECTION_KEY, "Renamed collection"),
    }
    return calls[operation]()


ALL_OPERATIONS = (
    "add_note",
    "add_item",
    "update_short_note",
    "update_item",
    "restore_from_trash",
    "upload_attachment",
    "delete_item",
    "add_tags",
    "remove_tags",
    "create_collection",
    "add_to_collection",
    "move_to_collection",
    "delete_collection",
    "rename_collection",
)
MUTATION_OPERATIONS = tuple(
    name for name in ALL_OPERATIONS if name not in {"add_note", "add_item", "upload_attachment", "create_collection"}
)
NOT_FOUND_OPERATIONS = tuple(
    name for name in ALL_OPERATIONS if name not in {"add_item", "upload_attachment", "create_collection"}
)


@pytest.fixture
def attachment_file(tmp_path: Path) -> Path:
    pdf = tmp_path / "paper with spaces.pdf"
    pdf.write_bytes(b"%PDF-1.4\nOffline attachment fixture\n%%EOF\n")
    return pdf


def test_constructor_applies_requested_timeout(offline_writer: tuple[ZoteroWriter, OfflineZotero]) -> None:
    writer, _ = offline_writer
    assert writer._zot.client.timeout == httpx.Timeout(4.5)


@pytest.mark.parametrize("template_status", [200, 400], ids=["normal-template", "template-service-fallback"])
def test_add_note_posts_content_and_parent_after_template_lookup(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], template_status: int
) -> None:
    writer, api = offline_writer
    api.get_status = template_status
    content = "<h2>Results</h2><p>Keep this rich-text note unchanged.</p>"
    assert writer.add_note(ITEM_KEY, content) == CREATED_KEY
    [request] = api.writes("POST")
    assert json.loads(request.content) == [
        {"itemType": "note", "tags": [], "collections": [], "relations": {}, "note": content, "parentItem": ITEM_KEY}
    ]


@pytest.mark.parametrize(
    ("doi", "url", "metadata", "item_type"),
    [
        ("10.1234/new", None, {"title": "Resolved title", "collections": [COLLECTION_KEY]}, "journalArticle"),
        (None, "https://example.invalid/paper", {"title": "Web title"}, "webpage"),
        (None, "https://example.invalid/paper", None, "webpage"),
    ],
    ids=["resolved-doi", "webpage-metadata", "bare-webpage"],
)
def test_add_item_posts_identifiers_and_metadata(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
    doi: str | None,
    url: str | None,
    metadata: dict[str, object] | None,
    item_type: str,
) -> None:
    writer, api = offline_writer
    original_metadata = copy.deepcopy(metadata)
    assert writer.add_item(doi=doi, url=url, extra_fields=metadata) == CREATED_KEY
    payload = json.loads(api.writes("POST")[0].content)
    expected = {"itemType": item_type, "tags": [], "collections": [], "relations": {}}
    expected["DOI" if doi else "url"] = doi or url
    expected.update(metadata or {})
    assert payload == [expected]
    assert metadata == original_metadata


def test_empty_item_input_fails_before_network(offline_writer: tuple[ZoteroWriter, OfflineZotero]) -> None:
    writer, api = offline_writer
    with pytest.raises(ValueError, match="provided"):
        writer.add_item()
    assert api.requests == []


@pytest.mark.parametrize(
    ("response", "message", "retryable"),
    [
        ({"failed": {"0": {"message": "Invalid title"}}}, "Invalid title", False),
        ({"failed": {"0": {}}}, "Unknown API error", False),
        ({}, "Unexpected API response", True),
        ({"successful": {"1": {"key": "WRONG001"}}}, "Unexpected API response", True),
    ],
)
def test_creation_response_must_contain_requested_item(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
    response: dict[str, Any],
    message: str,
    retryable: bool,
) -> None:
    writer, api = offline_writer
    api.create_response = response
    with pytest.raises(ZoteroWriteError, match=message) as caught:
        writer.add_item(doi="10.1234/new")
    assert caught.value.code == "api_error"
    assert caught.value.retryable is retryable


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ("", "short-note: New summary"),
        ("funder: Example\n\n\n", "funder: Example\nshort-note: New summary"),
        (
            "SHORT-NOTE: stale\ntranslated-title: 标题\n  short-note: duplicate  \n\n",
            "translated-title: 标题\nshort-note: New summary",
        ),
    ],
)
def test_short_note_merge_preserves_unrelated_lines(extra: str, expected: str) -> None:
    assert merge_short_note_into_extra(extra, "  New summary  ") == expected


@pytest.mark.parametrize(
    ("operation", "changed_field", "expected"),
    [
        ("update_item", "title", "New title"),
        ("restore_from_trash", "deleted", 0),
        ("update_short_note", "extra", "funder: Example\ntranslated-title: 标题\nshort-note: New summary"),
    ],
)
def test_item_updates_preserve_other_metadata_and_version(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
    attachment_file: Path,
    operation: str,
    changed_field: str,
    expected: object,
) -> None:
    writer, api = offline_writer
    original_data = copy.deepcopy(api.item["data"])
    invoke_write(writer, operation, attachment_file)
    [request] = api.writes("PATCH")
    assert request.headers["If-Unmodified-Since-Version"] == "17"
    assert json.loads(request.content) == {**original_data, changed_field: expected}
    assert api.item["data"] == original_data


@pytest.mark.parametrize("extra", [None, ""])
def test_update_short_note_handles_empty_extra(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], extra: str | None
) -> None:
    writer, api = offline_writer
    api.item["data"]["extra"] = extra
    writer.update_short_note(ITEM_KEY, "Summary")
    assert json.loads(api.writes("PATCH")[0].content)["extra"] == "short-note: Summary"


def test_add_tags_deduplicates_without_losing_existing_names(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
) -> None:
    writer, api = offline_writer
    writer.add_tags(ITEM_KEY, ["keep", "new", "new"])
    payload = json.loads(api.writes("PATCH")[0].content)
    names = [tag["tag"] for tag in payload["tags"]]
    assert set(names) == {"keep", "remove", "new"}
    assert len(names) == 3
    assert payload["DOI"] == api.item["data"]["DOI"]


def test_remove_tags_preserves_retained_tag_metadata(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
) -> None:
    writer, api = offline_writer
    writer.remove_tags(ITEM_KEY, ["remove", "absent"])
    payload = json.loads(api.writes("PATCH")[0].content)
    assert payload["tags"] == [{"tag": "keep", "type": 1}]
    assert payload["title"] == "Original title"


@pytest.mark.parametrize("operation", ["add_tags", "remove_tags"])
def test_tag_operations_handle_missing_tag_list(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], operation: str
) -> None:
    writer, api = offline_writer
    del api.item["data"]["tags"]
    getattr(writer, operation)(ITEM_KEY, ["new"])
    payload = json.loads(api.writes("PATCH")[0].content)
    assert payload["tags"] == ([{"tag": "new"}] if operation == "add_tags" else [])


@pytest.mark.parametrize("parent", [None, "PARENT01"])
def test_create_collection_posts_explicit_parent(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], parent: str | None
) -> None:
    writer, api = offline_writer
    assert writer.create_collection("New collection", parent) == CREATED_KEY
    [request] = api.writes("POST")
    assert request.url.path == "/users/123/collections"
    assert json.loads(request.content) == [{"name": "New collection", "parentCollection": parent or False}]


@pytest.mark.parametrize("operation", ["delete_item", "delete_collection"])
def test_delete_uses_fetched_resource_version(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path, operation: str
) -> None:
    writer, api = offline_writer
    invoke_write(writer, operation, attachment_file)
    [request] = api.writes("DELETE")
    expected = api.item if operation == "delete_item" else api.collection
    assert request.url.path.endswith(expected["key"])
    assert request.headers["If-Unmodified-Since-Version"] == str(expected["version"])


def test_rename_collection_preserves_parent_and_version(offline_writer: tuple[ZoteroWriter, OfflineZotero]) -> None:
    writer, api = offline_writer
    writer.rename_collection(COLLECTION_KEY, "Renamed collection")
    [request] = api.writes("PUT")
    assert request.headers["If-Unmodified-Since-Version"] == "9"
    assert json.loads(request.content) == {
        **api.collection,
        "data": {**api.collection["data"], "name": "Renamed collection"},
    }


@pytest.mark.parametrize("use_move_alias", [False, True])
def test_adding_collection_membership_preserves_existing_memberships(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], use_move_alias: bool
) -> None:
    writer, api = offline_writer
    method = writer.move_to_collection if use_move_alias else writer.add_to_collection
    method(ITEM_KEY, "TARGET01")
    assert json.loads(api.writes("PATCH")[0].content)["collections"] == ["SOURCE01", "KEEP0001", "TARGET01"]


def test_move_to_existing_target_does_not_duplicate_membership(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
) -> None:
    writer, api = offline_writer
    api.item["data"]["collections"] = ["SOURCE01", "TARGET01", "KEEP0001"]
    writer.move_to_collection(ITEM_KEY, "TARGET01", "SOURCE01")
    assert json.loads(api.writes("PATCH")[0].content)["collections"] == ["TARGET01", "KEEP0001"]


def test_move_missing_source_rejects_write(offline_writer: tuple[ZoteroWriter, OfflineZotero]) -> None:
    writer, api = offline_writer
    original = copy.deepcopy(api.item)
    with pytest.raises(ZoteroWriteError, match="not in source collection") as caught:
        writer.move_to_collection(ITEM_KEY, "TARGET01", "MISSING1")
    assert caught.value.code == "validation_error"
    assert caught.value.retryable is False
    assert not api.writes("PATCH")
    assert api.item == original


def test_upload_existing_attachment_runs_real_file_registration(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path
) -> None:
    writer, api = offline_writer
    api.create_response = {"success": {"0": CREATED_KEY}, "failed": {}, "unchanged": {}}
    assert writer.upload_attachment(ITEM_KEY, attachment_file) == CREATED_KEY
    registration, authorization = api.writes("POST")
    payload = json.loads(registration.content)[0]
    assert payload["parentItem"] == ITEM_KEY
    assert payload["filename"] == str(attachment_file)
    assert payload["title"] == attachment_file.name
    assert payload["contentType"] == "application/pdf"
    form = httpx.QueryParams(authorization.content.decode())
    assert form["filename"] == attachment_file.name
    assert form["filesize"] == str(attachment_file.stat().st_size)
    assert len(form["md5"]) == 32


@pytest.mark.parametrize(
    ("response", "expected_key", "message"),
    [
        ({"success": [{"key": CREATED_KEY}]}, CREATED_KEY, None),
        ({"failure": [{"message": "Quota exhausted"}]}, None, "Quota exhausted"),
        ({"failure": [{}]}, None, "Upload failed"),
        ({}, None, "Unexpected empty response"),
    ],
)
def test_attachment_result_and_failure_envelopes(
    offline_writer: tuple[ZoteroWriter, OfflineZotero],
    attachment_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, Any],
    expected_key: str | None,
    message: str | None,
) -> None:
    writer, _ = offline_writer
    upload = Mock(return_value=response)
    monkeypatch.setattr(writer._zot, "attachment_simple", upload)
    if expected_key is not None:
        assert writer.upload_attachment(ITEM_KEY, attachment_file) == expected_key
    else:
        with pytest.raises(ZoteroWriteError, match=message) as caught:
            writer.upload_attachment(ITEM_KEY, attachment_file)
        assert caught.value.code == "api_error"
        assert caught.value.retryable is True
    upload.assert_called_once_with([str(attachment_file)], parentid=ITEM_KEY)


def test_missing_attachment_fails_before_network(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], tmp_path: Path
) -> None:
    writer, api = offline_writer
    with pytest.raises(ZoteroWriteError, match="File not found") as caught:
        writer.upload_attachment(ITEM_KEY, tmp_path / "missing.pdf")
    assert caught.value.code == "validation_error"
    assert caught.value.retryable is False
    assert api.requests == []


@pytest.mark.parametrize("operation", ALL_OPERATIONS)
def test_http_transport_errors_are_retryable_for_every_operation(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path, operation: str
) -> None:
    writer, api = offline_writer
    api.failure = httpx.ReadTimeout("Offline read timed out")
    with pytest.raises(ZoteroWriteError, match="Network error") as caught:
        invoke_write(writer, operation, attachment_file)
    assert caught.value.code == "network_error"
    assert caught.value.retryable is True
    assert caught.value.__cause__ is api.failure


@pytest.mark.parametrize("operation", NOT_FOUND_OPERATIONS)
def test_missing_resources_are_nonretryable(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path, operation: str
) -> None:
    writer, api = offline_writer
    api.get_status = 404
    api.create_status = 404
    with pytest.raises(ZoteroWriteError, match="not found") as caught:
        invoke_write(writer, operation, attachment_file)
    assert caught.value.code == "not_found"
    assert caught.value.retryable is False


@pytest.mark.parametrize("operation", ALL_OPERATIONS)
def test_api_exceptions_do_not_escape_writer_boundary(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path, operation: str
) -> None:
    writer, api = offline_writer
    api.failure = PyZoteroError("Service rejected operation")
    with pytest.raises(ZoteroWriteError, match="Service rejected operation") as caught:
        invoke_write(writer, operation, attachment_file)
    assert caught.value.__cause__ is api.failure


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (UserNotAuthorisedError("Forbidden"), "write permissions"),
        (UnsupportedParamsError("Invalid user ID"), "numeric userID"),
        (UnsupportedParamsError("Unsupported field"), "Bad request"),
    ],
)
def test_api_errors_include_actionable_diagnostics(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], error: PyZoteroError, message: str
) -> None:
    writer, api = offline_writer
    api.failure = error
    with pytest.raises(ZoteroWriteError, match=message):
        writer.add_item(doi="10.1234/new")


@pytest.mark.parametrize("operation", MUTATION_OPERATIONS)
def test_rejected_http_mutations_must_not_report_success(
    offline_writer: tuple[ZoteroWriter, OfflineZotero], attachment_file: Path, operation: str
) -> None:
    """A rejected version-guarded HTTP write must surface as a writer error."""
    writer, api = offline_writer
    api.write_status = 412
    with pytest.raises(ZoteroWriteError):
        invoke_write(writer, operation, attachment_file)


def test_forbidden_create_reports_auth_invalid(offline_writer: tuple[ZoteroWriter, OfflineZotero]) -> None:
    writer, api = offline_writer
    api.create_status = 403
    with pytest.raises(ZoteroWriteError) as caught:
        writer.add_item(doi="10.1234/new")
    assert caught.value.code == "auth_invalid"
    assert caught.value.retryable is False
