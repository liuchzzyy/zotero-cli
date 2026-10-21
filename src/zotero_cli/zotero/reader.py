"""SQLite read layer for the local Zotero database.

Reads go straight to zotero.sqlite and never write. Connection setup is
WAL-aware: when a non-empty WAL is present the database is replayed from a
temporary copy instead of using the stale immutable snapshot. Citation
formatting comes from CitationExportMixin.

The reader keeps every query concern here (items, search, recent, trash,
duplicates, notes, collections, attachments, stats, relations, and the batch
hydration helpers they share); only SQL-free export formatting lives in
citations.py.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from difflib import SequenceMatcher
from pathlib import Path

from zotero_cli.core.zotero.citations import CitationExportMixin
from zotero_cli.models import (
    Attachment,
    Collection,
    Creator,
    DuplicateGroup,
    Item,
    Note,
    SearchResult,
)

# Excluded type names (looked up dynamically per database)
_EXCLUDED_TYPE_NAMES = ("attachment", "note", "annotation")

# Stay below SQLite's legacy 999-variable limit, including library parameters.
_ITEM_BATCH_SIZE = 900

# Supported Zotero userdata schema range. Newer schemas may rename tables the
# reader depends on, so refuse them with a clear error instead of failing with
# confusing SQL errors halfway through a command.
MIN_SCHEMA_VERSION = 100
MAX_SCHEMA_VERSION = 200

# Schema v130+ databases no longer ship fulltext word tables. Zotero Desktop
# keeps a per-attachment plain-text cache next to the stored file instead; scan
# a bounded number of those caches so full-text search keeps working without
# loading the whole corpus into memory.
_FULLTEXT_CACHE_FILENAME = ".zotero-ft-cache"
# Bounds are sized from observed real libraries (1,510 caches on the reference
# library) so a normal library gets a complete scan while pathological
# collections stay finite. Scan truncation is reported via
# SearchResult.fulltext_limited instead of failing or silently pretending the
# corpus was fully searched.
_FULLTEXT_CACHE_MAX_FILES = 4000
_FULLTEXT_CACHE_MAX_BYTES_PER_FILE = 8 * 1024 * 1024
_FULLTEXT_CACHE_MAX_TOTAL_BYTES = 256 * 1024 * 1024


class ZoteroSchemaError(RuntimeError):
    """Raised when the local Zotero userdata schema is outside the tested range."""

    def __init__(
        self,
        version: int,
        *,
        minimum: int = MIN_SCHEMA_VERSION,
        maximum: int = MAX_SCHEMA_VERSION,
    ) -> None:
        self.schema_version = version
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(
            f"Unsupported Zotero database schema version {version} "
            f"(tested range {minimum}-{maximum}). The database may come from a "
            "newer Zotero release; update zotero-cli or use a supported Zotero version."
        )


class ZoteroReader(CitationExportMixin):
    def __init__(self, db_path: Path, library_id: int = 1, prefs_js_path: Path | None = None) -> None:
        self._db_path = db_path
        self._library_id = library_id
        self._conn: sqlite3.Connection | None = None
        self._tmp_dir: Path | None = None
        self._excluded_sql: str | None = None
        self._excluded_ids: tuple[int, ...] | None = None
        self._tmp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
        self._fulltext_mode: str | None = None
        self._schema_checked = False
        from zotero_cli.core.zotero.attachments import AttachmentResolver

        self._resolver = AttachmentResolver(db_path, prefs_js_path)

    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if not self._db_path.exists():
            raise FileNotFoundError(
                f"Zotero database not found: {self._db_path}\n"
                f"  Run 'zot config show' to check your configuration.\n"
                f"  Run 'zot config init --data-dir <path>' to set the correct data directory."
            )
        # immutable=1 skips the WAL, so while Zotero Desktop is running it
        # returns a stale snapshot: recently synced attachments appear missing
        # and their PDF paths look broken. Only take that fast path when there
        # is no pending WAL; otherwise replay DB+WAL from a temp copy.
        wal_path = self._db_path.with_suffix(".sqlite-wal")
        try:
            if wal_path.exists() and wal_path.stat().st_size > 0:
                return self._connect_from_copy()
        except OSError:
            pass
        uri_path = self._db_path.as_posix()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(
                f"file:{uri_path}?mode=ro&immutable=1",
                uri=True,
                timeout=5.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM items LIMIT 1")
            self._validate_schema(conn)
            self._conn = conn
            return conn
        except ZoteroSchemaError:
            if conn is not None:
                conn.close()
            raise
        except sqlite3.Error:
            # Fallback: copy DB to temp file (e.g. if immutable read hits corruption)
            if conn is not None:
                conn.close()
            return self._connect_from_copy()

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        """Reject userdata schemas outside the tested range before running SQL."""
        if self._schema_checked:
            return
        self._schema_checked = True
        try:
            row = conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone()
        except sqlite3.Error:
            # Schema-less fixture databases (and pre-version columns) are fine.
            return
        if row is None:
            return
        version = row["version"]
        if not MIN_SCHEMA_VERSION <= version <= MAX_SCHEMA_VERSION:
            raise ZoteroSchemaError(version)

    def _get_excluded_ids(self) -> tuple[int, ...]:
        """Look up excluded type IDs by name (cached after first call)."""
        if self._excluded_ids is not None:
            return self._excluded_ids
        conn = self._connect()
        placeholders = ",".join("?" * len(_EXCLUDED_TYPE_NAMES))
        rows = conn.execute(
            f"SELECT itemTypeID FROM itemTypes WHERE typeName IN ({placeholders})",
            _EXCLUDED_TYPE_NAMES,
        ).fetchall()
        self._excluded_ids = tuple(r["itemTypeID"] for r in rows) if rows else (-1,)
        return self._excluded_ids

    def _get_excluded_sql(self) -> str:
        """Build SQL fragment with literal IDs (for simple string concatenation)."""
        if self._excluded_sql is not None:
            return self._excluded_sql
        ids = self._get_excluded_ids()
        self._excluded_sql = f"NOT IN ({','.join(str(i) for i in ids)})"
        return self._excluded_sql

    def _excluded_filter(self) -> tuple[str, tuple[int, ...]]:
        """Return (SQL fragment with ? placeholders, parameter tuple) for excluded types."""
        ids = self._get_excluded_ids()
        ph = ",".join("?" * len(ids))
        return f"NOT IN ({ph})", ids

    def _library_filter(self) -> tuple[str, tuple[int, ...]]:
        """Return (SQL fragment, params) scoping item queries to this library.

        Always filter explicitly: the user library is libraryID 1 and Zotero
        group libraries have their own IDs, so omitting the filter would leak
        group items into user-library results.
        """
        return "AND i.libraryID = ?", (self._library_id,)

    def _connect_from_copy(self) -> sqlite3.Connection:
        """Replay DB+WAL from a private temporary copy to avoid WAL locks.

        Files are materialised atomically inside a private temp directory and
        the resulting connection is marked query_only so reading can never
        write back to the copy or the live database.
        """
        tmp_dir_obj = tempfile.TemporaryDirectory()
        tmp_dir = Path(tmp_dir_obj.name)
        try:
            conn = self._connect_copy_connection(tmp_dir)
        except BaseException:
            tmp_dir_obj.cleanup()
            raise
        self._tmp_dir_obj = tmp_dir_obj
        self._tmp_dir = tmp_dir
        self._conn = conn
        return conn

    def _connect_copy_connection(self, tmp_dir: Path) -> sqlite3.Connection:
        tmp = tmp_dir / "zotero.sqlite"
        sources: list[tuple[Path, Path]] = [(self._db_path, tmp)]
        for suffix in ("-wal", "-shm"):
            source = Path(f"{self._db_path}{suffix}")
            if source.exists():
                sources.append((source, Path(f"{tmp}{suffix}")))
        for source, target in sources:
            scratch = target.with_name(target.name + ".tmp")
            try:
                shutil.copy2(source, scratch)
                scratch.replace(target)
            except OSError:
                scratch.unlink(missing_ok=True)
                raise
        conn = sqlite3.connect(str(tmp), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("SELECT 1 FROM items LIMIT 1")
            conn.execute("PRAGMA query_only = ON")
            self._validate_schema(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    def close(self) -> None:
        """Close the SQLite connection and clean up any WAL replay copy."""
        if self._conn:
            self._conn.close()
            self._conn = None
        if hasattr(self, "_tmp_dir_obj") and self._tmp_dir_obj is not None:
            self._tmp_dir_obj.cleanup()
            self._tmp_dir_obj = None
            self._tmp_dir = None

    def __enter__(self) -> ZoteroReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_schema_version(self) -> int | None:
        """Return the userdata schema version, or None when the table is absent."""
        conn = self._connect()
        row = conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone()
        return row["version"] if row else None

    def resolve_group_library_id(self, group_id: int) -> int | None:
        """Look up the SQLite libraryID for a Zotero group by its public groupID."""
        conn = self._connect()
        row = conn.execute(
            "SELECT libraryID FROM groups WHERE groupID = ?",
            (group_id,),
        ).fetchone()
        return row["libraryID"] if row else None

    def get_item(self, key: str) -> Item | None:
        """Look up a top-level item by key, scoped to this reader's library."""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        row = conn.execute(
            "SELECT itemID, itemTypeID, key, dateAdded, dateModified "
            "FROM items i WHERE key = ? AND itemTypeID " + self._get_excluded_sql() + " " + lib_sql,
            (key, *lib_params),
        ).fetchone()
        if row is None:
            return None
        item_id = row["itemID"]
        item_type = conn.execute(
            "SELECT typeName FROM itemTypes WHERE itemTypeID = ?",
            (row["itemTypeID"],),
        ).fetchone()["typeName"]
        fields = self._get_item_fields(conn, item_id)
        creators = self._get_item_creators(conn, item_id)
        tags = self._get_item_tags(conn, item_id)
        collections = self._get_item_collections(conn, item_id)
        return Item(
            key=key,
            item_type=item_type,
            title=fields.get("title", ""),
            creators=creators,
            abstract=fields.get("abstractNote"),
            date=fields.get("date"),
            url=fields.get("url"),
            doi=fields.get("DOI"),
            tags=tags,
            collections=collections,
            date_added=row["dateAdded"],
            date_modified=row["dateModified"],
            extra={k: v for k, v in fields.items() if k not in ("title", "abstractNote", "date", "url", "DOI")},
        )

    def search(
        self,
        query: str,
        collection: str | None = None,
        item_type: str | None = None,
        sort: str | None = None,
        direction: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """Search items by title, abstract, creator, tag, or full text; trash excluded."""
        conn = self._connect()
        item_ids: set[int] = set()
        fulltext_limited = False
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        if query:
            like = self._like_pattern(query)
            # Search titles, abstracts, and all other metadata field values
            rows = conn.execute(
                "SELECT DISTINCT i.itemID FROM items i "
                "JOIN itemData id ON i.itemID = id.itemID "
                "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                f"WHERE iv.value LIKE ? ESCAPE '\\' AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # Search creators
            rows = conn.execute(
                "SELECT DISTINCT ic.itemID FROM itemCreators ic "
                "JOIN creators c ON ic.creatorID = c.creatorID "
                "JOIN items i ON ic.itemID = i.itemID "
                "WHERE (c.firstName LIKE ? ESCAPE '\\' OR c.lastName LIKE ? ESCAPE '\\') "
                f"AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # Search tags
            rows = conn.execute(
                "SELECT DISTINCT it.itemID FROM itemTags it "
                "JOIN tags t ON it.tagID = t.tagID "
                "JOIN items i ON it.itemID = i.itemID "
                f"WHERE t.name LIKE ? ESCAPE '\\' AND i.itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (like, *excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

            # Search fulltext (legacy word tables or v130 per-attachment caches).
            fulltext_ids, fulltext_limited = self._search_fulltext(
                conn, query, excl_sql, excl_params, lib_sql, lib_params
            )
            item_ids.update(fulltext_ids)
        else:
            rows = conn.execute(
                f"SELECT itemID FROM items i WHERE itemTypeID {excl_sql} {lib_sql} "
                "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                (*excl_params, *lib_params),
            ).fetchall()
            item_ids.update(r["itemID"] for r in rows)

        # Filter by collection (accepts key or name)
        if collection:
            col_row = conn.execute(
                "SELECT collectionID FROM collections WHERE libraryID = ? AND (key = ? OR collectionName = ?)",
                (self._library_id, collection, collection),
            ).fetchone()
            if col_row:
                col_items = conn.execute(
                    "SELECT itemID FROM collectionItems WHERE collectionID = ?",
                    (col_row["collectionID"],),
                ).fetchall()
                col_item_ids = {r["itemID"] for r in col_items}
                item_ids &= col_item_ids
            else:
                raise ValueError(
                    f"Collection '{collection}' not found. Use 'zot zotero collection list' to see available names."
                )

        # Filter by item type (batched to respect SQLite's variable limit)
        if item_type:
            type_row = conn.execute(
                "SELECT itemTypeID FROM itemTypes WHERE typeName = ?",
                (item_type,),
            ).fetchone()
            if type_row and item_ids:
                typed_ids: set[int] = set()
                for chunk in self._chunks(list(item_ids)):
                    placeholders = ",".join("?" * len(chunk))
                    typed_rows = conn.execute(
                        f"SELECT itemID FROM items WHERE itemTypeID = ? AND itemID IN ({placeholders})",
                        (type_row["itemTypeID"], *chunk),
                    ).fetchall()
                    typed_ids.update(r["itemID"] for r in typed_rows)
                item_ids = typed_ids
            else:
                item_ids = set()

        # Resolve items in batch
        total = len(item_ids)

        if sort and item_ids:
            target_ids = self._sorted_item_ids(conn, item_ids, sort, direction, limit, offset)
        else:
            target_ids = sorted(item_ids)[offset : offset + limit]

        items = self._get_items_batch(conn, target_ids) if target_ids else []

        return SearchResult(items=items, total=total, query=query, fulltext_limited=fulltext_limited)

    # --- Search helpers ---

    @staticmethod
    def _like_pattern(query: str) -> str:
        """Build a LIKE pattern that treats user wildcards as literal text."""
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def _fulltext_schema(self, conn: sqlite3.Connection) -> str:
        """Return which fulltext schema this database uses ("legacy"/"v130"/"none")."""
        if self._fulltext_mode is not None:
            return self._fulltext_mode
        try:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('fulltextItemWords', 'fulltextWords', 'fulltextItems')"
                )
            }
        except sqlite3.Error:
            names = set()
        if {"fulltextItemWords", "fulltextWords"} <= names:
            mode = "legacy"
        elif "fulltextItems" in names:
            mode = "v130"
        else:
            mode = "none"
        self._fulltext_mode = mode
        return mode

    def _search_fulltext(
        self,
        conn: sqlite3.Connection,
        query: str,
        excl_sql: str,
        excl_params: tuple[int, ...],
        lib_sql: str,
        lib_params: tuple[int, ...],
    ) -> tuple[set[int], bool]:
        """Resolve item IDs whose attachment full text matches the query.

        Zotero 6 databases store tokenised full text in fulltextWords /
        fulltextItemWords; Zotero 7+ (userdata v130) only stores fulltextItems
        and keeps the extracted text in per-attachment .zotero-ft-cache files.
        Both are handled defensively: a missing table or unreadable cache
        degrades to metadata-only search instead of crashing.
        """
        mode = self._fulltext_schema(conn)
        if mode == "legacy":
            try:
                rows = conn.execute(
                    "SELECT DISTINCT ia.parentItemID FROM fulltextItemWords fw "
                    "JOIN fulltextWords w ON fw.wordID = w.wordID "
                    "JOIN itemAttachments ia ON fw.itemID = ia.itemID "
                    "JOIN items i ON ia.parentItemID = i.itemID "
                    f"WHERE w.word LIKE ? ESCAPE '\\' AND ia.parentItemID IS NOT NULL "
                    f"AND i.itemTypeID {excl_sql} {lib_sql} "
                    "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
                    (self._like_pattern(query), *excl_params, *lib_params),
                ).fetchall()
            except sqlite3.Error:
                return set(), False
            return {row["parentItemID"] for row in rows}, False
        if mode == "v130":
            return self._cached_fulltext_ids(conn, query, excl_sql, excl_params, lib_sql, lib_params)
        return set(), False

    def _cached_fulltext_ids(
        self,
        conn: sqlite3.Connection,
        query: str,
        excl_sql: str,
        excl_params: tuple[int, ...],
        lib_sql: str,
        lib_params: tuple[int, ...],
    ) -> tuple[set[int], bool]:
        """Scan a bounded number of .zotero-ft-cache files for the query text."""
        needle = query.casefold()
        if not needle:
            return set(), False
        base_join = (
            "FROM fulltextItems ft "
            "JOIN itemAttachments ia ON ia.itemID = ft.itemID "
            "JOIN items att ON att.itemID = ft.itemID "
            "JOIN items i ON ia.parentItemID = i.itemID "
            "WHERE ia.parentItemID IS NOT NULL "
            f"AND i.itemTypeID {excl_sql} {lib_sql} "
            "AND i.itemID NOT IN (SELECT itemID FROM deletedItems)"
        )
        try:
            total_indexed = conn.execute(
                f"SELECT COUNT(*) AS count {base_join}",
                (*excl_params, *lib_params),
            ).fetchone()["count"]
            rows = conn.execute(
                "SELECT ia.parentItemID AS parent_id, att.key AS attachment_key "
                f"{base_join} ORDER BY ft.itemID LIMIT ?",
                (*excl_params, *lib_params, _FULLTEXT_CACHE_MAX_FILES),
            ).fetchall()
        except sqlite3.Error:
            return set(), False
        limited = total_indexed > len(rows)
        storage_dir = self._resolver.storage_dir
        matched: set[int] = set()
        scanned_bytes = 0
        for position, row in enumerate(rows):
            cache_path = storage_dir / row["attachment_key"] / _FULLTEXT_CACHE_FILENAME
            try:
                if not cache_path.is_file():
                    continue
                with cache_path.open("rb") as handle:
                    data = handle.read(_FULLTEXT_CACHE_MAX_BYTES_PER_FILE)
            except OSError:
                continue
            scanned_bytes += len(data)
            if needle in data.decode("utf-8", errors="ignore").casefold():
                matched.add(row["parent_id"])
            if scanned_bytes >= _FULLTEXT_CACHE_MAX_TOTAL_BYTES:
                limited = limited or position + 1 < len(rows)
                break
        return matched, limited

    @staticmethod
    def _chunks(values: list[int], size: int | None = None) -> Iterator[list[int]]:
        size = _ITEM_BATCH_SIZE if size is None else size
        """Yield fixed-size slices so IN (...) queries stay under SQLite limits."""
        for start in range(0, len(values), size):
            yield values[start : start + size]

    def _sorted_item_ids(
        self,
        conn: sqlite3.Connection,
        item_ids: set[int],
        sort: str,
        direction: str,
        limit: int,
        offset: int,
    ) -> list[int]:
        """Return page-sorted IDs, batching IN (...) queries under SQLite limits."""
        ids = list(item_ids)
        descending = direction == "desc"
        if sort in ("dateAdded", "dateModified"):
            rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                rows.extend(
                    (str(row["sort_value"] or ""), row["itemID"])
                    for row in conn.execute(
                        f"SELECT itemID, {sort} AS sort_value FROM items WHERE itemID IN ({placeholders})",
                        chunk,
                    )
                )
            rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in rows[offset : offset + limit]]
        if sort == "title":
            title_field = conn.execute("SELECT fieldID FROM fields WHERE fieldName = 'title'").fetchone()
            field_id = title_field["fieldID"] if title_field else 4
            title_rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                title_rows.extend(
                    (self._nocase_key(str(row["sort_value"] or "")), row["itemID"])
                    for row in conn.execute(
                        "SELECT i.itemID AS itemID, COALESCE(iv_t.value, '') AS sort_value "
                        "FROM items i "
                        "LEFT JOIN itemData id_t ON i.itemID = id_t.itemID AND id_t.fieldID = ? "
                        "LEFT JOIN itemDataValues iv_t ON id_t.valueID = iv_t.valueID "
                        f"WHERE i.itemID IN ({placeholders})",
                        (field_id, *chunk),
                    )
                )
            title_rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in title_rows[offset : offset + limit]]
        if sort == "creator":
            creator_rows: list[tuple[str, int]] = []
            for chunk in self._chunks(ids):
                placeholders = ",".join("?" * len(chunk))
                creator_rows.extend(
                    (self._nocase_key(str(row["sort_value"] or "")), row["itemID"])
                    for row in conn.execute(
                        "SELECT i.itemID AS itemID, COALESCE(c.lastName, '') AS sort_value "
                        "FROM items i "
                        "LEFT JOIN itemCreators ic ON i.itemID = ic.itemID AND ic.orderIndex = 0 "
                        "LEFT JOIN creators c ON ic.creatorID = c.creatorID "
                        f"WHERE i.itemID IN ({placeholders})",
                        chunk,
                    )
                )
            creator_rows.sort(key=lambda row: row[0], reverse=descending)
            return [item_id for _, item_id in creator_rows[offset : offset + limit]]
        return sorted(item_ids)[offset : offset + limit]

    @staticmethod
    def _nocase_key(value: str) -> str:
        """Approximate SQLite NOCASE collation (ASCII-only case folding)."""
        return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)

    def get_recent_items(
        self,
        since: str,
        sort: str = "dateAdded",
        limit: int = 50,
    ) -> list[Item]:
        """Return items added or modified since the given timestamp."""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()
        col = "dateModified" if sort == "dateModified" else "dateAdded"
        rows = conn.execute(
            f"SELECT itemID FROM items i WHERE {col} >= ? AND itemTypeID {excl_sql} {lib_sql} "
            f"AND i.itemID NOT IN (SELECT itemID FROM deletedItems) ORDER BY {col} DESC LIMIT ?",
            (since, *excl_params, *lib_params, limit),
        ).fetchall()
        item_ids = [r["itemID"] for r in rows]
        return self._get_items_batch(conn, item_ids) if item_ids else []

    def get_trash_items(self, limit: int = 50) -> list[Item]:
        """Return items in the trash, ordered by deletion date (newest first)."""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()
        rows = conn.execute(
            f"SELECT i.itemID FROM items i "
            f"JOIN deletedItems d ON i.itemID = d.itemID "
            f"WHERE i.itemTypeID {excl_sql} {lib_sql} "
            f"ORDER BY d.dateDeleted DESC LIMIT ?",
            (*excl_params, *lib_params, limit),
        ).fetchall()
        item_ids = [r["itemID"] for r in rows]
        return self._get_items_batch(conn, item_ids) if item_ids else []

    def find_duplicates(
        self,
        strategy: str = "both",
        threshold: float = 0.85,
        limit: int = 50,
    ) -> list[DuplicateGroup]:
        """Find potential duplicate items by DOI and/or title similarity.

        Candidates are scoped to this library. Trashed items may still be
        included: find_duplicates follows the documented M-01 mixed trash
        scoping, so callers should not assume a global deletedItems filter.
        Title fuzzy matching uses blocking keys plus cheap ratio prefilters so
        it does not compare every pair of titles in large libraries.
        """
        if limit <= 0:
            return []

        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        # Load items for comparison (cap at 10k most recent). Per M-01, trashed
        # items keep the documented mixed scoping and may be included here.
        rows = conn.execute(
            f"SELECT i.itemID, i.key FROM items i WHERE i.itemTypeID {excl_sql} {lib_sql} "
            "ORDER BY i.dateAdded DESC LIMIT 10000",
            (*excl_params, *lib_params),
        ).fetchall()

        item_keys = {r["itemID"]: r["key"] for r in rows}
        item_ids = list(item_keys.keys())
        if not item_ids:
            return []

        groups: list[DuplicateGroup] = []
        seen_group_keys: set[frozenset[str]] = set()

        def _reached_limit() -> bool:
            return len(groups) >= limit

        # --- DOI strategy ---
        if strategy in ("doi", "both"):
            doi_map: dict[str, list[int]] = {}
            for chunk in self._chunks(item_ids):
                placeholders = ",".join("?" * len(chunk))
                doi_rows = conn.execute(
                    "SELECT id.itemID, iv.value FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'DOI' AND id.itemID IN ({placeholders}) AND iv.value != ''",
                    chunk,
                ).fetchall()
                for row in doi_rows:
                    doi_map.setdefault(row["value"].strip().lower(), []).append(row["itemID"])

            for ids in doi_map.values():
                if len(ids) < 2:
                    continue
                group_key = frozenset(item_keys[i] for i in ids)
                if group_key in seen_group_keys:
                    continue
                seen_group_keys.add(group_key)
                items = self._get_items_batch(conn, ids)
                if len(items) >= 2:
                    groups.append(DuplicateGroup(items=items, match_type="doi", score=1.0))
                    if _reached_limit():
                        return groups

        # --- Title strategy ---
        if strategy in ("title", "both"):
            title_values: dict[int, str] = {}
            doi_by_item_id: dict[int, str] = {}
            for chunk in self._chunks(item_ids):
                placeholders = ",".join("?" * len(chunk))
                title_rows = conn.execute(
                    "SELECT id.itemID, iv.value FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'title' AND id.itemID IN ({placeholders})",
                    chunk,
                ).fetchall()
                title_values.update({row["itemID"]: row["value"] for row in title_rows})
                doi_rows = conn.execute(
                    "SELECT id.itemID, LOWER(TRIM(iv.value)) AS doi FROM itemData id "
                    "JOIN fields f ON id.fieldID = f.fieldID "
                    "JOIN itemDataValues iv ON id.valueID = iv.valueID "
                    f"WHERE f.fieldName = 'DOI' AND id.itemID IN ({placeholders}) AND TRIM(iv.value) != ''",
                    chunk,
                ).fetchall()
                doi_by_item_id.update({row["itemID"]: row["doi"] for row in doi_rows})

            # A title-only match is not enough when both records carry
            # different DOIs: books, reviews, translations, and conference
            # chapters often share nearly identical titles. Keep same-DOI
            # matches (already found by the DOI strategy) and title matches
            # where at least one DOI is absent, but suppress conflicting DOI
            # pairs as false positives.
            def _compatible_title_ids(ids: list[int]) -> bool:
                dois = {doi_by_item_id[item_id] for item_id in ids if item_id in doi_by_item_id}
                return len(dois) <= 1

            normalized_titles = [(item_id, self._normalize_title(title)) for item_id, title in title_values.items()]
            norm_by_id = dict(normalized_titles)

            # Group exact normalized matches (O(n))
            norm_groups: dict[str, list[int]] = {}
            for item_id, norm in normalized_titles:
                norm_groups.setdefault(norm, []).append(item_id)

            for ids in norm_groups.values():
                if len(ids) >= 2 and _compatible_title_ids(ids):
                    group_key = frozenset(item_keys[i] for i in ids)
                    if group_key not in seen_group_keys:
                        seen_group_keys.add(group_key)
                        items = self._get_items_batch(conn, ids)
                        if len(items) >= 2:
                            groups.append(DuplicateGroup(items=items, match_type="title", score=1.0))
                            if _reached_limit():
                                return groups

            # Fuzzy match singletons only; blocking keeps this far below O(n^2).
            singletons = [(item_id, norm) for item_id, norm in normalized_titles if len(norm_groups[norm]) == 1]
            candidates = self._duplicate_candidates([norm for _item_id, norm in singletons])
            matched: set[int] = set()
            for idx, (id_a, norm_a) in enumerate(singletons):
                if id_a in matched:
                    continue
                cluster = [id_a]
                candidate_indexes = sorted(index for index in candidates[idx] if index > idx)
                for j in candidate_indexes:
                    id_b, norm_b = singletons[j]
                    if id_b in matched:
                        continue
                    if not self._title_lengths_can_match(norm_a, norm_b, threshold):
                        continue
                    matcher = SequenceMatcher(None, norm_a, norm_b)
                    if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                        continue
                    ratio = matcher.ratio()
                    if ratio >= threshold:
                        if not _compatible_title_ids([*cluster, id_b]):
                            continue
                        cluster.append(id_b)
                        matched.add(id_b)
                if len(cluster) >= 2:
                    matched.add(id_a)
                    group_key = frozenset(item_keys[cid] for cid in cluster)
                    if group_key not in seen_group_keys:
                        seen_group_keys.add(group_key)
                        items = self._get_items_batch(conn, cluster)
                        best_score = (
                            max(SequenceMatcher(None, norm_a, norm_by_id[cid]).ratio() for cid in cluster[1:])
                            if len(items) >= 2
                            else 0.0
                        )
                        groups.append(DuplicateGroup(items=items, match_type="title", score=round(best_score, 3)))
                        if _reached_limit():
                            return groups

        return groups[:limit]

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Lowercase and strip punctuation/whitespace runs for title matching."""
        stripped = re.sub(r"[^\w\s]", "", title.lower()).strip()
        return re.sub(r"\s+", " ", stripped)

    @staticmethod
    def _title_lengths_can_match(norm_a: str, norm_b: str, threshold: float) -> bool:
        """Cheap upper bound on SequenceMatcher.ratio() for two title strings."""
        total_len = len(norm_a) + len(norm_b)
        if total_len == 0:
            return True
        max_ratio = 2 * min(len(norm_a), len(norm_b)) / total_len
        return max_ratio >= threshold

    @staticmethod
    def _title_block_keys(normalized: str) -> tuple[str, ...]:
        """Blocking keys likely shared by near-duplicate titles."""
        if not normalized:
            return ("empty",)
        keys = ["prefix:" + normalized[:6]]
        tokens = normalized.split()
        if tokens:
            keys.append("first:" + tokens[0])
            keys.append("last:" + tokens[-1])
        return tuple(keys)

    def _duplicate_candidates(self, normalized_titles: list[str]) -> list[set[int]]:
        """Return candidate partner indexes for each title using blocking keys."""
        buckets: dict[str, list[int]] = {}
        for index, normalized in enumerate(normalized_titles):
            for key in self._title_block_keys(normalized):
                buckets.setdefault(key, []).append(index)

        candidates: list[set[int]] = [set() for _ in normalized_titles]
        # Safety valve: blocking already cuts most pairs, but cap pathological
        # blocks so a large library can never degrade back to a full scan.
        max_pairs = 250_000
        pair_count = 0
        for members in buckets.values():
            if len(members) < 2:
                continue
            # Very large buckets are usually generic prefixes ("the ", ...);
            # refine them once by a longer prefix before pairing.
            groups = [members]
            if len(members) > 128:
                refined: dict[str, list[int]] = {}
                for index in members:
                    refined.setdefault(normalized_titles[index][:12], []).append(index)
                groups = list(refined.values())
            for group in groups:
                if len(group) > 256:
                    # Pathological bucket: compare only a bounded sliding window.
                    group = group[:256]
                for left_pos, left in enumerate(group):
                    for right in group[left_pos + 1 :]:
                        if pair_count >= max_pairs:
                            return candidates
                        candidates[left].add(right)
                        candidates[right].add(left)
                        pair_count += 1
        return candidates

    def get_notes(self, key: str) -> list[Note]:
        """Return an item's notes, skipping trashed notes and trashed parents."""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql} AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        rows = conn.execute(
            "SELECT i.itemID, i.key, n.note FROM itemNotes n JOIN items i ON n.itemID = i.itemID "
            "WHERE n.parentItemID = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems) ORDER BY i.itemID",
            (parent["itemID"],),
        ).fetchall()
        if not rows:
            return []
        # Batch-fetch tags for all note items
        note_ids = [r["itemID"] for r in rows]
        ph = ",".join("?" * len(note_ids))
        tag_rows = conn.execute(
            f"SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON it.tagID = t.tagID WHERE it.itemID IN ({ph})",
            note_ids,
        ).fetchall()
        tags_by_id: dict[int, list[str]] = {}
        for tr in tag_rows:
            tags_by_id.setdefault(tr["itemID"], []).append(tr["name"])
        notes = []
        for r in rows:
            content = self._html_to_markdown(r["note"] or "")
            tags = tags_by_id.get(r["itemID"], [])
            notes.append(Note(key=r["key"], parent_key=key, content=content, tags=tags))
        return notes

    def get_collections(self) -> list[Collection]:
        """Return the library's collection tree, with children nested under parents."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT collectionID, collectionName, parentCollectionID, key FROM collections WHERE libraryID = ?",
            (self._library_id,),
        ).fetchall()
        coll_map: dict[int, Collection] = {}
        parent_map: dict[int, int | None] = {}
        for r in rows:
            coll_map[r["collectionID"]] = Collection(
                key=r["key"],
                name=r["collectionName"],
                parent_key=None,
                children=[],
            )
            parent_map[r["collectionID"]] = r["parentCollectionID"]

        for cid, parent_cid in parent_map.items():
            if parent_cid and parent_cid in coll_map:
                coll_map[cid].parent_key = coll_map[parent_cid].key
                coll_map[parent_cid].children.append(coll_map[cid])

        return [c for cid, c in coll_map.items() if parent_map[cid] is None]

    def get_collection_items(self, collection_key: str) -> list[Item]:
        """Return the top-level items in a collection (notes and attachments excluded)."""
        conn = self._connect()
        _, lib_params = self._library_filter()
        col_row = conn.execute(
            "SELECT collectionID FROM collections WHERE key = ? " + ("AND libraryID = ?" if lib_params else ""),
            (collection_key, *lib_params),
        ).fetchone()
        if col_row is None:
            return []
        rows = conn.execute(
            "SELECT i.itemID FROM collectionItems ci "
            "JOIN items i ON ci.itemID = i.itemID "
            "WHERE ci.collectionID = ? AND i.itemTypeID " + self._get_excluded_sql(),
            (col_row["collectionID"],),
        ).fetchall()
        return self._get_items_batch(conn, [r["itemID"] for r in rows])

    def get_attachments(self, key: str) -> list[Attachment]:
        """Return an item's attachments, skipping trashed attachments and trashed parents."""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql} AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        rows = conn.execute(
            "SELECT i.key, ia.contentType, ia.path "
            "FROM itemAttachments ia "
            "JOIN items i ON ia.itemID = i.itemID "
            "WHERE ia.parentItemID = ? AND i.itemID NOT IN (SELECT itemID FROM deletedItems)",
            (parent["itemID"],),
        ).fetchall()
        attachments = []
        for r in rows:
            raw_path = r["path"] or ""
            resolved = self._resolver.resolve(r["key"], raw_path)
            if resolved:
                filename = resolved.name
            elif raw_path.startswith("storage:"):
                parts = raw_path.replace("storage:", "").split("/")
                filename = parts[-1] if parts else ""
            else:
                filename = raw_path.split("/")[-1] if raw_path else ""
            attachments.append(
                Attachment(
                    key=r["key"],
                    parent_key=key,
                    filename=filename,
                    content_type=r["contentType"] or "",
                    path=resolved,
                )
            )
        return attachments

    def get_pdf_attachment(self, key: str) -> Attachment | None:
        """Return the item's first PDF attachment, if any."""
        for att in self.get_attachments(key):
            if att.content_type == "application/pdf":
                return att
        return None

    def get_pdf_attachments(self, key: str) -> list[Attachment]:
        """Return the item's PDF attachments, dropping duplicate resolved paths."""
        pdf_attachments: list[Attachment] = []
        seen_paths: set[str] = set()
        for att in self.get_attachments(key):
            if att.content_type != "application/pdf":
                continue
            marker = str(att.path.resolve()) if att.path and att.path.exists() else f"{att.filename}:{att.key}"
            if marker in seen_paths:
                continue
            seen_paths.add(marker)
            pdf_attachments.append(att)
        return pdf_attachments

    def get_arxiv_preprints(
        self,
        collection: str | None = None,
        limit: int = 200,
    ) -> list[Item]:
        """Find preprint items (arXiv, bioRxiv, medRxiv) by URL, DOI, extra, or itemType."""
        conn = self._connect()
        excl_sql, excl_params = self._excluded_filter()
        lib_sql, lib_params = self._library_filter()

        # Find items with arXiv or bioRxiv/medRxiv references in URL, DOI, or extra fields
        rows = conn.execute(
            "SELECT DISTINCT id.itemID FROM itemData id "
            "JOIN fields f ON id.fieldID = f.fieldID "
            "JOIN itemDataValues iv ON id.valueID = iv.valueID "
            "JOIN items i ON id.itemID = i.itemID "
            f"WHERE f.fieldName IN ('url', 'DOI', 'extra') "
            f"AND (iv.value LIKE '%arxiv%' OR iv.value LIKE '%biorxiv%' OR iv.value LIKE '%medrxiv%' "
            f"OR iv.value LIKE '%10.1101/%') "
            f"AND i.itemTypeID {excl_sql} {lib_sql}",
            (*excl_params, *lib_params),
        ).fetchall()
        item_ids = {r["itemID"] for r in rows}

        # Also find all items with itemType = preprint
        preprint_type = conn.execute("SELECT itemTypeID FROM itemTypes WHERE typeName = 'preprint'").fetchone()
        if preprint_type:
            rows = conn.execute(
                f"SELECT i.itemID FROM items i WHERE i.itemTypeID = ? {lib_sql}",
                (preprint_type["itemTypeID"], *lib_params),
            ).fetchall()
            for r in rows:
                item_ids.add(r["itemID"])

        if not item_ids:
            return []

        # Filter by collection if specified
        if collection:
            col_row = conn.execute(
                "SELECT collectionID FROM collections WHERE libraryID = ? AND (key = ? OR collectionName = ?)",
                (self._library_id, collection, collection),
            ).fetchone()
            if col_row:
                col_items = conn.execute(
                    "SELECT itemID FROM collectionItems WHERE collectionID = ?",
                    (col_row["collectionID"],),
                ).fetchall()
                item_ids &= {r["itemID"] for r in col_items}
            else:
                raise ValueError(
                    f"Collection '{collection}' not found. Use 'zot zotero collection list' to see available names."
                )

        target_ids = sorted(item_ids)[:limit]
        return self._get_items_batch(conn, target_ids) if target_ids else []

    def get_stats(self) -> dict:
        """Return library statistics."""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        # Total items (excluding attachments and notes)
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM items i WHERE itemTypeID " + self._get_excluded_sql() + " " + lib_sql,
            lib_params,
        ).fetchone()["cnt"]

        # Items by type
        type_rows = conn.execute(
            "SELECT t.typeName, COUNT(*) as cnt FROM items i "
            "JOIN itemTypes t ON i.itemTypeID = t.itemTypeID "
            "WHERE i.itemTypeID " + self._get_excluded_sql() + " " + lib_sql + " "
            "GROUP BY t.typeName ORDER BY cnt DESC",
            lib_params,
        ).fetchall()
        by_type = {r["typeName"]: r["cnt"] for r in type_rows}

        # Top tags
        tag_rows = conn.execute(
            "SELECT t.name, COUNT(*) as cnt FROM itemTags it "
            "JOIN tags t ON it.tagID = t.tagID "
            "JOIN items i ON it.itemID = i.itemID "
            f"WHERE 1=1 {lib_sql} GROUP BY t.name ORDER BY cnt DESC LIMIT 20",
            lib_params,
        ).fetchall()
        top_tags = {r["name"]: r["cnt"] for r in tag_rows}

        # Collections with item counts
        coll_rows = conn.execute(
            "SELECT c.collectionName, COUNT(ci.itemID) as cnt "
            "FROM collections c "
            "LEFT JOIN collectionItems ci ON c.collectionID = ci.collectionID "
            + ("WHERE c.libraryID = ? " if lib_params else "")
            + "GROUP BY c.collectionName ORDER BY cnt DESC",
            lib_params,
        ).fetchall()
        collections = {r["collectionName"]: r["cnt"] for r in coll_rows}

        # Attachments
        pdf_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM itemAttachments ia "
            "JOIN items i ON ia.itemID = i.itemID "
            f"WHERE ia.contentType = 'application/pdf' {lib_sql}",
            lib_params,
        ).fetchone()["cnt"]

        # Notes count
        notes_count = conn.execute(
            f"SELECT COUNT(*) as cnt FROM itemNotes n JOIN items i ON n.itemID = i.itemID WHERE 1=1 {lib_sql}",
            lib_params,
        ).fetchone()["cnt"]

        return {
            "total_items": total,
            "by_type": by_type,
            "top_tags": top_tags,
            "collections": collections,
            "pdf_attachments": pdf_count,
            "notes": notes_count,
        }

    def get_related_items(self, key: str, limit: int = 20) -> list[Item]:
        """Rank related items by explicit relations, shared collections, and tags."""
        conn = self._connect()
        lib_sql, lib_params = self._library_filter()
        parent = conn.execute(
            f"SELECT itemID FROM items i WHERE key = ? {lib_sql}",
            (key, *lib_params),
        ).fetchone()
        if parent is None:
            return []
        item_id = parent["itemID"]
        related_ids: dict[int, int] = {}

        # Explicit relations: resolve the dc:relation predicate instead of
        # hardcoding an ID that could differ between Zotero databases.
        predicate = conn.execute(
            "SELECT predicateID FROM relationPredicates WHERE predicate = 'dc:relation'"
        ).fetchone()
        if predicate is not None:
            rows = conn.execute(
                "SELECT object FROM itemRelations WHERE itemID = ? AND predicateID = ?",
                (item_id, predicate["predicateID"]),
            ).fetchall()
            for r in rows:
                obj = r["object"]
                rel_key = obj.rsplit("/", 1)[-1] if "/" in obj else obj
                rel_row = conn.execute(
                    f"SELECT itemID FROM items i WHERE key = ? {lib_sql}",
                    (rel_key, *lib_params),
                ).fetchone()
                if rel_row:
                    related_ids[rel_row["itemID"]] = related_ids.get(rel_row["itemID"], 0) + 100

        # Implicit: shared collections
        my_cols = {
            r["collectionID"]
            for r in conn.execute("SELECT collectionID FROM collectionItems WHERE itemID = ?", (item_id,)).fetchall()
        }
        if my_cols:
            placeholders = ",".join("?" * len(my_cols))
            rows = conn.execute(
                f"SELECT itemID, COUNT(*) as cnt FROM collectionItems "
                f"WHERE collectionID IN ({placeholders}) AND itemID != ? "
                f"GROUP BY itemID",
                (*my_cols, item_id),
            ).fetchall()
            for r in rows:
                related_ids[r["itemID"]] = related_ids.get(r["itemID"], 0) + r["cnt"]

        # Implicit: shared tags (2+ overlap)
        my_tags = {
            r["tagID"] for r in conn.execute("SELECT tagID FROM itemTags WHERE itemID = ?", (item_id,)).fetchall()
        }
        if my_tags:
            placeholders = ",".join("?" * len(my_tags))
            rows = conn.execute(
                f"SELECT itemID, COUNT(*) as cnt FROM itemTags "
                f"WHERE tagID IN ({placeholders}) AND itemID != ? "
                f"GROUP BY itemID HAVING cnt >= 2",
                (*my_tags, item_id),
            ).fetchall()
            for r in rows:
                related_ids[r["itemID"]] = related_ids.get(r["itemID"], 0) + r["cnt"] * 5

        sorted_ids = sorted(related_ids, key=lambda x: related_ids[x], reverse=True)[:limit]
        return self._get_items_batch(conn, sorted_ids)

    # --- Private helpers ---

    def _get_items_batch(self, conn: sqlite3.Connection, item_ids: list[int]) -> list[Item]:
        """Resolve multiple item IDs to Items using bulk queries instead of N+1."""
        if not item_ids:
            return []
        if len(item_ids) > _ITEM_BATCH_SIZE:
            return [
                item
                for start in range(0, len(item_ids), _ITEM_BATCH_SIZE)
                for item in self._get_items_batch(conn, item_ids[start : start + _ITEM_BATCH_SIZE])
            ]

        placeholders = ",".join("?" * len(item_ids))

        # Fetch base item rows
        lib_sql, lib_params = self._library_filter()
        rows = conn.execute(
            f"SELECT itemID, itemTypeID, key, dateAdded, dateModified "
            f"FROM items i WHERE itemID IN ({placeholders}) AND itemTypeID {self._get_excluded_sql()} {lib_sql}",
            (*item_ids, *lib_params),
        ).fetchall()
        if not rows:
            return []

        id_to_row = {r["itemID"]: r for r in rows}
        valid_ids = list(id_to_row.keys())
        valid_ph = ",".join("?" * len(valid_ids))

        # Batch fetch item types
        type_ids = list({r["itemTypeID"] for r in rows})
        type_ph = ",".join("?" * len(type_ids))
        type_rows = conn.execute(
            f"SELECT itemTypeID, typeName FROM itemTypes WHERE itemTypeID IN ({type_ph})",
            type_ids,
        ).fetchall()
        type_map = {r["itemTypeID"]: r["typeName"] for r in type_rows}

        # Batch fetch fields
        field_rows = conn.execute(
            f"SELECT id.itemID, f.fieldName, iv.value FROM itemData id "
            f"JOIN fields f ON id.fieldID = f.fieldID "
            f"JOIN itemDataValues iv ON id.valueID = iv.valueID "
            f"WHERE id.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        fields_map: dict[int, dict[str, str]] = {}
        for r in field_rows:
            fields_map.setdefault(r["itemID"], {})[r["fieldName"]] = r["value"]

        # Batch fetch creators
        creator_rows = conn.execute(
            f"SELECT ic.itemID, c.firstName, c.lastName, ct.creatorType "
            f"FROM itemCreators ic "
            f"JOIN creators c ON ic.creatorID = c.creatorID "
            f"JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID "
            f"WHERE ic.itemID IN ({valid_ph}) ORDER BY ic.itemID, ic.orderIndex",
            valid_ids,
        ).fetchall()
        creators_map: dict[int, list[Creator]] = {}
        for r in creator_rows:
            creators_map.setdefault(r["itemID"], []).append(
                Creator(r["firstName"] or "", r["lastName"] or "", r["creatorType"])
            )

        # Batch fetch tags
        tag_rows = conn.execute(
            f"SELECT it.itemID, t.name FROM itemTags it "
            f"JOIN tags t ON it.tagID = t.tagID "
            f"WHERE it.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        tags_map: dict[int, list[str]] = {}
        for r in tag_rows:
            tags_map.setdefault(r["itemID"], []).append(r["name"])

        # Batch fetch collections
        coll_rows = conn.execute(
            f"SELECT ci.itemID, c.key FROM collectionItems ci "
            f"JOIN collections c ON ci.collectionID = c.collectionID "
            f"WHERE ci.itemID IN ({valid_ph})",
            valid_ids,
        ).fetchall()
        colls_map: dict[int, list[str]] = {}
        for r in coll_rows:
            colls_map.setdefault(r["itemID"], []).append(r["key"])

        # Assemble items in original order
        items: list[Item] = []
        for item_id in item_ids:
            if item_id not in id_to_row:
                continue
            row = id_to_row[item_id]
            fields = fields_map.get(item_id, {})
            items.append(
                Item(
                    key=row["key"],
                    item_type=type_map.get(row["itemTypeID"], "unknown"),
                    title=fields.get("title", ""),
                    creators=creators_map.get(item_id, []),
                    abstract=fields.get("abstractNote"),
                    date=fields.get("date"),
                    url=fields.get("url"),
                    doi=fields.get("DOI"),
                    tags=tags_map.get(item_id, []),
                    collections=colls_map.get(item_id, []),
                    date_added=row["dateAdded"],
                    date_modified=row["dateModified"],
                    extra={k: v for k, v in fields.items() if k not in ("title", "abstractNote", "date", "url", "DOI")},
                )
            )
        return items

    def _get_item_fields(self, conn: sqlite3.Connection, item_id: int) -> dict[str, str]:
        rows = conn.execute(
            "SELECT f.fieldName, iv.value FROM itemData id "
            "JOIN fields f ON id.fieldID = f.fieldID "
            "JOIN itemDataValues iv ON id.valueID = iv.valueID "
            "WHERE id.itemID = ?",
            (item_id,),
        ).fetchall()
        return {r["fieldName"]: r["value"] for r in rows}

    def _get_item_creators(self, conn: sqlite3.Connection, item_id: int) -> list[Creator]:
        rows = conn.execute(
            "SELECT c.firstName, c.lastName, ct.creatorType "
            "FROM itemCreators ic "
            "JOIN creators c ON ic.creatorID = c.creatorID "
            "JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID "
            "WHERE ic.itemID = ? ORDER BY ic.orderIndex",
            (item_id,),
        ).fetchall()
        return [Creator(r["firstName"] or "", r["lastName"] or "", r["creatorType"]) for r in rows]

    def _get_item_tags(self, conn: sqlite3.Connection, item_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT t.name FROM itemTags it JOIN tags t ON it.tagID = t.tagID WHERE it.itemID = ?",
            (item_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def _get_item_collections(self, conn: sqlite3.Connection, item_id: int) -> list[str]:
        rows = conn.execute(
            "SELECT c.key FROM collectionItems ci "
            "JOIN collections c ON ci.collectionID = c.collectionID "
            "WHERE ci.itemID = ?",
            (item_id,),
        ).fetchall()
        return [r["key"] for r in rows]
