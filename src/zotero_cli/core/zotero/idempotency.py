"""SQLite-backed replay cache and opt-in durable mutation reservations.

Every idempotent mutation (add, update, delete, attach, note creation) reserves
(scope, key) before sending: an in-flight or unknown outcome blocks blind
resends, even after the cache TTL. Callers may keep using get_cached/
store_cached directly, but that pair alone does NOT provide an exactly-once
guarantee - use begin_mutation/release_rejected_mutation for writes whose
outcome matters. Reservations require reconciliation, not time-based expiry.

Scopes bind the command, library and request without including credentials.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx
import httpx2

from zotero_cli.config import state_dir

TTL_SECONDS = 24 * 60 * 60

# Statuses that prove the server rejected the write; the reservation can be
# released safely. Anything else (5xx, timeouts, 412) leaves the outcome
# unknown and keeps the reservation in place.
_REJECTED_STATUS_CODES = frozenset({400, 401, 403, 404, 409, 413, 422, 428, 429})


class PendingMutationError(Exception):
    """The same scoped key already has an in-flight or uncertain mutation."""


def request_scope(command: str, library_type: str, library_id: str, payload: object) -> str:
    """Bind a retry key to the target library and exact request, never credentials."""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{command}:{library_type}:{library_id}:{digest}"


def response_definitely_rejected(response: object) -> bool:
    """Return True only when the response proves the remote write did not land.

    pyzotero keeps the response of the last write request on the client, so
    callers can distinguish an explicit rejection (safe to release the
    reservation) from an unknown outcome (network drop, 412, 5xx, bad JSON).
    """
    if not isinstance(response, (httpx2.Response, httpx.Response)):
        return False
    request = getattr(response, "request", None)
    if request is None or request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return False
    if response.status_code in _REJECTED_STATUS_CODES:
        return True
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict) or any(payload.get(field) for field in ("successful", "success", "unchanged")):
        return False
    failed = payload.get("failed")
    failure = failed.get("0") if isinstance(failed, dict) else None
    code = failure.get("code") if isinstance(failure, dict) else None
    return isinstance(code, int) and code in _REJECTED_STATUS_CODES


_DEFINITIVE_FAILURE_CODES = frozenset({"not_found", "validation_error", "auth_invalid"})


def failure_definitely_rejected(*, response: object, code: str, retryable: bool, committed: bool = False) -> bool:
    """Return True when a write error proves the remote mutation did not land.

    Uses the last HTTP response when available and the typed error's committed
    flag; otherwise falls back to error codes that cannot represent an unknown
    outcome (a rejected key or invalid payload never commits). Conflicts, 5xx
    and network errors stay ambiguous.
    """
    if committed:
        return False
    if response_definitely_rejected(response):
        return True
    return code in _DEFINITIVE_FAILURE_CODES and not retryable


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
    conn.execute("DELETE FROM cache WHERE created_at < ?", (int(time.time()) - TTL_SECONDS,))
    conn.commit()
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
