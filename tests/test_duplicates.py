"""Tests for duplicate detection."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli_agent.cli import main
from zotero_cli_agent.core.reader import ZoteroReader

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _invoke(args: list[str], json_output: bool = False):
    runner = CliRunner()
    base = ["--json"] if json_output else []
    env = {"ZOT_DATA_DIR": str(FIXTURES_DIR), "ZOT_FORMAT": "table"}
    return runner.invoke(main, base + args, env=env)


class TestDuplicateReader:
    def test_find_duplicates_doi(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
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
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="title", threshold=0.7)
            # ATTN001 and DUPE008 have very similar titles
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
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="both")
            assert len(groups) >= 1
        finally:
            reader.close()

    def test_find_duplicates_no_matches(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="doi", limit=0)
            assert len(groups) == 0
        finally:
            reader.close()

    def test_find_duplicates_respects_limit(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            groups = reader.find_duplicates(strategy="both", limit=1)
            assert len(groups) <= 1
        finally:
            reader.close()

    def test_title_matches_with_conflicting_dois_are_not_duplicates(self, tmp_path):
        """Different DOI records with similar titles must not be auto-cleaned."""
        db_path = tmp_path / "zotero.sqlite"
        shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
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
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            with patch("zotero_cli_agent.core.reader.SequenceMatcher") as matcher:
                groups = reader.find_duplicates(strategy="both", limit=1)
            assert len(groups) == 1
            assert groups[0].match_type == "doi"
            matcher.assert_not_called()
        finally:
            reader.close()


class TestDuplicatesCLI:
    def test_duplicates_json(self):
        result = _invoke(["duplicates", "--by", "doi"], json_output=True)
        assert result.exit_code != 0
        data = json.loads(result.output)["data"]
        assert len(data) >= 1
        assert data[0]["match_type"] == "doi"

    def test_duplicates_table(self):
        result = _invoke(["duplicates"])
        assert result.exit_code != 0
        assert "ATTN001" in result.output or "DUPE008" in result.output

    def test_duplicates_by_title(self):
        # Default 0.85 threshold may not match; lower it for this fixture.
        result = _invoke(["duplicates", "--by", "title", "--threshold", "0.7"], json_output=True)
        assert result.exit_code != 0
