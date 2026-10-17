"""WAL visibility and duplicate-PDF handling.

Both bugs surfaced on real items: while Zotero Desktop ran, `immutable=1` read a
snapshot that predated the sync (attachments looked missing), and two copies of
one paper were concatenated into a single prompt.
"""

from __future__ import annotations

import sqlite3

import pytest

from zotero_cli.core.note_analysis import _drop_duplicate_parts
from zotero_cli.core.reader import ZoteroReader

_LONG = "This is a sufficiently long line of body text that survives the length filter."


class TestWalVisibility:
    def test_reads_rows_that_live_only_in_the_wal(self, tmp_path):
        db = tmp_path / "zotero.sqlite"
        writer = sqlite3.connect(db)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT)")
        writer.execute("INSERT INTO items VALUES (1, 'OLD')")
        writer.commit()

        # Second row stays in the WAL until a checkpoint runs. immutable=1 would
        # miss it entirely, which is exactly the stale-snapshot bug.
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


class TestDropDuplicateParts:
    def test_shorter_copy_is_dropped_in_favour_of_the_fuller_one(self):
        full = "\n".join([_LONG, "Second paragraph of the complete article.", _LONG + " 3"])
        partial = "\n".join([_LONG, "Second paragraph of the complete article."])
        assert _drop_duplicate_parts([partial, full]) == [full]

    def test_distinct_documents_are_kept(self):
        a = _LONG
        b = "A completely different sentence that shares no long line with the other one."
        assert len(_drop_duplicate_parts([a, b])) == 2

    def test_paper_and_its_supplementary_are_both_kept(self):
        main = "\n".join([_LONG, "Main results discussion."])
        si = "\n".join(["Supplementary figure captions and raw curves.", _LONG + " SI"])
        assert len(_drop_duplicate_parts([main, si])) == 2

    @pytest.mark.parametrize("parts", [[], [_LONG]])
    def test_trivial_input_untouched(self, parts):
        assert _drop_duplicate_parts(parts) == parts

    def test_identical_copies_collapse_to_one(self):
        assert _drop_duplicate_parts([_LONG, _LONG]) == [_LONG]

    def test_preserves_original_order_of_distinct_parts(self):
        # Order carries meaning (main text, then appendices); dedupe must not
        # sort by length to make matching easier.
        short_doc = _LONG
        long_doc = "\n".join([_LONG + " alpha", "Another body line that is long enough to count here."])
        assert _drop_duplicate_parts([short_doc, long_doc]) == [short_doc, long_doc]

    def test_short_text_without_long_lines_is_not_destroyed(self):
        # Both parts lack 60-char lines: we cannot judge, so keep both rather
        # than silently deleting content.
        assert len(_drop_duplicate_parts(["短文本", "另一段短文本"])) == 2
