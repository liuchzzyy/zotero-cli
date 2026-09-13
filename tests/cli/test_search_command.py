"""Tests for Tier 1 search enhancements: type filter."""

from __future__ import annotations

import json

from tests.support import FIXTURES_DIR
from tests.support import invoke_cli as _invoke

from zotero_cli.core.reader import ZoteroReader


class TestItemTypeFilter:
    def test_search_filter_by_type_journal(self):
        result = _invoke(["search", "attention", "--type", "journalArticle"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert all(i["item_type"] == "journalArticle" for i in data)

    def test_search_filter_by_type_book(self):
        result = _invoke(["search", "", "--type", "book"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert len(data) >= 1
        assert all(i["item_type"] == "book" for i in data)

    def test_search_filter_by_type_no_match(self):
        result = _invoke(["search", "attention", "--type", "thesis"], json_output=True)
        assert result.exit_code == 0
        assert json.loads(result.output)["data"] == []

    def test_list_filter_by_type(self):
        result = _invoke(["list", "--type", "book"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert all(i["item_type"] == "book" for i in data)

    def test_reader_search_item_type(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            result = reader.search("", item_type="book")
            assert all(i.item_type == "book" for i in result.items)
        finally:
            reader.close()

    def test_type_filter_with_collection(self):
        result = _invoke(
            ["search", "", "--type", "journalArticle", "--collection", "Machine Learning"], json_output=True
        )
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert all(i["item_type"] == "journalArticle" for i in data)


class TestSortOrder:
    def test_search_sort_by_date_added_desc(self):
        result = _invoke(["search", "", "--sort", "dateAdded", "--direction", "desc"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_added"] for i in data]
        assert dates == sorted(dates, reverse=True)

    def test_search_sort_by_date_added_asc(self):
        result = _invoke(["search", "", "--sort", "dateAdded", "--direction", "asc"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_added"] for i in data]
        assert dates == sorted(dates)

    def test_search_sort_by_title(self):
        result = _invoke(["search", "", "--sort", "title", "--direction", "asc"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        titles = [i["title"] for i in data]
        assert titles == sorted(titles, key=str.lower)

    def test_search_sort_by_date_modified(self):
        result = _invoke(["search", "", "--sort", "dateModified", "--direction", "desc"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_modified"] for i in data]
        assert dates == sorted(dates, reverse=True)

    def test_list_sort_by_date_added(self):
        result = _invoke(["list", "--sort", "dateAdded", "--direction", "desc"], json_output=True)
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        dates = [i["date_added"] for i in data]
        assert dates == sorted(dates, reverse=True)

    def test_reader_search_sort(self):
        reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
        try:
            result = reader.search("", sort="dateAdded", direction="desc")
            dates = [i.date_added for i in result.items]
            assert dates == sorted(dates, reverse=True)
        finally:
            reader.close()
