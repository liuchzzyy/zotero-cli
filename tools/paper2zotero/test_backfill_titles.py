"""Offline tests for full-library Zotero translated-title backfill."""

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import backfill_titles as backfill
import zh_text


class BackfillTests(unittest.TestCase):
    def test_selects_only_titled_items_without_translation(self):
        items = [
            {"key": "A", "data": {"title": "Cathode", "extra": "funder: ABC", "itemType": "journalArticle"}},
            {"key": "B", "data": {"title": "Already", "extra": "translated-title: 已有翻译"}},
            {"key": "C", "data": {"title": "", "extra": "", "itemType": "note"}},
            {"key": "D", "data": {"title": "Blog", "extra": "Translated-Title: 译文", "itemType": "blogPost"}},
            {"key": "E", "data": {"title": "Other", "extra": "unrelated: 中文"}},
            {"key": "F", "data": {"title": "Paper.pdf", "extra": "", "itemType": "attachment"}},
        ]
        self.assertEqual([x["key"] for x in backfill.select_missing(items)], ["A", "E"])

    def test_model_returns_keyed_zh_translations_and_rejects_mismatch(self):
        rows = [{"key": "A1234567", "title": "Operando XAS of MnO2 cathodes"}]
        raw = '[{"key":"A1234567","title":"Operando XAS of MnO2 cathodes","title_zh":"MnO2 正极的原位 XAS 研究"}]\n\nsession_id: sample'
        self.assertEqual(backfill.parse_translations(raw, rows), {"A1234567": "MnO2 正极的原位 XAS 研究"})
        with self.assertRaises(backfill.BackfillError):
            backfill.parse_translations(raw.replace("Operando XAS of MnO2 cathodes", "Different title"), rows)

    def test_model_uses_query_file_and_retries_invalid_answer(self):
        rows = [{"key": "A1234567", "title": "Operando XAS of MnO2 cathodes"}]
        bad = CompletedProcess([], 0, stdout="[]", stderr="")
        good = CompletedProcess(
            [],
            0,
            stdout='[{"key":"A1234567","title":"Operando XAS of MnO2 cathodes","title_zh":"MnO2 正极的原位 XAS 研究"}]',
            stderr="",
        )

        with patch.object(backfill.subprocess, "run", side_effect=[bad, good]) as model:
            self.assertEqual(backfill.translate_batch(rows), {"A1234567": "MnO2 正极的原位 XAS 研究"})
            self.assertEqual(model.call_count, 2)
            args = model.call_args.args[0]
            self.assertIn("--ignore-rules", args)
            self.assertEqual(args[args.index("-t") + 1], "browser")
            self.assertIn("--query-file", args)

    def test_normalise_multiline_jats_title_without_changing_source(self):
        original = "Interlayer‐Engineered 1T‐MoSe\n   <sub>2</sub> Enabling Reversible Mn"
        self.assertEqual(
            backfill._normalise_input_title(original), "Interlayer‐Engineered 1T‐MoSe 2 Enabling Reversible Mn"
        )
        self.assertIn("<sub>2</sub>", original)

    def test_append_preserves_existing_extra_lines(self):
        self.assertEqual(
            backfill.merge_extra("funder: ABC\nshort-note: keep", "水系 Zn–MnO₂ 正极"),
            "funder: ABC\nshort-note: keep\ntranslated-title: 水系 Zn–MnO2 正极",
        )

    def test_backfilled_title_is_flattened_and_spaced_like_imported_titles(self):
        merged = backfill.merge_extra("", "层状δ-MnO₂正极在5 A g⁻¹下提供300 mAh g⁻¹")
        self.assertEqual(merged, "translated-title: 层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1")
        self.assertFalse(zh_text.has_script(merged))

    def test_write_only_target_item_and_verify_remote_extra(self):
        api = Mock()
        api.item.side_effect = [
            {"key": "A1234567", "data": {"title": "Cathode", "DOI": "10.1000/a", "extra": "funder: ABC"}},
            {
                "key": "A1234567",
                "data": {"title": "Cathode", "DOI": "10.1000/a", "extra": "funder: ABC\ntranslated-title: 正极"},
            },
        ]
        writer = Mock()
        backfill.update_one(api, writer, {"key": "A1234567", "title": "Cathode", "doi": "10.1000/a"}, "正极")
        writer.update_item.assert_called_once_with("A1234567", {"extra": "funder: ABC\ntranslated-title: 正极"})
        self.assertEqual(api.item.call_count, 2)

    def test_failed_readback_does_not_report_success(self):
        api = Mock()
        api.item.side_effect = [
            {"key": "A1234567", "data": {"title": "Cathode", "extra": ""}},
            {"key": "A1234567", "data": {"title": "Cathode", "extra": ""}},
        ]
        with self.assertRaises(backfill.BackfillError):
            backfill.update_one(api, Mock(), {"key": "A1234567", "title": "Cathode"}, "正极")

    def test_resume_skips_existing_translations_without_overwriting(self):
        api = Mock()
        api.item.return_value = {"key": "A1234567", "data": {"title": "Cathode", "extra": "translated-title: 已有翻译"}}
        writer = Mock()
        self.assertEqual(
            backfill.update_one(api, writer, {"key": "A1234567", "title": "Cathode"}, "新译文"), "already_translated"
        )
        writer.update_item.assert_not_called()

    def test_stage_must_complete_before_any_remote_write(self):
        items = [
            {"key": "A1234567", "data": {"title": "A", "extra": ""}},
            {"key": "B1234567", "data": {"title": "B", "extra": ""}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with patch.object(
                backfill, "translate_batch", side_effect=[{"A1234567": "甲"}, backfill.BackfillError("model failed")]
            ):
                with self.assertRaises(backfill.BackfillError):
                    backfill.stage_translations(items, path, batch_size=1)
            saved = backfill.json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["title_zh"], "甲")
            self.assertFalse(saved[1].get("title_zh"))

    def test_default_state_file_lives_under_gitignored_state_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(backfill, "state_dir", return_value=Path(directory)):
                path = backfill.default_state_file()
        self.assertEqual(path, Path(directory) / "paper2zotero" / "backfill_titles_state.json")

    def test_stage_fails_closed_on_unreadable_checkpoint(self):
        items = [{"key": "A1234567", "title": "Paper A", "doi": ""}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{unfinished", encoding="utf-8")
            with self.assertRaisesRegex(backfill.BackfillError, "拒绝覆盖"):
                backfill.stage_translations(items, path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{unfinished")

    def test_resolve_collection_by_key_or_name(self):
        api = Mock()
        api.collection.return_value = {"key": "COL1", "data": {"name": "Inbox"}}
        api.collection_items.return_value = [{"key": "A"}]
        api.everything.side_effect = lambda x: x
        res = backfill.resolve_collection_items(api, "COL1")
        self.assertEqual(res, [{"key": "A"}])

        api.collection.side_effect = Exception("not a key")
        api.collections.return_value = [{"key": "COL2", "data": {"name": "00 | 收件箱"}}]
        api.collection_items.return_value = [{"key": "B"}]
        res2 = backfill.resolve_collection_items(api, "00 | 收件箱")
        self.assertEqual(res2, [{"key": "B"}])


if __name__ == "__main__":
    unittest.main()
