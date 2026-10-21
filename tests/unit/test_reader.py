import sqlite3
from pathlib import Path

import pytest

from zotero_cli.zotero import reader as reader_module
from zotero_cli.zotero.reader import ZoteroReader


@pytest.fixture
def reader(test_db_path: Path) -> ZoteroReader:
    return ZoteroReader(test_db_path)


class TestGetItem:
    def test_get_existing_item(self, reader: ZoteroReader):
        item = reader.get_item("ATTN001")
        assert item is not None
        assert item.title == "Attention Is All You Need"
        assert item.item_type == "journalArticle"
        assert item.doi == "10.5555/attention"
        assert item.date == "2017"
        assert len(item.creators) == 2
        assert item.creators[0].last_name == "Vaswani"

    def test_get_nonexistent_item(self, reader: ZoteroReader):
        item = reader.get_item("NONEXIST")
        assert item is None

    def test_get_item_tags(self, reader: ZoteroReader):
        item = reader.get_item("ATTN001")
        assert "transformer" in item.tags
        assert "attention" in item.tags

    def test_get_item_collections(self, reader: ZoteroReader):
        item = reader.get_item("ATTN001")
        assert "COLML01" in item.collections

    def test_get_book(self, reader: ZoteroReader):
        item = reader.get_item("DEEP003")
        assert item.item_type == "book"
        assert item.title == "Deep Learning"


class TestSearch:
    def test_search_by_title(self, reader: ZoteroReader):
        result = reader.search("attention")
        assert result.total > 0
        assert any(i.key == "ATTN001" for i in result.items)

    def test_search_by_creator(self, reader: ZoteroReader):
        result = reader.search("Vaswani")
        assert result.total > 0
        assert any(i.key == "ATTN001" for i in result.items)

    def test_search_by_tag(self, reader: ZoteroReader):
        result = reader.search("transformer")
        assert result.total >= 2

    def test_search_no_results(self, reader: ZoteroReader):
        result = reader.search("nonexistentquery12345")
        assert result.total == 0

    def test_search_with_collection_filter(self, reader: ZoteroReader):
        result = reader.search("", collection="Transformers")
        assert result.total > 0
        assert all(i.key == "ATTN001" for i in result.items)

    def test_search_with_collection_filter_by_key(self, reader: ZoteroReader):
        result = reader.search("", collection="COLML01")
        assert result.total > 0

    def test_search_with_limit(self, reader: ZoteroReader):
        result = reader.search("", limit=1)
        assert len(result.items) == 1


class TestSearchFulltextBudget:
    def test_metadata_search_reports_not_limited(self, reader: ZoteroReader):
        result = reader.search("", limit=1)
        assert result.fulltext_limited is False

    def test_fulltext_scan_is_complete_under_default_budget(self, reader: ZoteroReader):
        result = reader.search("e")
        assert result.fulltext_limited is False

    def test_search_propagates_fulltext_limited_flag(self, reader: ZoteroReader, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            type(reader),
            "_search_fulltext",
            lambda self, *args, **kwargs: (set(), True),
        )
        result = reader.search("e")
        assert result.fulltext_limited is True

    @staticmethod
    def _budget_conn() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER, libraryID INTEGER)")
        conn.execute("CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INTEGER)")
        conn.execute("CREATE TABLE fulltextItems (itemID INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY)")
        # 键与夹具存储目录一致，使缓存文件可被读取。
        for attachment_id, parent_id, key in ((5, 1, "ATCH005"), (10, 2, "GRPATT10")):
            conn.execute("INSERT INTO items VALUES (?, ?, 2, 1)", (attachment_id, key))
            conn.execute("INSERT INTO items VALUES (?, ?, 2, 1)", (parent_id, f"ITEM{parent_id:02d}"))
            conn.execute("INSERT INTO itemAttachments VALUES (?, ?)", (attachment_id, parent_id))
            conn.execute("INSERT INTO fulltextItems VALUES (?)", (attachment_id,))
        return conn

    def test_file_budget_marks_scan_limited(self, reader: ZoteroReader, monkeypatch: pytest.MonkeyPatch):
        conn = self._budget_conn()
        try:
            monkeypatch.setattr(reader_module, "_FULLTEXT_CACHE_MAX_FILES", 1)
            _matched, limited = reader._cached_fulltext_ids(
                conn, "needle", "NOT IN (?,?)", (14, 26), "AND i.libraryID = ?", (1,)
            )
        finally:
            conn.close()
        assert limited is True

    def test_byte_budget_marks_scan_limited(self, reader: ZoteroReader, monkeypatch: pytest.MonkeyPatch):
        conn = self._budget_conn()
        try:
            monkeypatch.setattr(reader_module, "_FULLTEXT_CACHE_MAX_BYTES_PER_FILE", 1)
            monkeypatch.setattr(reader_module, "_FULLTEXT_CACHE_MAX_TOTAL_BYTES", 1)
            _matched, limited = reader._cached_fulltext_ids(
                conn, "needle", "NOT IN (?,?)", (14, 26), "AND i.libraryID = ?", (1,)
            )
        finally:
            conn.close()
        assert limited is True


class TestNotes:
    def test_get_notes(self, reader: ZoteroReader):
        notes = reader.get_notes("ATTN001")
        assert len(notes) == 1
        assert "transformer architecture" in notes[0].content

    def test_get_notes_empty(self, reader: ZoteroReader):
        notes = reader.get_notes("DEEP003")
        assert len(notes) == 0


class TestCollections:
    def test_get_collections(self, reader: ZoteroReader):
        collections = reader.get_collections()
        assert len(collections) >= 1
        ml = next(c for c in collections if c.name == "Machine Learning")
        assert any(ch.name == "Transformers" for ch in ml.children)

    def test_get_collection_items(self, reader: ZoteroReader):
        items = reader.get_collection_items("COLML01")
        assert len(items) >= 2


class TestAttachments:
    def test_get_attachments(self, reader: ZoteroReader):
        attachments = reader.get_attachments("ATTN001")
        assert len(attachments) == 1
        assert attachments[0].content_type == "application/pdf"
        assert attachments[0].filename == "attention.pdf"

    def test_get_pdf_attachment(self, reader: ZoteroReader):
        att = reader.get_pdf_attachment("ATTN001")
        assert att is not None
        assert att.key == "ATCH005"

    def test_get_pdf_attachment_none(self, reader: ZoteroReader):
        att = reader.get_pdf_attachment("DEEP003")
        assert att is None


class TestContextManager:
    def test_context_manager(self, test_db_path: Path):
        with ZoteroReader(test_db_path) as reader:
            item = reader.get_item("ATTN001")
            assert item is not None
        # 退出上下文后连接应已关闭
        assert reader._conn is None


class TestSchemaVersion:
    def test_check_schema_version(self, reader: ZoteroReader):
        version = reader.get_schema_version()
        assert version is not None
        assert isinstance(version, int)


class TestExportCitation:
    def test_export_bibtex(self, reader: ZoteroReader):
        bib = reader.export_citation("ATTN001", fmt="bibtex")
        assert "@article" in bib
        assert "Attention" in bib
        assert "Vaswani" in bib

    def test_export_csl_json(self, reader: ZoteroReader):
        import json

        csl = reader.export_citation("ATTN001", fmt="csl-json")
        assert csl is not None
        data = json.loads(csl)
        assert data["type"] == "article-journal"
        assert data["title"] == "Attention Is All You Need"
        assert data["DOI"] == "10.5555/attention"
        assert any(a["family"] == "Vaswani" for a in data["author"])

    def test_export_ris(self, reader: ZoteroReader):
        ris = reader.export_citation("ATTN001", fmt="ris")
        assert ris is not None
        assert "TY  - JOUR" in ris
        assert "TI  - Attention Is All You Need" in ris
        assert "AU  - Vaswani," in ris
        assert "DO  - 10.5555/attention" in ris
        assert "ER  - " in ris

    def test_export_unsupported_format(self, reader: ZoteroReader):
        result = reader.export_citation("ATTN001", fmt="xml")
        assert result is None

    def test_export_nonexistent(self, reader: ZoteroReader):
        bib = reader.export_citation("NONEXIST", fmt="bibtex")
        assert bib is None


class TestRelatedItems:
    def test_explicit_relation(self, reader: ZoteroReader):
        related = reader.get_related_items("ATTN001")
        keys = [i.key for i in related]
        assert "BERT002" in keys

    def test_implicit_relation_shared_tags(self, reader: ZoteroReader):
        related = reader.get_related_items("ATTN001")
        keys = [i.key for i in related]
        assert "BERT002" in keys

    def test_no_relations(self, reader: ZoteroReader):
        related = reader.get_related_items("DEEP003")
        assert isinstance(related, list)


# --- 用户查询中的 LIKE 通配符会被转义 ---


def test_like_pattern_escapes_user_wildcards() -> None:
    from zotero_cli.zotero.reader import ZoteroReader

    assert ZoteroReader._like_pattern("100%") == "%100\\%%"
    assert ZoteroReader._like_pattern("a_b") == "%a\\_b%"
    assert ZoteroReader._like_pattern("50%_x") == "%50\\%\\_x%"


def test_search_treats_percent_and_underscore_as_literal_text(test_db_path: Path, tmp_path: Path) -> None:
    import shutil
    import sqlite3

    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(test_db_path, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (99, 2, 1, 'PERCENT1')")
        conn.execute("INSERT INTO itemDataValues VALUES (900, 'Yield of 100% coverage')")
        conn.execute("INSERT INTO itemData VALUES (99, 4, 900)")  # fixture title fieldID = 4
    with ZoteroReader(db_path) as reader:
        assert [item.key for item in reader.search("100%", limit=10).items] == ["PERCENT1"]
        # 此处 "_" 不是通配符，因此只有字面下划线才会匹配。
        assert reader.search("_", limit=10).total == 0
        assert reader.search("%", limit=10).total == 1


# --- dc:relation 谓词经解析得到，而非硬编码 ---


def test_related_items_resolve_relation_predicate_by_name(test_db_path: Path, tmp_path: Path) -> None:
    import shutil
    import sqlite3

    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(test_db_path, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE relationPredicates SET predicateID = 5 WHERE predicate = 'dc:relation'")
        conn.execute("UPDATE itemRelations SET predicateID = 5")
    with ZoteroReader(db_path) as reader:
        related = reader.get_related_items("ATTN001", limit=10)
    assert "BERT002" in [item.key for item in related]
