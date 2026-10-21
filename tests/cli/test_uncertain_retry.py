"""通过真实 pyzotero 与纯 HTTP 假服务验证笔记重试的安全性。"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import httpx2 as httpx
import pytest
from pyzotero import zotero
from tests.support import invoke_agent_cli

from zotero_cli import config
from zotero_cli.zotero.access import idempotency

WRITE_ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake-test-key"}
NOTE_ARGS = ["zotero", "note", "ATTN001", "--add", "A finding", "--idempotency-key", "same-request"]


class NoteService:
    """模拟一次已提交、但响应可能在创建后丢失的写入。"""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.posts: list[httpx.Request] = []
        self.disconnect_after_create = True
        self.reject_next: httpx.Response | None = None
        self.response_after_create: httpx.Response | None = None
        self.before_create: Callable[[], None] | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/items/new":
            assert request.url.params["itemType"] == "note"
            return httpx.Response(
                200,
                json={"itemType": "note", "note": "", "tags": [], "collections": [], "relations": {}},
            )
        assert request.method == "POST"
        assert request.url.path in {"/users/123/items", "/users/456/items", "/groups/123/items"}
        self.posts.append(request)
        payload = json.loads(request.content)
        assert len(payload) == 1
        assert payload[0]["parentItem"] == "ATTN001"
        if self.before_create is not None:
            self.before_create()
        if self.reject_next is not None:
            response, self.reject_next = self.reject_next, None
            return response
        self.created.append(payload[0])
        if self.disconnect_after_create:
            self.disconnect_after_create = False
            raise httpx.RemoteProtocolError("Server committed, then disconnected", request=request)
        if self.response_after_create is not None:
            return self.response_after_create
        return httpx.Response(
            200,
            json={"successful": {"0": {"key": "NOTE0001"}}, "failed": {}, "unchanged": {}},
        )


@pytest.fixture
def note_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NoteService]:
    # 绝不加载检出目录中的凭据，也不写入其持久状态。
    monkeypatch.setattr(config.paths, "config_file_path", lambda: tmp_path / "no-live-config.toml")
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / "state")
    service = NoteService()
    real_zotero = zotero.Zotero
    with ExitStack() as clients:

        def make_zotero(library_id: str, library_type: str, api_key: str) -> zotero.Zotero:
            client = clients.enter_context(httpx.Client(transport=httpx.MockTransport(service.handle), trust_env=False))
            api = real_zotero(library_id, library_type, api_key, client=client)
            client.headers.update(api.default_headers())
            return api

        monkeypatch.setattr("zotero_cli.zotero.access.writer.zotero.Zotero", make_zotero)
        yield service


def test_note_retry_after_lost_create_response_is_fail_closed(note_service: NoteService) -> None:
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    retry = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)

    assert len(note_service.created) == 1, "A lost response must not cause another note creation"
    assert len(note_service.posts) == 1, "Unknown outcomes must reject a blind resend before HTTP"
    assert first.exit_code == 5, first.output
    assert json.loads(first.output)["error"]["retryable"] is False
    assert "unknown" in json.loads(first.output)["error"]["hint"].lower()
    assert retry.exit_code == 6, retry.output
    error = json.loads(retry.output)["error"]
    assert error["code"] == "conflict"
    assert error["retryable"] is False
    assert "reconcile" in error["hint"].lower()


@pytest.mark.parametrize(
    "rejection",
    [
        httpx.Response(400, text="Invalid note field"),
        httpx.Response(404, text="Parent not found"),
        httpx.Response(429, headers={"Retry-After": "1"}, text="Rate limited before writing"),
        httpx.Response(
            200,
            json={
                "successful": {},
                "failed": {"0": {"code": 400, "message": "Invalid note field"}},
                "unchanged": {},
            },
        ),
    ],
    ids=["http-400", "http-404", "http-429", "item-failed-400"],
)
def test_note_explicit_rejection_releases_reservation(note_service: NoteService, rejection: httpx.Response) -> None:
    note_service.reject_next = rejection
    note_service.disconnect_after_create = False
    rejected = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    retry = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    replay = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)

    assert retry.exit_code == 0, retry.output
    assert rejected.exit_code != 0
    assert json.loads(rejected.output)["error"]["retryable"] is False
    assert "unknown" not in json.loads(rejected.output)["error"]["hint"].lower()
    assert replay.exit_code == 0, replay.output
    assert json.loads(replay.output) == json.loads(retry.output)
    assert len(note_service.posts) == 2
    assert len(note_service.created) == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="truncated-json"),
        httpx.Response(200, json=[]),
        httpx.Response(500, text="Server error"),
        httpx.Response(200, json={"failed": {"0": {"code": [], "message": "Bad response"}}}),
        httpx.Response(412, text="Write token was already used"),
        httpx.Response(
            200,
            json={"success": {"0": "NOTE0001"}, "failed": {"0": {"code": 400, "message": "Conflicting response"}}},
        ),
    ],
    ids=["invalid-json", "invalid-shape", "server-error", "invalid-failure-code", "used-token", "conflicting-response"],
)
def test_note_unparseable_or_server_error_is_an_unknown_write(
    note_service: NoteService, response: httpx.Response
) -> None:
    note_service.disconnect_after_create = False
    note_service.response_after_create = response
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    retry = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)

    assert first.exit_code != 0
    assert first.output, "An uncertain outcome must emit a structured error, not escape as an exception"
    error = json.loads(first.output)["error"]
    assert error["retryable"] is False
    assert "unknown" in error["hint"].lower()
    assert retry.exit_code == 6, retry.output
    assert len(note_service.posts) == 1
    assert len(note_service.created) == 1


def test_note_pending_does_not_expire_or_depend_on_credentials(note_service: NoteService) -> None:
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert first.exit_code == 5, first.output
    now = idempotency.time.time()
    with patch.object(idempotency.time, "time", return_value=now + idempotency.TTL_SECONDS * 2):
        retry = invoke_agent_cli(NOTE_ARGS, env={**WRITE_ENV, "ZOT_API_KEY": "rotated-fake-key"})
    assert retry.exit_code == 6, retry.output
    assert len(note_service.posts) == 1
    with sqlite3.connect(idempotency._db_path()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
        rows = conn.execute("SELECT scope, key FROM pending").fetchall()
    assert "fake-test-key" not in str(rows)
    assert "rotated-fake-key" not in str(rows)
    assert "A finding" not in str(rows)


def test_note_pending_is_durable_across_processes(note_service: NoteService, tmp_path: Path) -> None:
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert first.exit_code == 5, first.output
    script = """
import socket
import sys
from pathlib import Path
from zotero_cli import config
from zotero_cli.zotero.access import idempotency

def no_network(*args, **kwargs):
    raise RuntimeError("Pending retries must not reach a network")

socket.socket.connect = no_network
socket.socket.connect_ex = no_network
root = Path(sys.argv[1])
config.paths.config_file_path = lambda: root / "no-live-config.toml"
idempotency.state_dir = lambda: root / "state"
from zotero_cli.cli import main
main(["--json", *sys.argv[2:]])
"""
    retry = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), *NOTE_ARGS],
        env={**{key: value for key, value in os.environ.items() if not key.startswith("ZOT_")}, **WRITE_ENV},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert retry.returncode == 6, retry.stdout + retry.stderr
    assert json.loads(retry.stdout)["error"]["retryable"] is False
    assert len(note_service.created) == 1


def test_note_in_flight_same_key_fails_closed(note_service: NoteService) -> None:
    competing = []
    note_service.disconnect_after_create = False
    note_service.before_create = lambda: competing.append(invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV))

    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert first.exit_code == 0, first.output
    assert len(competing) == 1
    assert competing[0].exit_code == 6, competing[0].output
    error = json.loads(competing[0].output)["error"]
    assert error["retryable"] is False
    assert "in progress" in error["message"]
    assert len(note_service.posts) == 1
    assert len(note_service.created) == 1


def test_simultaneous_reservations_have_only_one_owner(note_service: NoteService) -> None:
    barrier = Barrier(2)

    def claim() -> bool:
        barrier.wait(timeout=5)
        try:
            assert idempotency.begin_mutation("note:scope", "same-key") is None
            return True
        except idempotency.PendingMutationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(owners) == [False, True]
    assert note_service.posts == []


def test_note_scope_still_separates_payload_library_and_library_type(note_service: NoteService) -> None:
    note_service.disconnect_after_create = False
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    changed_args = ["different finding" if arg == "A finding" else arg for arg in NOTE_ARGS]
    different_payload = invoke_agent_cli(changed_args, env=WRITE_ENV)
    different_library = invoke_agent_cli(NOTE_ARGS, env={**WRITE_ENV, "ZOT_LIBRARY_ID": "456"})
    different_type = invoke_agent_cli(["--library", "group:123", *NOTE_ARGS], env=WRITE_ENV)
    replay = invoke_agent_cli(NOTE_ARGS, env={**WRITE_ENV, "ZOT_API_KEY": "rotated-fake-key"})
    assert all(
        result.exit_code == 0 for result in (first, different_payload, different_library, different_type, replay)
    )
    assert len(note_service.posts) == 4
    assert json.loads(replay.output) == json.loads(first.output)


def test_note_state_unavailable_prevents_http(note_service: NoteService, monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("Database locked")

    monkeypatch.setattr(idempotency, "_connect", failed_connect)
    result = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["retryable"] is False
    assert note_service.posts == []


def test_note_cache_write_failure_preserves_pending(note_service: NoteService, monkeypatch: pytest.MonkeyPatch) -> None:
    note_service.disconnect_after_create = False

    def fail_to_store(*_args: object) -> None:
        raise sqlite3.OperationalError("Disk full")

    monkeypatch.setattr(idempotency, "store_cached", fail_to_store)
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    retry = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert first.exit_code == 3, first.output
    assert json.loads(first.output)["error"]["retryable"] is False
    assert "NOTE0001" in first.output
    assert retry.exit_code == 6, retry.output
    assert len(note_service.posts) == 1


def test_note_failed_rejection_release_stays_fail_closed(
    note_service: NoteService, monkeypatch: pytest.MonkeyPatch
) -> None:
    note_service.reject_next = httpx.Response(400, text="Rejected")

    def fail_to_release(*_args: object) -> None:
        raise sqlite3.OperationalError("Disk full")

    monkeypatch.setattr(idempotency, "release_rejected_mutation", fail_to_release)
    first = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    retry = invoke_agent_cli(NOTE_ARGS, env=WRITE_ENV)
    assert first.exit_code != 0
    assert json.loads(first.output)["error"]["retryable"] is False
    assert retry.exit_code == 6, retry.output
    assert len(note_service.posts) == 1
    assert len(note_service.created) == 0


def test_note_dry_run_creates_neither_reservation_nor_http(note_service: NoteService) -> None:
    result = invoke_agent_cli([*NOTE_ARGS, "--dry-run"], env=WRITE_ENV)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    assert not idempotency._db_path().exists()
    assert note_service.posts == []
