"""Offline checks for the independent 05:00 DOI-to-Zotero task."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

import zh_text
import zotero_inbox as inbox

ABSTRACT = "MnO2 cathodes were probed by operando XAS at 0.5 A g-1."
ABSTRACT_ZH = "在 0.5 A g^-1 下用原位 XAS 研究了 MnO2 正极。"


def api_for_new_item(doi="10.1000/a", key="NEWITEM1", *, abstract_note="",
                     title_line="translated-title: 正极"):
    api = Mock()
    api.collection.return_value = {"key": inbox.INBOX_KEY, "data": {"name": inbox.INBOX_NAME}}
    api.item.return_value = {"key": key, "data": {"DOI": doi, "collections": [inbox.INBOX_KEY],
                                                "extra": title_line, "abstractNote": abstract_note}}
    return api


def csv_row(**overrides):
    base = {"doi": "10.1000/a", "title": "Cathode", "abstract": "", "title_zh": "正极",
            "abstract_zh": "", "is_cathode_material": "1", "is_characterization_technique": "0"}
    base.update(overrides)
    return base


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
        item = {"doi": "10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0"}
        self.assertEqual(len(inbox.select_rows([item.copy(), item.copy()])), 2)

    def test_the_selected_column_written_by_screening_is_honoured(self):
        """The producer owns the policy; the importer must not re-derive it."""
        rows = [
            {"doi": "10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0",
             "selected": "1"},
            {"doi": "10.1000/b", "is_cathode_material": "1", "is_characterization_technique": "1",
             "selected": "0"},  # flags say yes, screening said no
        ]
        self.assertEqual([row["doi"] for row in inbox.select_rows(rows)], ["10.1000/a"])

    def test_a_csv_without_the_selected_column_falls_back_to_the_flags(self):
        """An older annotated CSV predates the three-stage split and has no column."""
        rows = [
            {"doi": "10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0"},
            {"doi": "10.1000/b", "is_cathode_material": "0", "is_characterization_technique": "0"},
            {"doi": "10.1000/c", "is_cathode_material": "0", "is_characterization_technique": "uncertain"},
        ]
        self.assertEqual([row["doi"] for row in inbox.select_rows(rows)], ["10.1000/a", "10.1000/c"])

    def test_a_blank_selected_cell_falls_back_to_the_flags_for_that_row(self):
        rows = [{"doi": "10.1000/a", "is_cathode_material": "1", "is_characterization_technique": "0",
                 "selected": ""}]
        self.assertEqual([row["doi"] for row in inbox.select_rows(rows)], ["10.1000/a"])

    def test_doi_import_creates_full_metadata_in_inbox_without_library_search(self):
        api = api_for_new_item()
        api.items.side_effect = AssertionError("do not search existing items")
        api.top.side_effect = AssertionError("do not list existing items")
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode", "DOI": "10.1000/a"}):
            key = inbox.import_doi("10.1000/a", "正极", "", api, writer)
        writer.add_item.assert_called_once_with(
            doi="10.1000/a", extra_fields={"title": "Cathode", "DOI": "10.1000/a",
                                             "extra": "translated-title: 正极", "collections": [inbox.INBOX_KEY]})
        api.item.assert_called_once_with("NEWITEM1")
        self.assertEqual(key, "NEWITEM1")

    def test_doi_import_stores_translated_title_and_preserves_other_extra(self):
        title_zh = "水系 Zn-MnO2 正极的 operando XAS 研究"
        api = api_for_new_item(title_line=f"translated-title: {title_zh}")
        api.item.return_value["data"]["extra"] = f"funder: ABC\ntranslated-title: {title_zh}"
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Operando XAS of MnO2 cathodes",
                                                                 "DOI": "10.1000/a", "extra": "funder: ABC"}):
            key = inbox.import_doi("10.1000/a", title_zh, "", api, writer)
        self.assertEqual(key, "NEWITEM1")
        writer.add_item.assert_called_once_with(doi="10.1000/a", extra_fields={
            "title": "Operando XAS of MnO2 cathodes", "DOI": "10.1000/a",
            "extra": f"funder: ABC\ntranslated-title: {title_zh}",
            "collections": [inbox.INBOX_KEY],
        })
        writer.update_item.assert_not_called()

    def test_chinese_abstract_replaces_the_crossref_abstract(self):
        api = api_for_new_item(title_line="translated-title: MnO2 正极的原位 XAS 研究",
                              abstract_note=ABSTRACT_ZH)
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={
            "title": "Operando XAS of MnO2 cathodes", "DOI": "10.1000/a", "abstractNote": ABSTRACT,
        }):
            key = inbox.import_doi("10.1000/a", "MnO2 正极的原位 XAS 研究", ABSTRACT_ZH, api, writer,
                                   source_abstract=ABSTRACT)
        self.assertEqual(key, "NEWITEM1")
        fields = writer.add_item.call_args.kwargs["extra_fields"]
        self.assertEqual(fields["abstractNote"], ABSTRACT_ZH)
        self.assertNotIn(ABSTRACT, fields["abstractNote"])

    def test_a_paper_without_a_crossref_abstract_keeps_the_field_empty(self):
        api = api_for_new_item(abstract_note="")
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={
            "title": "Cathode", "DOI": "10.1000/a", "abstractNote": "",
        }):
            inbox.import_doi("10.1000/a", "正极", "", api, writer, source_abstract="")
        fields = writer.add_item.call_args.kwargs["extra_fields"]
        self.assertNotIn("abstractNote", fields)

    def test_subscripts_flattened_and_cjk_latin_spaced_before_the_write(self):
        api = api_for_new_item(
            title_line="translated-title: 层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1",
            abstract_note="层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1。",
        )
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Layered MnO2", "DOI": "10.1000/a"}):
            inbox.import_doi("10.1000/a", "层状δ-MnO₂正极在5 A g⁻¹下提供300 mAh g⁻¹",
                             "层状δ-MnO₂正极在5 A g⁻¹下提供300 mAh g⁻¹。",
                             api, writer, source_abstract=ABSTRACT)
        fields = writer.add_item.call_args.kwargs["extra_fields"]
        self.assertEqual(fields["extra"],
                         "translated-title: 层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1")
        self.assertEqual(fields["abstractNote"], "层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1。")
        self.assertFalse(zh_text.has_script(fields["extra"]))
        self.assertFalse(zh_text.has_script(fields["abstractNote"]))

    def test_missing_chinese_abstract_is_rejected_before_any_write(self):
        api = api_for_new_item()
        writer = Mock()
        with patch.object(inbox, "resolve_metadata") as resolve:
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "正极", "", api, writer, source_abstract=ABSTRACT)
        writer.add_item.assert_not_called()
        resolve.assert_not_called()

    def test_invented_abstract_for_an_abstractless_paper_is_rejected(self):
        api = api_for_new_item()
        writer = Mock()
        with self.assertRaises(inbox.ZoteroInboxError):
            inbox.import_doi("10.1000/a", "正极", ABSTRACT_ZH, api, writer, source_abstract="")
        writer.add_item.assert_not_called()

    def test_abstract_note_mismatch_is_not_marked_success(self):
        """A dropped abstractNote field must fail the item instead of passing silently."""
        api = api_for_new_item(abstract_note="")  # the write did not store the abstract
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode", "DOI": "10.1000/a"}):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "正极", ABSTRACT_ZH, api, writer, source_abstract=ABSTRACT)

    def test_missing_abstract_note_field_counts_as_empty(self):
        """An item type without the field must not fail when nothing was translated."""
        api = api_for_new_item()
        del api.item.return_value["data"]["abstractNote"]
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode", "DOI": "10.1000/a"}):
            self.assertEqual(inbox.import_doi("10.1000/a", "正极", "", api, writer), "NEWITEM1")

    def test_verified_extra_mismatch_is_not_marked_success(self):
        api = api_for_new_item()
        api.item.return_value["data"]["extra"] = "funder: ABC"
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode", "DOI": "10.1000/a"}):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "正极", "", api, writer)

    def test_missing_chinese_title_rejected_before_zotero_write(self):
        api = api_for_new_item()
        writer = Mock()
        with patch.object(inbox, "resolve_metadata") as resolve:
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "", "", api, writer)
        writer.add_item.assert_not_called()
        resolve.assert_not_called()

    def test_csv_requires_translation_for_selected_rows(self):
        bad = csv_row(title_zh="")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(bad))
                writer.writeheader()
                writer.writerow(bad)
            before = path.read_bytes()
            api = Mock()
            writer = Mock()
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.sync_csv(path, api, writer)
            self.assertEqual(path.read_bytes(), before)
            writer.add_item.assert_not_called()

    def test_csv_without_abstract_column_is_rejected_before_any_write(self):
        legacy = csv_row()
        del legacy["abstract_zh"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(legacy))
                writer.writeheader()
                writer.writerow(legacy)
            writer_mock = Mock()
            with self.assertRaises(inbox.ZoteroInboxError) as caught:
                inbox.sync_csv(path, Mock(), writer_mock)
            self.assertIn("abstract_zh", str(caught.exception))
            writer_mock.add_item.assert_not_called()

    def test_unverified_write_fails_without_falsely_marking_success(self):
        api = api_for_new_item()
        api.item.return_value = {"key": "NEWITEM1", "data": {"DOI": "10.1000/b", "collections": [inbox.INBOX_KEY]}}
        writer = Mock()
        writer.add_item.return_value = "NEWITEM1"
        with patch.object(inbox, "resolve_metadata", return_value={"title": "Cathode"}):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "正极", "", api, writer)

    def test_missing_crossref_metadata_does_not_create_bare_item(self):
        api = api_for_new_item()
        writer = Mock()
        with patch.object(inbox, "resolve_metadata", return_value=None):
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.import_doi("10.1000/a", "正极", "", api, writer)
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
            csv_row(doi="10.1000/a", title="A", title_zh="正极", is_priority="0"),
            csv_row(doi="10.1000/b", title="B", title_zh="负极", is_priority="1",
                    is_cathode_material="0"),
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
            add.assert_called_once_with("10.1000/a", "正极", "", api, writer, source_abstract="")
            self.assertEqual(count, 1)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual = list(reader)
                self.assertEqual(reader.fieldnames, list(rows[0]) + ["zotero_status", "zotero_key"])
            self.assertEqual([(r["doi"], r["title"]) for r in actual], [(r["doi"], r["title"]) for r in rows])
            self.assertEqual(actual[0]["zotero_status"], "added")
            self.assertEqual(actual[0]["zotero_key"], "NEWITEM1")
            self.assertEqual(actual[1]["zotero_status"], "skipped")

    def test_csv_row_with_abstract_passes_it_to_the_import(self):
        rows = [csv_row(abstract=ABSTRACT, abstract_zh=ABSTRACT_ZH)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch.object(inbox, "import_doi", return_value="NEWITEM1") as add:
                inbox.sync_csv(path, api_for_new_item(), Mock())
        add.assert_called_once_with("10.1000/a", "正极", ABSTRACT_ZH, ANY, ANY, source_abstract=ABSTRACT)

    def test_row_with_source_abstract_but_no_translation_blocks_the_whole_batch(self):
        rows = [csv_row(), csv_row(doi="10.1000/b", abstract=ABSTRACT, abstract_zh="")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            before = path.read_bytes()
            writer = Mock()
            with self.assertRaises(inbox.ZoteroInboxError):
                inbox.sync_csv(path, api_for_new_item(), writer)
            writer.add_item.assert_not_called()
            self.assertEqual(path.read_bytes(), before)

    def test_resume_skips_successful_csv_rows_without_library_deduplication(self):
        rows = [csv_row(zotero_status="added", zotero_key="NEWITEM1")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
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
            csv_row(doi="10.1000/a", title="A", title_zh="正极"),
            csv_row(doi="10.1000/b", title="B", title_zh="表征",
                    is_cathode_material="0", is_characterization_technique="uncertain"),
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
        rows = [csv_row(title="Anode", title_zh="负极", is_cathode_material="0")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
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

    def test_successful_run_deletes_only_the_processed_csv(self):
        rows = [csv_row()]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            other = Path(folder) / "updates_2026-10-14.csv"
            other.write_text("unrelated", encoding="utf-8")
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(
                inbox, "open_zotero", return_value=(Mock(), Mock())
            ), patch.object(inbox, "sync_csv", return_value=1) as sync:
                inbox.main(["--date", "2026-10-15"])
            sync.assert_called_once()
            self.assertFalse(path.exists())
            self.assertTrue(other.exists())

    def test_failed_run_keeps_csv_for_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            path.write_text("original contents", encoding="utf-8")
            before = path.read_bytes()
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(
                inbox, "open_zotero", return_value=(Mock(), Mock())
            ), patch.object(inbox, "sync_csv", side_effect=inbox.ZoteroInboxError("write failed")):
                with self.assertRaises(inbox.ZoteroInboxError):
                    inbox.main(["--date", "2026-10-15"])
            self.assertEqual(path.read_bytes(), before)

    def test_realistic_mid_batch_failure_retains_checkpoint_csv(self):
        rows = [csv_row(doi="10.1000/a", title="A", title_zh="正极"),
                csv_row(doi="10.1000/b", title="B", title_zh="正极")]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(
                inbox, "open_zotero", return_value=(api_for_new_item(), Mock())
            ), patch.object(inbox, "import_doi", side_effect=["NEWITEM1", inbox.ZoteroInboxError("second failed")]):
                with self.assertRaises(inbox.ZoteroInboxError):
                    inbox.main(["--date", "2026-10-15"])
            self.assertTrue(path.exists())
            with path.open(encoding="utf-8-sig", newline="") as handle:
                actual = list(csv.DictReader(handle))
            self.assertEqual(actual[0]["zotero_key"], "NEWITEM1")
            self.assertEqual(actual[1]["zotero_key"], "")

    def test_dry_run_lists_candidates_without_web_api_writes(self):
        rows = [csv_row()]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "updates_2026-10-15.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with patch.object(inbox, "SCRIPT_DIR", Path(folder)), patch.object(inbox, "open_zotero") as connect, patch("builtins.print") as report:
                inbox.main(["--date", "2026-10-15", "--dry-run"])
            connect.assert_not_called()
            report.assert_called_once()
            self.assertIn("10.1000/a", report.call_args.args[0])
            self.assertEqual(path.read_text(encoding="utf-8-sig").count("10.1000/a"), 1)


if __name__ == "__main__":
    unittest.main()
