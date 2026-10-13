from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_cli.core import idempotency


@pytest.mark.parametrize("operation", ["store", "get", "clear", "failed_store"])
def test_cache_connections_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
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
