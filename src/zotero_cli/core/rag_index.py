from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_QUERY_TOKEN_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH expression from a free-text query."""
    tokens = [t for t in _QUERY_TOKEN_RE.split(query.lower()) if t]
    return " OR ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens)


class RagIndex:
    """SQLite term index: chunks + FTS5 for BM25 keyword retrieval.

    Vectors no longer live here; they are stored in the Qdrant local vector
    store keyed by the same chunk id.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), timeout=60.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 60000")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                doc_len INTEGER NOT NULL DEFAULT 0
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                tokenize = 'unicode61'
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_item ON chunks(item_key);
            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self._conn.commit()

    def insert_chunk(self, item_key: str, source: str, content: str, doc_len: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO chunks (item_key, source, content, doc_len) VALUES (?, ?, ?, ?)",
            (item_key, source, content, doc_len),
        )
        chunk_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
        self._conn.execute("INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (chunk_id, content))
        self._conn.commit()
        return chunk_id

    def insert_chunk_no_commit(self, item_key: str, source: str, content: str, doc_len: int = 0) -> int:
        cur = self._conn.execute(
            "INSERT INTO chunks (item_key, source, content, doc_len) VALUES (?, ?, ?, ?)",
            (item_key, source, content, doc_len),
        )
        chunk_id = int(cur.lastrowid) if cur.lastrowid is not None else 0
        self._conn.execute("INSERT INTO chunks_fts (rowid, content) VALUES (?, ?)", (chunk_id, content))
        return chunk_id

    def commit(self) -> None:
        self._conn.commit()

    def get_all_chunks(self) -> list[dict]:
        rows = self._conn.execute("SELECT id, item_key, source, content, doc_len FROM chunks").fetchall()
        return [dict(r) for r in rows]

    def get_chunk(self, chunk_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, item_key, source, content, doc_len FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_chunks_by_ids(self, chunk_ids: list[int]) -> dict[int, dict]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        rows = self._conn.execute(
            f"SELECT id, item_key, source, content, doc_len FROM chunks WHERE id IN ({placeholders})", chunk_ids
        ).fetchall()
        return {int(r["id"]): dict(r) for r in rows}

    def get_chunk_ids(self) -> list[int]:
        rows = self._conn.execute("SELECT id FROM chunks ORDER BY id").fetchall()
        return [int(r["id"]) for r in rows]

    def search_bm25(self, query: str, limit: int = 200) -> list[tuple[int, float, dict]]:
        fts = _fts_query(query)
        if not fts:
            return []
        rows = self._conn.execute(
            """
            SELECT c.id AS id, c.item_key AS item_key, c.source AS source, c.content AS content, c.doc_len AS doc_len,
                   -bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (fts, limit),
        ).fetchall()
        return [(int(r["id"]), float(r["score"]), dict(r)) for r in rows]

    def get_indexed_keys(self) -> set[str]:
        rows = self._conn.execute("SELECT DISTINCT item_key FROM chunks").fetchall()
        return {str(r["item_key"]) for r in rows}

    def get_item_chunk_ids(self, item_key: str) -> list[int]:
        rows = self._conn.execute("SELECT id FROM chunks WHERE item_key = ?", (item_key,)).fetchall()
        return [int(r["id"]) for r in rows]

    def delete_chunks_for_item(self, item_key: str) -> list[int]:
        ids = self.get_item_chunk_ids(item_key)
        if ids:
            self._conn.executemany("DELETE FROM chunks WHERE id = ?", [(i,) for i in ids])
            self._conn.executemany("DELETE FROM chunks_fts WHERE rowid = ?", [(i,) for i in ids])
        return ids

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO index_meta (key, value) VALUES (?, ?)", (key, value))
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def clear(self) -> None:
        self._conn.executescript("DELETE FROM chunks_fts; DELETE FROM chunks; DELETE FROM index_meta;")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
