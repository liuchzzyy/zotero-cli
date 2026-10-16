"""Offline checks for the independent 05:00 DOI-to-Zotero task."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import zotero_inbox as inbox


def api_for_new_item(doi="10.1000/a", key="NEWITEM1"):
    api = Mock()
    api.collection.return_value = {"key": inbox.INBOX_KEY, "data": {"name": inbox.INBOX_NAME}}
    api.item.return_value = {"key": key, "data": {"DOI": doi, "collections": [inbox.INBOX_KEY]}}
    return api


class ZoteroInboxTests(unittest.TestCase):
    def test_either_flag_1_or_uncertain_selects_independent_of_priority(self):
        rows = [
            {"doi": "10.1000/a", "is_priority": "0", "is_cathode_material": "1", "is_characterization_technique": "0"},
            {"doi": "10.1000/b", "is_priority": "0", "is_cathode_material": "0", "is_characterization_technique": "uncertain"},
            {"doi": "10.1000/c", "is_priority": "1", "is_cathode_material": "0", "is_characterization_technique": "0"},
            {"doi": "10.1000/d", "is_priority": "0", "is_cathode_material": "uncertain", "is_characterization_technique": "1"},
        ]
        self.assertEqual([row["doi"] for row in inbox.select_rows(rows)],
                         ["10.1000/a", "10.1000/b", "10.1000/d"])

    def test_repeated_doi_in_csv_is_not_deduplicated(self):
        row = {"doi": "10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0"}
        self.assertEqual(len(inbox.select_rows([row.copy(), row.copy()])), 2)

    def test_doi_import_creates_full_metadata_in_inbox_without_library_search(self):
        api = api_for_new_item()
        api.items.side_effect = AssertionError("do not search existing items")
        api.top.side_effect = AssertionError("do not list existing items")
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode", "DOI": "10.1000/a"}):
            key = inbox.import_doi("10.1000/a", api, writer)
        writer.add_item.assert_called_once_with(
            doi="10.1000/a", extra_fields={"title": "Cathode", "DOI": "10.1000/a", "collections": [inbox.INBOX_KEY]})
        api.item.assert_called_once_with("NEWITEM1")
        self.assertEqual(key, "NEWITEM1")

    def test_unverified_write_fails_without_falsely_marking_success(self):
        api = api_for_new_item()
        api.item.return_value = {"key": "NEWITEM1", "data": {"DOI": "10.1000/b", "collections": [inbox.INBOX_KEY]}}
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode"}):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", api, writer)

    def test_missing_crossref_metadata_does_not_create_bare_item(self):
        api = api_for_new_item()
        writer = Mock()
        with patch.object(inbox, "resolve_metadata", return_value=None):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", api, writer)
        writer.add_item.assert_not_called()

    def test_invalid_flags_or_doi_fail_before_any_write(self):
        for row in (
            {"doi": "10.1000/a", "is_cathode_material": "yes", "is_characterization_technique": "0"},
            {"doi": "https://doi.org/10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0"},
        ):
            with self.subTest(row=row), self.assertRaises(inbox.ZoteroInboxError):
                inbox.select_rows([row])

    def test_rejects_wrong_inbox_before_creating_item(self):
        api = api_for_new_item()
        api.collection.return_value = {"key": inbox.INBOX_KEY, "data": {"name": "wrong"}}
        writer = Mock()
        with self.assertRaises(inbox.ZoteroInboxError):
            inbox.verify_inbox(api)
        writer.add_item.assert_not_called()

    def test_csv_imports_each_selected_row_and_preserves_original_columns(self):
        rows = [
            {"doi": "10.1000/a", "title": "A", "title_zh": "正极", "is_priority": "0", "is_cathode_material": "1", "is_characterization_technique": "0"},
            {"doi": "10.1000/b", "title": "B", "title_zh": "负极", "is_priority": "1", "is_cathode_material": "0", "is_characterization_technique": "0"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            api = api_for_new_item()
            writer = Mock()
            with patch.object(inbox, "import_doi", return_value="NEWITEM1") as add:
                count = inbox.sync_csv(path, api, writer)
            add.assert_called_once_with("10.1000/a", api, writer)
            self.assertEqual(count, 1)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual = list(reader)
                self.assertEqual(reader.fieldnames, list(rows[0]) + ["zotero_status", "zotero_key"])
            self.assertEqual([(r["doi"], r["title"]) for r in actual], [(r["doi"], r["title"]) for r in rows])
            self.assertEqual(actual[0]["zotero_status"], "added")
            self.assertEqual(actual[0]["zotero_key"], "NEWITEM1")
            self.assertEqual(actual[1]["zotero_status"], "skipped")

    def test_resume_skips_successful_csv_rows_without_library_deduplication(self):
        row = {"doi": "10.1000/a", "title": "A", "title_zh": "正极", "is_cathode_material": "1",
               "is_characterization_technique": "0", "zotero_status": "added", "zotero_key": "NEWITEM1"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            before = path.read_bytes()
            api = api_for_new_item()
            writer = Mock()
            with patch.object(inbox, "import_doi") as add:
                count = inbox.sync_csv(path, api, writer)
            self.assertEqual(count, 0)
            add.assert_not_called()
            writer.add_item.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_per_item_checkpoint_survives_later_failure(self):
        rows = [
            {"doi": "10.1000/a", "title": "A", "title_zh": "正极", "is_cathode_material": "1", "is_characterization_technique": "0"},
            {"doi": "10.1000/b", "title": "B", "title_zh": "表征", "is_cathode_material": "0", "is_characterization_technique": "uncertain"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch.object(inbox, "import_doi", side_effect=["NEWITEM1", inbox.ZoteroInboxError("network")]) as add:
                with self.assertRaises(inbox.ZoteroInboxError):
                    inbox.sync_csv(path, api_for_new_item(), Mock())
            self.assertEqual(add.call_count, 2)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                result = list(csv.DictReader(handle))
            self.assertEqual(result[0]["zotero_status"], "added")
            self.assertEqual(result[0]["zotero_key"], "NEWITEM1")
            self.assertEqual(result[1]["zotero_key"], "")

    def test_invalid_or_incomplete_csv_is_not_imported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            path.write_text("doi,title,is_cathode_material,is_characterization_technique\n"
                            "10.1000/a,A,1,0\n", encoding="utf-8-sig")
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.sync_csv(path, Mock(), Mock())

    def test_no_selected_rows_is_noop_without_zotero_api_calls(self):
        row = {"doi": "10.1000/a", "title": "Anode", "title_zh": "负极", "is_cathode_material": "0",
               "is_characterization_technique": "0"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            before = path.read_bytes()
            api = Mock()
            writer = Mock()
            self.assertEqual(inbox.sync_csv(path, api, writer), 0)
            api.assert_not_called()
            writer.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_default_date_uses_yesterday_csv_and_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(inbox, "target_date", return_value="2026-10-15"):
                with self.assertRaises(inbox.ZoteroInboxError):
                    inbox.main([])

    def test_dry_run_lists_candidates_without_web_api_writes(self):
        row = {"doi": "10.1000/a", "title": "A", "title_zh": "正极", "is_cathode_material": "1",
               "is_characterization_technique": "0"}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(inbox, "open_zotero") as connect, patch("builtins.print") as report:
                inbox.main(["--date", "2026-10-15", "--dry-run"])
            connect.assert_not_called()
            report.assert_called_once()
            self.assertIn("10.1000/a", report.call_args.args[0])
            self.assertEqual(path.read_text(encoding="utf-8-sig").count("10.1000/a"), 1)


if __name__ == "__main__":
    unittest.main()
