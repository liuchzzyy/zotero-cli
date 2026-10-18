"""SQLite-backed replay cache and opt-in durable mutation reservations.

The success cache alone cannot protect a write that committed remotely but
lost its response. Callers using only get_cached/store_cached do NOT have an
exactly-once guarantee. Note creation additionally reserves (scope, key)
before sending: an in-flight or uncertain attempt blocks blind resends, even
after the cache TTL. Reservations require reconciliation, not time-based expiry.

Scopes bind the command, library and request without including credentials.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from zotero_cli.config import state_dir

TTL_SECONDS = 24 * 60 * 60


class PendingMutationError(Exception):
    """The same scoped key already has an in-flight or uncertain mutation."""


def request_scope(command: str, library_type: str, library_id: str, payload: object) -> str:
    """Bind a retry key to the target library and exact request, never credentials."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{command}:{library_type}:{library_id}:{digest}"


def _db_path() -> Path:
    base = state_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "idempotency.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        "scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at INTEGER NOT NULL, PRIMARY KEY (scope, key))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending ("
        "scope TEXT NOT NULL, key TEXT NOT NULL, created_at INTEGER NOT NULL, "
        "PRIMARY KEY (scope, key))"
    )
    return conn


def _cached_value(conn: sqlite3.Connection, scope: str, key: str) -> dict | None:
    row = conn.execute(
        "SELECT value, created_at FROM cache WHERE scope=? AND key=?",
        (scope, key),
    ).fetchone()
    if row is None:
        return None
    value_json, created_at = row
    if int(time.time()) - created_at > TTL_SECONDS:
        return None
    try:
        loaded = json.loads(value_json)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def get_cached(scope: str, key: str) -> dict | None:
    if not key:
        return None
    with closing(_connect()) as conn, conn:
        return _cached_value(conn, scope, key)


def begin_mutation(scope: str, key: str) -> dict | None:
    """Atomically replay a success or durably reserve a write before HTTP.

    A duplicate reservation fails closed. It has no TTL because neither a
    process exit nor elapsed time establishes whether the remote write committed.
    """
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached_value(conn, scope, key)
        if cached is not None:
            return cached
        try:
            conn.execute(
                "INSERT INTO pending (scope, key, created_at) VALUES (?, ?, ?)",
                (scope, key, int(time.time())),
            )
        except sqlite3.IntegrityError as exc:
            raise PendingMutationError("A write is in progress or its previous outcome is unknown") from exc
    return None


def release_rejected_mutation(scope: str, key: str) -> None:
    """Release a reservation only after the server explicitly rejected the write."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM pending WHERE scope=? AND key=?", (scope, key))


def store_cached(scope: str, key: str, envelope: dict) -> None:
    if not key:
        return
    now = int(time.time())
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (scope, key, value, created_at) VALUES (?, ?, ?, ?)",
            (scope, key, json.dumps(envelope), now),
        )
        conn.execute("DELETE FROM pending WHERE scope=? AND key=?", (scope, key))


def clear() -> None:
    """Clear cache and reservations. For isolated tests only, not recovery."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM cache")
        conn.execute("DELETE FROM pending")
