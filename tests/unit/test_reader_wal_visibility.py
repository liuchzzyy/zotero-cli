"""只存在于 WAL 中的行也必须对只读读取器可见。

真实条目上暴露过问题：Zotero Desktop 运行时用 `immutable=1` 读到的是同步前的
快照，附件会被误判为缺失。
"""

from __future__ import annotations

import sqlite3

from zotero_cli.zotero.access.reader import ZoteroReader


class TestWalVisibility:
    def test_reads_rows_that_live_only_in_the_wal(self, tmp_path):
        db = tmp_path / "zotero.sqlite"
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
        writer.execute("INSERT INTO items VALUES (1, 'OLD')")
        writer.commit()

        # 第二行在检查点执行前只存在于 WAL 中。若用 immutable=1 就会
        # 完全读不到它，这正是快照过期那个缺陷。
        writer.execute("INSERT INTO items VALUES (2, 'NEW')")
        writer.commit()
        assert (tmp_path / "zotero.sqlite-wal").stat().st_size > 0

        reader = ZoteroReader(db)
        try:
            conn = reader._connect()
            keys = {row["key"] for row in conn.execute("SELECT key FROM items")}
        finally:
            reader.close()
            writer.close()

        assert keys == {"OLD", "NEW"}

    def test_falls_back_to_immutable_without_a_pending_wal(self, tmp_path):
        db = tmp_path / "zotero.sqlite"
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
        writer.execute("INSERT INTO items VALUES (1, 'ONLY')")
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.close()
        wal = tmp_path / "zotero.sqlite-wal"
        assert not wal.exists() or wal.stat().st_size == 0

        reader = ZoteroReader(db)
        try:
            conn = reader._connect()
            assert conn.execute("SELECT key FROM items").fetchone()["key"] == "ONLY"
            assert reader._tmp_dir_obj is None  # fast path, no temp copy
        finally:
            reader.close()
