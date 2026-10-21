from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.support import FIXTURES_DIR

from zotero_cli.models import Item
from zotero_cli.zotero.reader import ZoteroReader


def _read_items(reader: ZoteroReader, method: str) -> list[Item]:
    if method == "collection":
        return reader.get_collection_items("COLML01")
    return reader.get_related_items("ATTN001", limit=100)


@pytest.mark.parametrize(
    ("method", "keys"),
    [
        ("collection", ["ATTN001", "BERT002", "DEEP003", "SCAL006"]),
        ("related", ["BERT002", "DEEP003", "SCAL006"]),
    ],
)
def test_batch_reads_preserve_order_and_metadata(test_db_path: Path, method: str, keys: list[str]) -> None:
    with ZoteroReader(test_db_path) as reader:
        expected = [reader.get_item(key) for key in keys]
        assert _read_items(reader, method) == expected


@pytest.mark.parametrize("method", ["collection", "related"])
def test_query_count_does_not_grow_with_result_count(test_db_path: Path, tmp_path: Path, method: str) -> None:
    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(test_db_path, db_path)

    def query_count() -> tuple[int, int]:
        with ZoteroReader(db_path) as reader:
            conn = reader._connect()
            reader._get_excluded_sql()
            queries: list[str] = []
            conn.set_trace_callback(queries.append)
            items = _read_items(reader, method)
            return len(queries), len(items)

    small_queries, small_results = query_count()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 2, 1, ?)",
            [(i, f"BATCH{i}") for i in range(100, 140)],
        )
        conn.executemany(
            "INSERT INTO collectionItems (collectionID, itemID) VALUES (1, ?)",
            [(i,) for i in range(100, 140)] + [(4,), (5,)],
        )
    large_queries, large_results = query_count()
    assert large_results == small_results + 40  # Notes and attachments stay excluded.
    assert large_queries == small_queries


@pytest.mark.parametrize("method", ["collection", "related"])
def test_batch_reads_preserve_group_library_filter(test_db_path: Path, method: str) -> None:
    with ZoteroReader(test_db_path, library_id=2) as reader:
        assert _read_items(reader, method) == []
        group_items = reader.get_collection_items("GRPCOL03")
        assert group_items == [reader.get_item("GRPITM09")]


def test_related_items_preserve_limit(test_db_path: Path) -> None:
    with ZoteroReader(test_db_path) as reader:
        assert reader.get_related_items("ATTN001", limit=0) == []
        assert reader.get_related_items("ATTN001", limit=1) == [reader.get_item("BERT002")]


def test_missing_collection_and_related_item(test_db_path: Path) -> None:
    with ZoteroReader(test_db_path) as reader:
        assert reader.get_collection_items("MISSING") == []
        assert reader.get_related_items("MISSING") == []


def test_large_collection_is_hydrated_in_batches(
    test_db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A collection larger than the batch size is hydrated through multiple queries."""
    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(test_db_path, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 2, 2, ?)",
            [(i, f"BATCH{i}") for i in range(100, 120)],
        )
        conn.executemany(
            "INSERT INTO collectionItems (collectionID, itemID) VALUES (3, ?)",
            [(i,) for i in range(100, 120)],
        )
    # Force the multi-query path without depending on the Python 3.11+ sqlite3 API.
    monkeypatch.setattr("zotero_cli.zotero.reader._ITEM_BATCH_SIZE", 7)
    with ZoteroReader(db_path, library_id=2) as reader:
        items = reader.get_collection_items("GRPCOL03")
        assert [item.key for item in items] == ["GRPITM09", *[f"BATCH{i}" for i in range(100, 120)]]


# --- M-06: every IN (...) query is batched under the legacy variable limit ---


def _seed_large_user_library(db_path: Path, count: int = 1000) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 2, 1, ?)",
            [(2000 + i, f"BIG{i:05d}") for i in range(count)],
        )
        conn.executemany(
            "INSERT INTO itemDataValues (valueID, value) VALUES (?, ?)",
            [(5000 + i, f"Batched title number {i:05d}") for i in range(count)],
        )
        conn.executemany(
            "INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, 4, ?)",
            [(2000 + i, 5000 + i) for i in range(count)],
        )


def test_search_filter_and_sort_respect_legacy_parameter_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the multi-query path on every Python version (no sqlite3 setlimit dependency).
    from zotero_cli.zotero import reader as reader_module

    monkeypatch.setattr(reader_module, "_ITEM_BATCH_SIZE", 100)
    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
    _seed_large_user_library(db_path)
    with ZoteroReader(db_path) as reader:
        typed = reader.search("", item_type="journalArticle", limit=5)
        assert typed.total > 100
        assert len(typed.items) == 5
        titled = reader.search("", sort="title", direction="asc", limit=5)
        assert len(titled.items) == 5
        dated = reader.search("", sort="dateAdded", direction="desc", limit=5)
        assert len(dated.items) == 5
        creators = reader.search("", sort="creator", direction="asc", limit=5)
        assert len(creators.items) == 5
        assert all(group.match_type == "doi" for group in reader.find_duplicates(strategy="doi", limit=5))


def test_batched_sort_keeps_requested_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from zotero_cli.zotero import reader as reader_module

    monkeypatch.setattr(reader_module, "_ITEM_BATCH_SIZE", 100)
    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
    _seed_large_user_library(db_path, count=950)
    with ZoteroReader(db_path) as reader:
        titles = [item.title for item in reader.search("", sort="title", direction="asc", limit=6).items]
        assert titles == sorted(titles, key=str.lower)
        dates = [item.date_added for item in reader.search("", sort="dateAdded", direction="asc", limit=6).items]
        assert dates == sorted(dates)
