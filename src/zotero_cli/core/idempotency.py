"""SQLite-backed idempotency cache for mutating commands.

An agent that retries a `zot add --idempotency-key K ...` call with the same
key within TTL gets back the original envelope instead of duplicating the
mutation upstream. Safe retries are the whole point of idempotency keys:
agents don't have to know whether the previous attempt actually committed.

The cache is keyed by (scope, key). Scope is command-specific
("add:doi:10.1/x", "update:ABC123", etc.) so two commands using the same
user-supplied key don't collide.
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
    return conn


def get_cached(scope: str, key: str) -> dict | None:
    if not key:
        return None
    now = int(time.time())
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT value, created_at FROM cache WHERE scope=? AND key=?",
            (scope, key),
        ).fetchone()
    if row is None:
        return None
    value_json, created_at = row
    if now - created_at > TTL_SECONDS:
        return None
    try:
        loaded = json.loads(value_json)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def store_cached(scope: str, key: str, envelope: dict) -> None:
    if not key:
        return
    now = int(time.time())
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (scope, key, value, created_at) VALUES (?, ?, ?, ?)",
            (scope, key, json.dumps(envelope), now),
        )


def clear() -> None:
    """Clear the entire cache. Exposed for tests."""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM cache")

