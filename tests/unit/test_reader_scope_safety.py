"""Reader library/trash boundaries, exercised only on temporary SQLite fixtures."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zotero_cli.core.zotero.reader import ZoteroReader


@pytest.fixture
def scope_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "zotero.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
            INSERT INTO itemTypes VALUES (2, 'journalArticle'), (14, 'attachment'), (26, 'note'), (37, 'preprint');
            CREATE TABLE items (
                itemID INTEGER PRIMARY KEY, itemTypeID INT, libraryID INT, key TEXT,
                dateAdded TEXT DEFAULT '2024-01-01', dateModified TEXT DEFAULT '2024-01-02',
                UNIQUE (libraryID, key)
            );
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            INSERT INTO fields VALUES (1, 'title'), (2, 'url'), (3, 'DOI');
            CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
            CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
            INSERT INTO creatorTypes VALUES (1, 'author');
            CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO tags VALUES (1, 'personal'), (2, 'group'), (3, 'common-a'), (4, 'common-b');
            CREATE TABLE itemTags (itemID INT, tagID INT);
            CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT,
                parentCollectionID INT, libraryID INT, key TEXT, UNIQUE (libraryID, key));
            INSERT INTO collections VALUES (1, 'Personal', NULL, 1, 'PERSONCL'),
                (2, 'Group', NULL, 2, 'GROUPCOL'), (3, 'Personal shared', NULL, 1, 'SHARECOL'),
                (4, 'Group shared', NULL, 2, 'SHARECOL');
            CREATE TABLE collectionItems (collectionID INT, itemID INT);
            CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT);
            CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, contentType TEXT, path TEXT);
            CREATE TABLE relationPredicates (predicateID INTEGER PRIMARY KEY, predicate TEXT UNIQUE);
            INSERT INTO relationPredicates VALUES (1, 'dc:relation');
            CREATE TABLE itemRelations (itemID INT, predicateID INT, object TEXT);
            CREATE TABLE fulltextWords (wordID INTEGER PRIMARY KEY, word TEXT);
            CREATE TABLE fulltextItemWords (wordID INT, itemID INT);
            CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT DEFAULT '2024-02-01');
        """)
        parents = [(1, 1, "PERSON01"), (2, 2, "GROUP001"), (3, 1, "SHARED01"), (4, 2, "SHARED01")]
        for item_id, library_id, key in parents:
            conn.execute(
                "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 2, ?, ?)", (item_id, library_id, key)
            )
            conn.execute("INSERT INTO itemDataValues VALUES (?, ?)", (item_id, f"Scope paper {item_id}"))
            conn.execute("INSERT INTO itemData VALUES (?, 1, ?)", (item_id, item_id))
            conn.execute("INSERT INTO creators VALUES (?, 'Scope', 'Author')", (item_id,))
            conn.execute("INSERT INTO itemCreators VALUES (?, ?, 1, 0)", (item_id, item_id))
            conn.executemany("INSERT INTO itemTags VALUES (?, ?)", [(item_id, library_id), (item_id, 3), (item_id, 4)])
            conn.execute("INSERT INTO collectionItems VALUES (?, ?)", (item_id, item_id))
            conn.execute(
                "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 26, ?, ?)",
                (100 + item_id, library_id, f"NOTE{item_id:04}"),
            )
            conn.execute(
                "INSERT INTO itemNotes VALUES (?, ?, ?)", (100 + item_id, item_id, f"<p>Note for parent {item_id}</p>")
            )
            conn.execute(
                "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, 14, ?, ?)",
                (200 + item_id, library_id, f"FILE{item_id:04}"),
            )
            conn.execute(
                "INSERT INTO itemAttachments VALUES (?, ?, 'application/pdf', ?)",
                (200 + item_id, item_id, f"storage:paper{item_id}.pdf"),
            )
    return db_path


@pytest.mark.parametrize("method", ["get_notes", "get_attachments", "get_pdf_attachments", "get_pdf_attachment"])
@pytest.mark.parametrize("key", ["PERSON01", "SHARED01"])
def test_group_child_reads_respect_parent_library(scope_db: Path, method: str, key: str) -> None:
    with ZoteroReader(scope_db, library_id=2) as reader:
        result = getattr(reader, method)(key)
        if key == "PERSON01":
            assert reader.get_item(key) is None
            assert result == (None if method == "get_pdf_attachment" else [])
        else:
            item = reader.get_item(key)
            assert item is not None
            assert item.title == "Scope paper 4"
            expected_key = "NOTE0004" if method == "get_notes" else "FILE0004"
            assert (result.key if method == "get_pdf_attachment" else [child.key for child in result]) == (
                expected_key if method == "get_pdf_attachment" else [expected_key]
            )


@pytest.mark.parametrize("field", ["total_items", "by_type", "top_tags", "collections", "pdf_attachments", "notes"])
def test_group_statistics_use_one_library(scope_db: Path, field: str) -> None:
    expected = {
        "total_items": 2,
        "by_type": {"journalArticle": 2},
        "top_tags": {"group": 2, "common-a": 2, "common-b": 2},
        "collections": {"Group": 1, "Group shared": 1},
        "pdf_attachments": 2,
        "notes": 2,
    }
    with ZoteroReader(scope_db, library_id=2) as reader:
        assert reader.get_stats()[field] == expected[field]


def test_group_collection_lookup_handles_library_local_keys(scope_db: Path) -> None:
    with ZoteroReader(scope_db, library_id=2) as reader:
        items = reader.get_collection_items("SHARECOL")
        assert [item.title for item in items] == ["Scope paper 4"]
        assert reader.get_collection_items("PERSONCL") == []


@pytest.mark.parametrize("query", ["", "Scope", "Author", "common", "fulltext-only"])
@pytest.mark.parametrize("library_id", [1, 2])
def test_search_excludes_trash_before_counting_and_paging(scope_db: Path, query: str, library_id: int) -> None:
    with sqlite3.connect(scope_db) as conn:
        conn.executemany("INSERT INTO deletedItems (itemID) VALUES (?)", [(1,), (2,)])
        conn.execute("INSERT INTO fulltextWords VALUES (1, 'fulltext-only')")
        conn.executemany("INSERT INTO fulltextItemWords VALUES (1, ?)", [(201,), (202,), (203,), (204,)])
    with ZoteroReader(scope_db, library_id=library_id) as reader:
        result = reader.search(query, sort="title", direction="asc", limit=1)
        assert result.total == (2 if library_id == 1 else 1)
        assert [item.title for item in result.items] == [f"Scope paper {3 if library_id == 1 else 4}"]
        assert len(reader.search(query, limit=1, offset=1).items) == (1 if library_id == 1 else 0)


@pytest.mark.parametrize("sort", ["dateAdded", "dateModified"])
@pytest.mark.parametrize("library_id", [1, 2])
def test_recent_excludes_trash_before_limit(scope_db: Path, sort: str, library_id: int) -> None:
    with sqlite3.connect(scope_db) as conn:
        conn.executemany("INSERT INTO deletedItems (itemID) VALUES (?)", [(1,), (2,)])
        conn.execute("UPDATE items SET dateAdded = '2025-01-01', dateModified = '2025-01-01' WHERE itemID IN (1, 2)")
    with ZoteroReader(scope_db, library_id=library_id) as reader:
        items = reader.get_recent_items("2000-01-01", sort=sort, limit=1)
        assert len(items) == 1
        assert items[0].title in (["Scope paper 3", "Scope paper 4"] if library_id == 1 else ["Scope paper 4"])


@pytest.mark.parametrize("method", ["get_notes", "get_attachments", "get_pdf_attachments", "get_pdf_attachment"])
@pytest.mark.parametrize("parent_deleted", [False, True])
def test_child_reads_exclude_deleted_children_or_parent(scope_db: Path, method: str, parent_deleted: bool) -> None:
    with sqlite3.connect(scope_db) as conn:
        conn.executescript("""
            INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES
                (302, 26, 2, 'LIVENOTE'), (402, 14, 2, 'LIVEFILE');
            INSERT INTO itemNotes VALUES (302, 2, '<p>Live note</p>');
            INSERT INTO itemAttachments VALUES (402, 2, 'application/pdf', 'storage:live.pdf');
            INSERT INTO deletedItems (itemID) VALUES (102), (202);
        """)
        if parent_deleted:
            conn.execute("INSERT INTO deletedItems (itemID) VALUES (2)")
    with ZoteroReader(scope_db, library_id=2) as reader:
        result = getattr(reader, method)("GROUP001")
        if parent_deleted:
            assert result == (None if method == "get_pdf_attachment" else [])
        else:
            expected_key = "LIVENOTE" if method == "get_notes" else "LIVEFILE"
            assert (result.key if method == "get_pdf_attachment" else [child.key for child in result]) == (
                expected_key if method == "get_pdf_attachment" else [expected_key]
            )
