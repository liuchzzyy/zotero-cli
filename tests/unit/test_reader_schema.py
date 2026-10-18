"""Schema-parity and schema-version guard tests for the local Zotero fixture."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from zotero_cli.core.zotero.reader import ZoteroReader

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
GENERATOR_PATH = FIXTURES_DIR / "create_test_db.py"
BINARY_FIXTURE = FIXTURES_DIR / "zotero.sqlite"


def _load_generator():
    spec = importlib.util.spec_from_file_location("fixture_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema(db_path: Path) -> dict[str, list[str]]:
    with sqlite3.connect(db_path) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] for table in sorted(tables)}


def test_generator_and_binary_fixture_schema_are_in_sync(tmp_path: Path) -> None:
    """H-09: the checked-in fixture must match what the generator produces."""
    generator = _load_generator()
    generated = tmp_path / "generated.sqlite"
    generator.create_test_db(generated)

    assert _schema(generated) == _schema(BINARY_FIXTURE)
    with sqlite3.connect(generated) as conn:
        assert conn.execute("SELECT version FROM version WHERE schema = 'userdata'").fetchone() == (130,)


def test_binary_fixture_uses_v130_fulltext_schema() -> None:
    """H-09: fixture models Zotero userdata v130 (fulltextItems, no word tables)."""
    with sqlite3.connect(BINARY_FIXTURE) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "fulltextItems" in tables
    assert "fulltextWords" not in tables
    assert "fulltextItemWords" not in tables
    assert (FIXTURES_DIR / "storage" / "ATCH005" / ".zotero-ft-cache").is_file()
    assert (FIXTURES_DIR / "storage" / "GRPATT10" / ".zotero-ft-cache").is_file()


def test_legacy_fulltext_schema_still_supported(tmp_path: Path) -> None:
    """C-01: Zotero 6 databases with word tables keep working."""
    db_path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
            INSERT INTO itemTypes VALUES (2, 'journalArticle'), (14, 'attachment'), (26, 'note');
            CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, libraryID INT, key TEXT,
                dateAdded TEXT, dateModified TEXT);
            INSERT INTO items VALUES (1, 2, 1, 'PARENT1', '2024-01-01', '2024-01-01');
            INSERT INTO items VALUES (2, 14, 1, 'ATTACH1', '2024-01-01', '2024-01-01');
            CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            INSERT INTO fields VALUES (1, 'title');
            CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
            CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
            CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE itemTags (itemID INT, tagID INT);
            CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT,
                parentCollectionID INT, libraryID INT, key TEXT);
            CREATE TABLE collectionItems (collectionID INT, itemID INT);
            CREATE TABLE relationPredicates (predicateID INTEGER PRIMARY KEY, predicate TEXT);
            CREATE TABLE itemRelations (itemID INT, predicateID INT, object TEXT);
            CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, contentType TEXT, path TEXT);
            CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT);
            CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
            CREATE TABLE fulltextWords (wordID INTEGER PRIMARY KEY, word TEXT);
            CREATE TABLE fulltextItemWords (wordID INT, itemID INT);
            CREATE TABLE version (schema TEXT PRIMARY KEY, version INT);
            INSERT INTO version VALUES ('userdata', 120);
            INSERT INTO itemAttachments VALUES (2, 1, 'application/pdf', 'storage:paper.pdf');
            INSERT INTO fulltextWords VALUES (1, 'legacyword');
            INSERT INTO fulltextItemWords VALUES (1, 2);
        """)
    with ZoteroReader(db_path) as reader:
        assert reader.get_schema_version() == 120
        result = reader.search("legacyword", limit=10)
    assert [item.key for item in result.items] == ["PARENT1"]


def test_search_prefers_v130_fulltext_cache_over_missing_legacy_tables(tmp_path) -> None:
    """C-01: a database without any fulltext table degrades to metadata search."""
    db_path = tmp_path / "no-fulltext.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
            INSERT INTO itemTypes VALUES (2, 'journalArticle'), (14, 'attachment'), (26, 'note');
            CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INT, libraryID INT, key TEXT,
                dateAdded TEXT, dateModified TEXT);
            INSERT INTO items VALUES (1, 2, 1, 'PARENT1', '2024-01-01', '2024-01-01');
            CREATE TABLE itemData (itemID INT, fieldID INT, valueID INT);
            CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
            CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
            INSERT INTO fields VALUES (1, 'title');
            INSERT INTO itemDataValues VALUES (1, 'A metadata title');
            INSERT INTO itemData VALUES (1, 1, 1);
            CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
            CREATE TABLE creatorTypes (creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
            CREATE TABLE itemCreators (itemID INT, creatorID INT, creatorTypeID INT, orderIndex INT);
            CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE itemTags (itemID INT, tagID INT);
            CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT,
                parentCollectionID INT, libraryID INT, key TEXT);
            CREATE TABLE collectionItems (collectionID INT, itemID INT);
            CREATE TABLE relationPredicates (predicateID INTEGER PRIMARY KEY, predicate TEXT);
            CREATE TABLE itemRelations (itemID INT, predicateID INT, object TEXT);
            CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INT, contentType TEXT, path TEXT);
            CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INT, note TEXT);
            CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
            CREATE TABLE version (schema TEXT PRIMARY KEY, version INT);
            INSERT INTO version VALUES ('userdata', 130);
        """)
    with ZoteroReader(db_path) as reader:
        assert reader.search("metadata", limit=10).total == 1
        assert reader.search("missingword", limit=10).total == 0
