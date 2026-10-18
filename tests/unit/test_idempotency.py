from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from zotero_cli.core.zotero import idempotency


@pytest.mark.parametrize("operation", ["store", "get", "clear", "failed_store"])
def test_cache_connections_are_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    monkeypatch.setattr(idempotency, "_db_path", lambda: tmp_path / "idempotency.db")
    idempotency.store_cached("update:ABC", "retry", {"ok": True})
    connect = idempotency._connect
    connections: list[sqlite3.Connection] = []

    def track_connection() -> sqlite3.Connection:
        conn = connect()
        connections.append(conn)
        return conn

    monkeypatch.setattr(idempotency, "_connect", track_connection)
    if operation == "store":
        idempotency.store_cached("update:ABC", "retry", {"ok": True})
    elif operation == "get":
        assert idempotency.get_cached("update:ABC", "retry") == {"ok": True}
    elif operation == "clear":
        idempotency.clear()
    else:
        with pytest.raises(TypeError):
            idempotency.store_cached("update:ABC", "retry", {"invalid": object()})

    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


# --- M-04: cache purge and definite-rejection classification ---


def test_expired_cache_rows_are_purged_on_connect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(idempotency, "_db_path", lambda: tmp_path / "idempotency.db")
    db_path = tmp_path / "idempotency.db"
    idempotency.store_cached("update:ABC", "fresh", {"ok": True})
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO cache (scope, key, value, created_at) VALUES (?, ?, ?, ?)",
            ("update:ABC", "expired", "{}", 1),
        )
    assert idempotency.get_cached("update:ABC", "fresh") == {"ok": True}
    with sqlite3.connect(db_path) as conn:
        expired = conn.execute("SELECT COUNT(*) FROM cache WHERE key = 'expired'").fetchone()[0]
    assert expired == 0


def test_failure_definitely_rejected_uses_response_then_error_code() -> None:
    request = httpx.Request("POST", "https://api.zotero.org/users/1/items")
    rejected = httpx.Response(400, request=request)
    unknown = httpx.Response(500, request=request)
    assert idempotency.failure_definitely_rejected(response=rejected, code="api_error", retryable=False) is True
    assert idempotency.failure_definitely_rejected(response=unknown, code="api_error", retryable=True) is False
    assert idempotency.failure_definitely_rejected(response=None, code="not_found", retryable=False) is True
    assert idempotency.failure_definitely_rejected(response=None, code="network_error", retryable=True) is False
    assert idempotency.failure_definitely_rejected(response=None, code="conflict", retryable=False) is False


def test_response_definitely_rejected_reads_item_failures() -> None:
    request = httpx.Request("POST", "https://api.zotero.org/users/1/items")
    payload = {"successful": {}, "failed": {"0": {"code": 400, "message": "bad"}}, "unchanged": {}}
    assert idempotency.response_definitely_rejected(httpx.Response(200, json=payload, request=request)) is True
    get_request = httpx.Request("GET", "https://api.zotero.org/users/1/items/ABC")
    assert idempotency.response_definitely_rejected(httpx.Response(400, request=get_request)) is False
