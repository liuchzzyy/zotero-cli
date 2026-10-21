"""Offline CLI-to-service coverage with real files, readers, and retry storage."""

from __future__ import annotations

import json
import shutil
import socket
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import httpx2
import pytest
from click.testing import Result
from tests.support import FIXTURES_DIR, invoke_cli, parse_json_output

from zotero_cli import config
from zotero_cli.core.zotero import idempotency
from zotero_cli.core.zotero import publication_status as semantic_scholar
from zotero_cli.core.zotero.writer import SYNC_REMINDER

WRITE_ENV = {"ZOT_LIBRARY_ID": "12345", "ZOT_API_KEY": "offline-write-key"}


@dataclass
class ServiceSandbox:
    root: Path
    db_path: Path
    pdf: Path
    zotero: MagicMock
    constructor: MagicMock
    responses: dict[str, tuple[int, dict[str, Any]] | Exception]
    requests: list[httpx.Request] = field(default_factory=list)
    clients: list[httpx.Client] = field(default_factory=list)

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def env(self) -> dict[str, str]:
        return {"ZOT_DATA_DIR": str(self.db_path.parent), **WRITE_ENV}


def _set_field(conn: sqlite3.Connection, item_id: int, name: str, value: str) -> None:
    row = conn.execute("SELECT fieldID FROM fields WHERE fieldName = ?", (name,)).fetchone()
    if row is None:
        field_id = conn.execute("INSERT INTO fields (fieldName) VALUES (?)", (name,)).lastrowid
    else:
        field_id = row[0]
    value_id = conn.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,)).lastrowid
    conn.execute(
        "INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
        (item_id, field_id, value_id),
    )


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServiceSandbox]:
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    db_path = data_dir / "zotero.sqlite"
    shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
    pdf = tmp_path / "supplement 研究.pdf"
    pdf.write_bytes(b"%PDF-1.4 offline fixture\n")
    isolated_config = tmp_path / "offline.toml"
    monkeypatch.setattr(config.paths, "config_file_path", lambda: isolated_config)
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setenv("ZOT_PROFILE", "")

    def reject_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("A service test attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    zotero = MagicMock()
    zotero.attachment_simple.return_value = {
        "success": [{"key": "NEWATT01"}],
        "failure": [],
        "unchanged": [],
    }
    zotero.item.side_effect = lambda key: {
        "key": key,
        "version": 1,
        "data": {"key": key, "title": f"Original {key}", "DOI": "old", "extra": "Keep me"},
    }
    constructor = MagicMock(return_value=zotero)
    monkeypatch.setattr("zotero_cli.core.zotero.writer.zotero.Zotero", constructor)
    result = ServiceSandbox(tmp_path, db_path, pdf, zotero, constructor, {})
    real_client = httpx.Client

    def respond(request: httpx.Request) -> httpx.Response:
        result.requests.append(request)
        api_id = request.url.path.split("/paper/", 1)[1]
        response = result.responses.get(api_id, (404, {}))
        if isinstance(response, Exception):
            raise response
        status, payload = response
        return httpx.Response(status, json=payload, request=request)

    def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        client = real_client(*args, transport=httpx.MockTransport(respond), **kwargs)
        result.clients.append(client)
        return client

    monkeypatch.setattr(semantic_scholar.httpx, "Client", client_factory)
    monkeypatch.setattr(semantic_scholar.time, "sleep", lambda seconds: None)
    yield result
    assert all(client.is_closed for client in result.clients)


def _seed_preprints(sandbox: ServiceSandbox) -> None:
    with sqlite3.connect(sandbox.db_path) as conn:
        _set_field(conn, 1, "url", "https://arxiv.org/abs/2401.00001v2")
        _set_field(conn, 2, "DOI", "10.1101/2024.01.02.000002")
        _set_field(conn, 3, "extra", "arXiv:2401.00003")
        _set_field(conn, 6, "url", "https://www.medrxiv.org/content/10.1101/2024.01.04.000004v1")
    sandbox.responses.update(
        {
            "arXiv:2401.00001": (
                200,
                {
                    "title": "Published model",
                    "venue": "Offline Conference",
                    "journal": {"name": "Fallback Journal"},
                    "externalIds": {"DOI": "10.1000/published-one"},
                    "publicationDate": "2025-04-01",
                },
            ),
            "DOI:10.1101/2024.01.02.000002": (
                200,
                {
                    "title": "Published biology",
                    "publicationVenue": {"name": "Offline Biology"},
                    "externalIds": {"DOI": "10.1000/published-two"},
                },
            ),
            "arXiv:2401.00003": (
                200,
                {
                    "title": "Still a preprint",
                    "venue": "arXiv",
                    "externalIds": {"DOI": "10.48550/arXiv.2401.00003"},
                },
            ),
        }
    )


def _invoke(sandbox: ServiceSandbox, args: list[str], *, json_output: bool = True, credentials: bool = True) -> Result:
    env = {"ZOT_DATA_DIR": str(sandbox.db_path.parent)}
    if credentials:
        env.update(WRITE_ENV)
    return invoke_cli(args, json_output=json_output, env=env)


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_attach_dry_run_with_no_credentials_never_constructs_writer(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    result = _invoke(
        sandbox,
        ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf), "--dry-run", "--idempotency-key", "preview"],
        json_output=json_output,
        credentials=False,
    )
    assert result.exit_code == 0, result.output
    sandbox.constructor.assert_not_called()
    assert not sandbox.state.exists()
    assert sandbox.pdf.read_bytes() == b"%PDF-1.4 offline fixture\n"
    if json_output:
        payload = parse_json_output(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would"] == {
            "parent": "ATTN001",
            "file": str(sandbox.pdf),
            "size_bytes": sandbox.pdf.stat().st_size,
        }
    else:
        assert "[dry-run] Would upload" in result.stdout
        assert str(sandbox.pdf.stat().st_size) + " bytes" in result.stdout


@pytest.mark.parametrize("missing", ["ZOT_LIBRARY_ID", "ZOT_API_KEY"])
def test_attach_incomplete_credentials_fail_before_service_or_cache(sandbox: ServiceSandbox, missing: str) -> None:
    result = invoke_cli(
        ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf), "--idempotency-key", "no-auth"],
        json_output=True,
        env={**sandbox.env, missing: ""},
    )
    assert result.exit_code == 2
    payload = parse_json_output(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "auth_missing"
    assert "config init" in payload["error"]["hint"]
    sandbox.constructor.assert_not_called()
    assert not sandbox.state.exists()


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_attach_success_with_credentials_preserves_output_and_sync_contract(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    result = _invoke(sandbox, ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf)], json_output=json_output)
    assert result.exit_code == 0, result.output
    sandbox.constructor.assert_called_once_with("12345", "user", "offline-write-key")
    sandbox.zotero.attachment_simple.assert_called_once_with([str(sandbox.pdf)], parentid="ATTN001")
    assert "offline-write-key" not in result.output
    if json_output:
        payload = parse_json_output(result.output)
        assert payload["data"] == {
            "attachment_key": "NEWATT01",
            "parent_key": "ATTN001",
            "file": str(sandbox.pdf),
            "sync_required": True,
        }
        assert payload["next"] == ["zot zotero read ATTN001"]
        assert result.stderr == ""
    else:
        assert "Attachment uploaded: NEWATT01" in result.stdout
        assert SYNC_REMINDER in result.stderr


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_attach_retry_replays_real_sqlite_cache_without_another_upload(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    args = ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf), "--idempotency-key", "retry-once"]
    first = _invoke(sandbox, args)
    second = _invoke(sandbox, args, json_output=json_output)
    assert first.exit_code == second.exit_code == 0, first.output + second.output
    sandbox.constructor.assert_called_once()
    sandbox.zotero.attachment_simple.assert_called_once()
    with sqlite3.connect(sandbox.state / "idempotency.db") as conn:
        rows = conn.execute("SELECT scope, key, value FROM cache").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "retry-once"
    assert rows[0][0].startswith("attach:user:12345:")
    assert "offline-write-key" not in rows[0][0]
    assert json.loads(rows[0][2]) == parse_json_output(first.output)
    if json_output:
        assert parse_json_output(second.output) == parse_json_output(first.output)
    else:
        assert "NEWATT01 (cached)" in second.stdout
        assert SYNC_REMINDER not in second.output


@pytest.mark.parametrize("change", ["contents", "parent", "library"])
def test_attach_retry_key_is_bound_to_contents_parent_and_library(sandbox: ServiceSandbox, change: str) -> None:
    args = ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf), "--idempotency-key", "shared-retry"]
    first = _invoke(sandbox, args)
    env = sandbox.env
    if change == "contents":
        sandbox.pdf.write_bytes(b"%PDF-1.4 changed offline fixture\n")
    elif change == "parent":
        args[2] = "BERT002"
    else:
        env = {**env, "ZOT_LIBRARY_ID": "67890"}
    second = invoke_cli(args, json_output=True, env=env)
    assert first.exit_code == second.exit_code == 0, first.output + second.output
    assert sandbox.zotero.attachment_simple.call_count == 2
    with sqlite3.connect(sandbox.state / "idempotency.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 2


def test_attach_group_credentials_use_public_group_id(sandbox: ServiceSandbox) -> None:
    result = _invoke(sandbox, ["--library", "group:9876", "zotero", "attach", "GROUP001", "--file", str(sandbox.pdf)])
    assert result.exit_code == 0, result.output
    sandbox.constructor.assert_called_once_with("9876", "group", "offline-write-key")
    sandbox.zotero.attachment_simple.assert_called_once_with([str(sandbox.pdf)], parentid="GROUP001")


@pytest.mark.parametrize("failure", ["network", "upload", "empty"])
def test_attach_failure_keeps_reservation_for_unknown_outcome(sandbox: ServiceSandbox, failure: str) -> None:
    if failure == "network":
        sandbox.zotero.attachment_simple.side_effect = httpx2.ConnectError("offline upload disconnected")
        expected_code, expected_exit = "network_error", 5
    else:
        sandbox.zotero.attachment_simple.return_value = {
            "success": [],
            "unchanged": [],
            "failure": [{"message": "offline upload rejected"}] if failure == "upload" else [],
        }
        expected_code, expected_exit = "api_error", 1
    args = ["zotero", "attach", "ATTN001", "--file", str(sandbox.pdf), "--idempotency-key", "retry-failed"]
    failed = _invoke(sandbox, args)
    assert failed.exit_code == expected_exit, failed.output
    error = parse_json_output(failed.output)["error"]
    assert error["code"] == expected_code
    # M-04: the idempotency key is reserved before the upload. An unknown outcome
    # keeps the reservation, so the failure is not retryable with the same key.
    assert error["retryable"] is False
    assert "outcome is unknown" in error["hint"].lower()
    assert sandbox.zotero.attachment_simple.call_count == 1
    with sqlite3.connect(sandbox.state / "idempotency.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
    sandbox.zotero.attachment_simple.side_effect = None
    sandbox.zotero.attachment_simple.return_value = {"success": [{"key": "RETRY001"}]}
    retried = _invoke(sandbox, args)
    assert retried.exit_code == 6, retried.output
    assert parse_json_output(retried.output)["error"]["code"] == "conflict"
    # The reservation is kept: no second upload happens for the same key.
    assert sandbox.zotero.attachment_simple.call_count == 1


def test_attach_missing_path_is_rejected_before_credentials(sandbox: ServiceSandbox) -> None:
    result = _invoke(sandbox, ["zotero", "attach", "ATTN001", "--file", str(sandbox.root / "missing.pdf")])
    assert result.exit_code != 0
    assert "does not exist" in result.output
    sandbox.constructor.assert_not_called()
    assert not sandbox.state.exists()


@pytest.mark.parametrize("mode", ["upload", "dry-run", "idempotent-upload"])
def test_attach_directory_is_rejected_before_any_upload(sandbox: ServiceSandbox, mode: str) -> None:
    directory = sandbox.root / "not-a-file.pdf"
    directory.mkdir()
    args = ["zotero", "attach", "ATTN001", "--file", str(directory)]
    if mode == "dry-run":
        args.append("--dry-run")
    elif mode == "idempotent-upload":
        args.extend(["--idempotency-key", "reject-directory"])
    result = _invoke(sandbox, args)
    assert result.exit_code != 0, "A directory must not be accepted as an attachment file"
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    sandbox.constructor.assert_not_called()
    assert not sandbox.state.exists()


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_empty_collection_stops_without_clients(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    result = _invoke(
        sandbox,
        ["update-status", "--collection", "Transformers", "--apply"],
        json_output=json_output,
        credentials=False,
    )
    assert result.exit_code == 0, result.output
    assert sandbox.clients == []
    sandbox.constructor.assert_not_called()
    if json_output:
        payload = parse_json_output(result.output)
        assert payload["meta"]["count"] == 0
        assert payload["data"] == {
            "results": [],
            "published": 0,
            "checked": 0,
            "updated": 0,
            "sync_required": False,
        }
    else:
        assert "No preprints found." in result.stdout


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_mixed_publication_results_default_to_read_only(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    _seed_preprints(sandbox)
    result = _invoke(sandbox, ["update-status"], json_output=json_output, credentials=False)
    assert result.exit_code == 0, result.output
    assert len(sandbox.requests) == 4
    assert all("x-api-key" not in request.headers for request in sandbox.requests)
    sandbox.constructor.assert_not_called()
    if json_output:
        payload = parse_json_output(result.output)
        assert payload["dry_run"] is True
        assert payload["meta"]["count"] == 4
        data = payload["data"]
        assert (data["checked"], data["published"], data["updated"], data["sync_required"]) == (4, 2, 0, False)
        assert data["update_failed"] == []
        rows = {row["key"]: row for row in data["results"]}
        assert list(rows) == ["ATTN001", "BERT002", "DEEP003", "SCAL006"]
        assert rows["ATTN001"]["doi"] == "10.1000/published-one"
        assert rows["BERT002"]["journal"] == "Offline Biology"
        assert rows["BERT002"]["source"] == "doi"
        assert rows["DEEP003"]["published"] is False
        assert rows["SCAL006"]["published"] is False
        assert all("updated" not in row for row in rows.values())
    else:
        assert "No API key" in result.stderr
        assert "Checking 2 arXiv + 2 bioRxiv/medRxiv preprint(s)" in result.stdout
        assert "Estimated time: ~12s" in result.stdout
        assert "Not yet published" in result.stdout
        assert "Not found on Semantic Scholar" in result.stdout
        assert "Found 2/4 published paper(s)" in result.stdout
        assert "Use --apply" in result.stdout


@pytest.mark.parametrize("api_key_source", ["flag", "config"])
def test_update_status_semantic_scholar_key_and_collection_limit_are_forwarded(
    sandbox: ServiceSandbox,
    api_key_source: str,
) -> None:
    _seed_preprints(sandbox)
    (sandbox.root / "offline.toml").write_text(
        '[zotero]\nsemantic_scholar_api_key = "offline-config-ss"\n',
        encoding="utf-8",
    )
    args = ["--limit", "1", "update-status", "--collection", "Machine Learning", "--limit", "2"]
    expected_key = "offline-config-ss"
    if api_key_source == "flag":
        args.extend(["--api-key", "offline-flag-ss"])
        expected_key = "offline-flag-ss"
    result = _invoke(sandbox, args)
    assert result.exit_code == 0, result.output
    assert parse_json_output(result.output)["data"]["checked"] == 2
    assert len(sandbox.requests) == 2
    assert all(request.headers["x-api-key"] == expected_key for request in sandbox.requests)
    assert expected_key not in result.output
    sandbox.constructor.assert_not_called()


def test_update_status_missing_collection_is_typed_before_network(sandbox: ServiceSandbox) -> None:
    _seed_preprints(sandbox)
    result = _invoke(sandbox, ["update-status", "--collection", "unknown-offline-collection"])
    assert result.exit_code == 4
    payload = parse_json_output(result.output)
    assert payload["error"]["code"] == "not_found"
    assert "unknown-offline-collection" in payload["error"]["message"]
    assert sandbox.requests == []
    sandbox.constructor.assert_not_called()


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_item_without_identifier_does_not_create_clients(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    result = _invoke(sandbox, ["update-status", "DUPE008", "--apply"], json_output=json_output)
    assert result.exit_code == 0, result.output
    assert sandbox.clients == []
    sandbox.constructor.assert_not_called()
    if json_output:
        data = parse_json_output(result.output)["data"]
        assert data == {"results": [], "published": 0, "checked": 0, "updated": 0, "sync_required": False}
    else:
        assert "No items with preprint IDs" in result.stdout


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_apply_requires_credentials_only_for_published_items(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    _seed_preprints(sandbox)
    unpublished = _invoke(
        sandbox,
        ["update-status", "DEEP003", "--apply"],
        json_output=json_output,
        credentials=False,
    )
    assert unpublished.exit_code == 0, unpublished.output
    published = _invoke(
        sandbox,
        ["update-status", "ATTN001", "--apply"],
        json_output=json_output,
        credentials=False,
    )
    assert published.exit_code == 2, published.output
    sandbox.constructor.assert_not_called()
    if json_output:
        assert parse_json_output(published.output)["error"]["code"] == "auth_missing"
        assert parse_json_output(unpublished.output)["data"]["sync_required"] is False
    else:
        assert "Zotero write credentials not configured" in published.stderr
        assert SYNC_REMINDER not in unpublished.output


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_apply_maps_only_publication_fields_and_requests_sync(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    _seed_preprints(sandbox)
    result = _invoke(sandbox, ["update-status", "--apply"], json_output=json_output)
    assert result.exit_code == 0, result.output
    sandbox.constructor.assert_called_once_with("12345", "user", "offline-write-key")
    updates = [call.args[0] for call in sandbox.zotero.update_item.call_args_list]
    assert len(updates) == 2
    assert [item["key"] for item in updates] == ["ATTN001", "BERT002"]
    assert updates[0]["data"] == {
        "key": "ATTN001",
        "title": "Original ATTN001",
        "extra": "Keep me",
        "DOI": "10.1000/published-one",
        "publicationTitle": "Offline Conference",
        "date": "2025-04-01",
    }
    assert updates[1]["data"] == {
        "key": "BERT002",
        "title": "Original BERT002",
        "extra": "Keep me",
        "DOI": "10.1000/published-two",
        "publicationTitle": "Offline Biology",
    }
    if json_output:
        payload = parse_json_output(result.output)
        assert "dry_run" not in payload
        assert payload["data"]["updated"] == 2
        assert payload["data"]["sync_required"] is True
        assert payload["data"]["update_failed"] == []
        assert [row.get("updated") for row in payload["data"]["results"]] == [True, True, None, None]
    else:
        assert "Updated 2 item(s)" in result.stdout
        assert SYNC_REMINDER in result.stdout


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_apply_continues_after_failed_write_and_reports_exact_counts(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    _seed_preprints(sandbox)
    sandbox.zotero.update_item.side_effect = [httpx2.ConnectError("offline write interrupted"), None]
    result = _invoke(sandbox, ["update-status", "--apply"], json_output=json_output)
    # fix2: a partial apply (one write failed) is ok="partial" and exits non-zero.
    assert result.exit_code == 5, result.output
    assert sandbox.zotero.update_item.call_count == 2
    if json_output:
        envelope = parse_json_output(result.output)
        assert envelope["ok"] == "partial"
        assert "error" not in envelope
        data = envelope["data"]
        assert (data["checked"], data["published"], data["updated"], data["sync_required"]) == (4, 2, 1, True)
        assert [row["key"] for row in data["update_failed"]] == ["ATTN001"]
        failure = data["results"][0]
        assert failure["updated"] is False
        assert failure["update_error"]["code"] == "network_error"
        assert failure["update_error"]["retryable"] is True
        assert data["results"][1]["updated"] is True
    else:
        assert "Failed ATTN001: Network error" in result.stderr
        assert "Updated BERT002" in result.stdout
        assert "Updated 1 item(s)" in result.stdout
        assert SYNC_REMINDER in result.stdout


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_update_status_all_writes_failed_never_claims_sync_required(
    sandbox: ServiceSandbox,
    json_output: bool,
) -> None:
    _seed_preprints(sandbox)
    sandbox.zotero.update_item.side_effect = httpx2.ConnectError("offline service unavailable")
    result = _invoke(sandbox, ["update-status", "--apply"], json_output=json_output)
    # fix2: all writes failed -> still ok="partial" (data has update_failed) and exit 5.
    assert result.exit_code == 5, result.output
    if json_output:
        envelope = parse_json_output(result.output)
        assert envelope["ok"] == "partial"
        data = envelope["data"]
        assert data["updated"] == 0
        assert data["sync_required"] is False
        assert len(data["update_failed"]) == 2
        assert all(row["error"]["retryable"] for row in data["update_failed"])
    else:
        assert "Updated 0 item(s)" in result.stdout
        assert result.stderr.count("Failed ") == 2
        assert SYNC_REMINDER not in result.output


@pytest.mark.parametrize("key", ["ATTN001", "BERT002"])
def test_update_status_single_source_with_api_key_omits_rate_limit_warning(
    sandbox: ServiceSandbox,
    key: str,
) -> None:
    _seed_preprints(sandbox)
    result = _invoke(
        sandbox,
        ["update-status", key, "--api-key", "offline-ss-key"],
        json_output=False,
    )
    assert result.exit_code == 0, result.output
    assert "No API key" not in result.stderr
    assert "Estimated time" not in result.stdout
    assert "Checking 1 " in result.stdout
    assert "Found 1/1 published paper(s)" in result.stdout
    assert len(sandbox.requests) == 1
    assert sandbox.requests[0].headers["x-api-key"] == "offline-ss-key"
    sandbox.constructor.assert_not_called()


def test_update_status_missing_item_does_not_construct_service_clients(sandbox: ServiceSandbox) -> None:
    result = _invoke(sandbox, ["update-status", "OFFLINE-MISSING", "--apply"])
    assert result.exit_code == 4
    assert parse_json_output(result.output)["error"]["code"] == "not_found"
    assert sandbox.clients == []
    sandbox.constructor.assert_not_called()


@pytest.mark.parametrize("failure", ["not-found", "unavailable", "disconnect"])
def test_update_status_failed_lookup_does_not_write_or_claim_published(
    sandbox: ServiceSandbox,
    failure: str,
) -> None:
    _seed_preprints(sandbox)
    if failure == "disconnect":
        sandbox.responses["arXiv:2401.00001"] = httpx.ConnectError("offline lookup disconnected")
    else:
        sandbox.responses["arXiv:2401.00001"] = (404 if failure == "not-found" else 503, {})
    result = _invoke(sandbox, ["update-status", "ATTN001", "--apply"])
    if failure == "not-found":
        assert result.exit_code == 0, result.output
        data = parse_json_output(result.output)["data"]
        assert (data["checked"], data["published"], data["updated"], data["sync_required"]) == (1, 0, 0, False)
        assert data["results"][0]["published"] is False
    else:
        # L-04: Semantic Scholar lookup failures are surfaced instead of silently
        # reporting the item as an unpublished preprint.
        assert result.exit_code == 5, result.output
        error = parse_json_output(result.output)["error"]
        assert error["code"] == "network_error"
        assert error["retryable"] is True
    sandbox.constructor.assert_not_called()
