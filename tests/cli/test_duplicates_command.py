"""重复条目检测测试。"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

from tests.support import SAMPLE_DATA_DIR
from tests.support import invoke_cli as _invoke

from zotero_cli.zotero.reader import ZoteroReader


class TestDuplicateReader:
    def test_find_duplicates_doi(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="doi")
            assert len(groups) >= 1
            doi_group = [g for g in groups if g.match_type == "doi"][0]
            keys = {i.key for i in doi_group.items}
            assert "ATTN001" in keys
            assert "DUPE008" in keys
            assert doi_group.score == 1.0
        finally:
            reader.close()

    def test_find_duplicates_title(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="title", threshold=0.7)
            # ATTN001 与 DUPE008 的标题非常相似
            found = False
            for g in groups:
                keys = {i.key for i in g.items}
                if "ATTN001" in keys and "DUPE008" in keys:
                    found = True
                    assert g.match_type == "title"
                    assert g.score >= 0.7
            assert found
        finally:
            reader.close()

    def test_find_duplicates_both(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="both")
            assert len(groups) >= 1
        finally:
            reader.close()

    def test_find_duplicates_no_matches(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="doi", limit=0)
            assert len(groups) == 0
        finally:
            reader.close()

    def test_find_duplicates_respects_limit(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="both", limit=1)
            assert len(groups) <= 1
        finally:
            reader.close()

    def test_title_matches_with_conflicting_dois_are_not_duplicates(self, tmp_path):
        """标题相似但 DOI 不同的记录不得被自动清理。"""
        db_path = tmp_path / "zotero.sqlite"
        shutil.copy2(SAMPLE_DATA_DIR / "zotero.sqlite", db_path)
        with sqlite3.connect(db_path) as conn:
            different_doi_value_id = conn.execute(
                "SELECT id.valueID FROM itemData id "
                "JOIN items i ON id.itemID = i.itemID "
                "JOIN fields f ON id.fieldID = f.fieldID "
                "WHERE i.key = 'BERT002' AND f.fieldName = 'DOI'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE itemData SET valueID = ? WHERE itemID = (SELECT itemID FROM items WHERE key = 'DUPE008') "
                "AND fieldID = (SELECT fieldID FROM fields WHERE fieldName = 'DOI')",
                (different_doi_value_id,),
            )

        reader = ZoteroReader(db_path)
        try:
            groups = reader.find_duplicates(strategy="title", threshold=0.7)
            assert not any({"ATTN001", "DUPE008"} <= {item.key for item in group.items} for group in groups)
        finally:
            reader.close()

    def test_find_duplicates_stops_after_doi_limit(self):
        reader = ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite")
        try:
            with patch("zotero_cli.zotero.reader.SequenceMatcher") as matcher:
                groups = reader.find_duplicates(strategy="both", limit=1)
            assert len(groups) == 1
            assert groups[0].match_type == "doi"
            matcher.assert_not_called()
        finally:
            reader.close()


class TestDuplicatesCLI:
    def test_duplicates_json(self):
        result = _invoke(["zotero", "duplicates", "--by", "doi"], json_output=True)
        assert result.exit_code != 0
        data = json.loads(result.output)["data"]
        assert len(data) >= 1
        assert data[0]["match_type"] == "doi"

    def test_duplicates_table(self):
        result = _invoke(["zotero", "duplicates"])
        assert result.exit_code != 0
        assert "ATTN001" in result.output or "DUPE008" in result.output

    def test_duplicates_by_title(self):
        # 默认 0.85 阈值可能匹配不到，这里针对该夹具调低阈值。
        result = _invoke(["zotero", "duplicates", "--by", "title", "--threshold", "0.7"], json_output=True)
        assert result.exit_code != 0


# --- 重复检测的范围与复杂度（读取器层） ---


def test_find_duplicates_may_include_trashed_items(tmp_path: Path) -> None:
    """find_duplicates 保持文档约定的混合回收站范围。

    回收站中的条目不会被排除在重复候选集之外，因此已删除的副本仍可能出现在重复分组里。
    """
    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(SAMPLE_DATA_DIR / "zotero.sqlite", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO deletedItems (itemID) VALUES (8)")  # DUPE008
    with ZoteroReader(db_path) as reader:
        doi_groups = reader.find_duplicates(strategy="doi")
        title_groups = reader.find_duplicates(strategy="title", threshold=0.7)
    doi_keys = {item.key for group in doi_groups for item in group.items}
    title_keys = {item.key for group in title_groups for item in group.items}
    assert {"ATTN001", "DUPE008"} <= doi_keys
    assert {"ATTN001", "DUPE008"} <= title_keys


def test_find_duplicates_scoped_to_requested_library() -> None:
    """群组文库的行不得在默认文库视图中产生重复。"""
    with ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite") as reader:
        groups = reader.find_duplicates(strategy="both")
        assert all("GRPITM09" not in {item.key for item in group.items} for group in groups)
    with ZoteroReader(SAMPLE_DATA_DIR / "zotero.sqlite", library_id=2) as reader:
        assert reader.find_duplicates(strategy="both") == []


def test_find_duplicates_uses_blocking_instead_of_full_scan(tmp_path: Path, monkeypatch) -> None:
    """分块策略把 SequenceMatcher 调用次数压到远低于二次方基线。"""
    from zotero_cli.zotero import reader as reader_module

    db_path = tmp_path / "zotero.sqlite"
    shutil.copy2(SAMPLE_DATA_DIR / "zotero.sqlite", db_path)
    with sqlite3.connect(db_path) as conn:
        for table in ("itemData", "itemDataValues", "items"):
            conn.execute(f"DELETE FROM {table}")
        rows = []
        values = []
        data = []
        for index in range(1200):
            item_id = 1000 + index
            rows.append((item_id, 2, 1, f"SYN{index:07d}", "2024-01-01", "2024-01-02"))
            values.append((100000 + index, f"Unique research topic number {index}"))
            data.append((item_id, 4, 100000 + index))
        # 一对真实的近似重复记录。
        rows.append((3000, 2, 1, "SYNPAIR1", "2024-01-01", "2024-01-02"))
        rows.append((3001, 2, 1, "SYNPAIR2", "2024-01-01", "2024-01-02"))
        values.append((200000, "Deep learning methods for battery materials"))
        values.append((200001, "Deep learning methods for battery material"))
        data.extend([(3000, 4, 200000), (3001, 4, 200001)])
        conn.executemany(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key, dateAdded, dateModified) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.executemany("INSERT INTO itemDataValues (valueID, value) VALUES (?, ?)", values)
        conn.executemany("INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)", data)

    calls = {"count": 0}
    real_sequence_matcher = reader_module.SequenceMatcher

    def counting_sequence_matcher(*args, **kwargs):
        calls["count"] += 1
        return real_sequence_matcher(*args, **kwargs)

    monkeypatch.setattr(reader_module, "SequenceMatcher", counting_sequence_matcher)
    with ZoteroReader(db_path) as reader:
        groups = reader.find_duplicates(strategy="title", threshold=0.85, limit=10)
    assert groups, "near-duplicate pair should still be detected"
    assert calls["count"] < 5000, f"blocking should bound comparisons, called {calls['count']} times"
