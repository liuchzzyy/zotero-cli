"""Offline unit tests for Zotero abstract translation backfill."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

import translate_abstracts as trans
import zh_text


class TranslateAbstractsTests(unittest.TestCase):
    def test_is_predominantly_chinese(self) -> None:
        self.assertTrue(trans.is_predominantly_chinese("这是水系锌离子电池的正极材料研究"))
        self.assertTrue(trans.is_predominantly_chinese("层状 δ-MnO2 正极在 5 A g^-1 下表现出优异容量"))
        self.assertFalse(trans.is_predominantly_chinese("Aqueous zinc-ion batteries (AZIBs) are promising."))
        self.assertFalse(trans.is_predominantly_chinese(""))

    def test_clean_source_abstract(self) -> None:
        raw_prefix = "Abstract MnO2 is commonly used as the cathode material..."
        self.assertEqual(trans.clean_source_abstract(raw_prefix), "MnO2 is commonly used as the cathode material...")

        # Crossref repetition stutter
        stutter = (
            "The high-voltage Mn2+ reaction offers 600 mAh g^-1. , "
            "The high-voltage Mn2+ reaction offers 600 mAh g^-1. This chemistry has attracted attention."
        )
        cleaned = trans.clean_source_abstract(stutter)
        self.assertFalse(cleaned.startswith("The high-voltage Mn2+ reaction offers 600 mAh g^-1. ,"))
        self.assertIn("This chemistry has attracted attention.", cleaned)

    def test_select_items_skips_empty_and_existing_chinese(self) -> None:
        items = [
            {
                "key": "A",
                "data": {"title": "Paper A", "abstractNote": "Aqueous battery with MnO2", "itemType": "journalArticle"},
            },
            {
                "key": "B",
                "data": {
                    "title": "Paper B",
                    "abstractNote": "水系锌离子电池电化学性能研究",
                    "itemType": "journalArticle",
                },
            },
            {"key": "C", "data": {"title": "Paper C", "abstractNote": "", "itemType": "journalArticle"}},
            {"key": "D", "data": {"title": "Paper D", "abstractNote": None, "itemType": "journalArticle"}},
            {"key": "E", "data": {"title": "Note E", "abstractNote": "Note text", "itemType": "note"}},
        ]
        selected, stats = trans.select_items_to_translate(items, force=False)
        self.assertEqual([x["key"] for x in selected], ["A"])
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["no_abstract"], 2)
        self.assertEqual(stats["already_chinese"], 1)
        self.assertEqual(stats["needs_translation"], 1)

        # Force mode re-selects already Chinese items
        selected_force, _ = trans.select_items_to_translate(items, force=True)
        self.assertEqual([x["key"] for x in selected_force], ["A", "B"])

    def test_parse_model_response_validates_json_and_keys(self) -> None:
        batch = [{"key": "KEY1", "title": "Title 1", "abstract": "Abstract 1"}]
        raw = '[{"key": "KEY1", "title": "Title 1", "abstract_zh": "这是翻译后的中文摘要。"}]\n\nsession_id: 123'
        parsed = trans.parse_model_response(raw, batch)
        self.assertEqual(parsed, {"KEY1": "这是翻译后的中文摘要。"})

        # Markdown json fence tolerated
        fenced = '```json\n[{"key": "KEY1", "title": "Title 1", "abstract_zh": "这是翻译后的中文摘要。"}]\n```'
        self.assertEqual(trans.parse_model_response(fenced, batch), {"KEY1": "这是翻译后的中文摘要。"})

        # Non-matching key raises
        mismatched = '[{"key": "OTHER", "title": "Title 1", "abstract_zh": "中文摘要"}]'
        with self.assertRaises(trans.AbstractTranslationError):
            trans.parse_model_response(mismatched, batch)

        # Missing Chinese text raises
        no_cjk = '[{"key": "KEY1", "title": "Title 1", "abstract_zh": "Pure English response without CJK"}]'
        with self.assertRaises(trans.AbstractTranslationError):
            trans.parse_model_response(no_cjk, batch)

    def test_stage_translations_normalises_abstract_formatting(self) -> None:
        candidates = [{"key": "KEY1", "title": "T1", "abstract": "English text"}]
        mock_translator = Mock(return_value={"KEY1": "摘要：在5 A g⁻¹下测试δ-MnO₂正极，容量达300 mAh g⁻¹。"})
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            state = trans.stage_translations(candidates, state_file, batch_size=1, custom_translator=mock_translator)
            self.assertEqual(len(state), 1)
            norm = state[0]["abstract_zh"]
            # No super/subscript
            self.assertFalse(zh_text.has_script(norm))
            self.assertIn("MnO2", norm)
            self.assertIn("5 A g^-1", norm)
            self.assertIn("300 mAh g^-1", norm)
            # CJK/Latin spacing
            self.assertIn("在 5 A g^-1 下测试 δ-MnO2 正极", norm)
            # Leading "摘要：" removed
            self.assertFalse(norm.startswith("摘要"))

    def test_update_one_abstract_and_verify(self) -> None:
        api = Mock()
        api.item.side_effect = [
            {"key": "KEY1", "data": {"abstractNote": "Old English abstract"}},
            {"key": "KEY1", "data": {"abstractNote": "新中文摘要"}},
        ]
        writer = Mock()
        status = trans.update_one_abstract(
            api, writer, {"key": "KEY1", "abstract": "Old English abstract"}, "新中文摘要"
        )
        self.assertEqual(status, "updated")
        writer.update_item.assert_called_once_with("KEY1", {"abstractNote": "新中文摘要"})
        self.assertEqual(api.item.call_count, 2)

    def test_update_one_abstract_detects_already_up_to_date(self) -> None:
        api = Mock()
        api.item.return_value = {"key": "KEY1", "data": {"abstractNote": "已存在的中文摘要"}}
        writer = Mock()
        status = trans.update_one_abstract(api, writer, {"key": "KEY1"}, "已存在的中文摘要")
        self.assertEqual(status, "already_up_to_date")
        writer.update_item.assert_not_called()

    def test_failed_readback_raises_error(self) -> None:
        api = Mock()
        api.item.side_effect = [
            {"key": "KEY1", "data": {"abstractNote": "Old English"}},
            {"key": "KEY1", "data": {"abstractNote": "Different after write"}},
        ]
        writer = Mock()
        with self.assertRaises(trans.AbstractTranslationError):
            trans.update_one_abstract(api, writer, {"key": "KEY1", "abstract": "Old English"}, "期望写入的内容")

    def test_dry_run_does_not_mutate(self) -> None:
        candidates = [{"key": "KEY1", "title": "T1", "abstract": "Eng"}]
        api = Mock()
        writer = Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            res = trans.run_abstract_translation(api, writer, candidates, state_file, dry_run=True)
            self.assertEqual(res["to_translate"], 1)
            self.assertEqual(res["updated"], 0)
            writer.update_item.assert_not_called()
            self.assertFalse(state_file.exists())

    def test_default_state_file_lives_under_gitignored_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(trans, "state_dir", return_value=Path(tmpdir)):
                path = trans.default_state_file()
        self.assertEqual(path, Path(tmpdir) / "paper2zotero" / "translate_abstracts_state.json")

    def test_stage_translations_checkpoints_each_completed_batch_and_resumes(self) -> None:
        candidates = [
            {"key": "A", "title": "Title A", "abstract": "English A"},
            {"key": "B", "title": "Title B", "abstract": "English B"},
        ]
        calls: list[list[str]] = []

        def failing_translator(batch: list[dict[str, str]]) -> dict[str, str]:
            calls.append([row["key"] for row in batch])
            if len(calls) == 1:
                return {"A": "中文摘要甲"}
            raise trans.AbstractTranslationError("model unavailable")

        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            with self.assertRaisesRegex(trans.AbstractTranslationError, "model unavailable"):
                trans.stage_translations(candidates, state_file, batch_size=1, custom_translator=failing_translator)
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["abstract_zh"], "中文摘要甲")
            self.assertNotIn("abstract_zh", saved[1])

            resume_calls: list[list[str]] = []

            def resume_translator(batch: list[dict[str, str]]) -> dict[str, str]:
                resume_calls.append([row["key"] for row in batch])
                return {row["key"]: f"中文摘要{row['key']}" for row in batch}

            state = trans.stage_translations(candidates, state_file, batch_size=1, custom_translator=resume_translator)

        self.assertEqual(resume_calls, [["B"]])
        self.assertEqual({row["key"]: row["abstract_zh"] for row in state}, {"A": "中文摘要甲", "B": "中文摘要 B"})

    def test_stage_translations_fails_closed_on_unreadable_checkpoint(self) -> None:
        candidates = [{"key": "A", "title": "Title A", "abstract": "English A"}]
        for content in ("{unfinished", '{"not": "a list"}', '[{"title": "no key"}]'):
            with tempfile.TemporaryDirectory() as tmpdir:
                state_file = Path(tmpdir) / "state.json"
                state_file.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(trans.AbstractTranslationError, "拒绝覆盖"):
                    trans.stage_translations(candidates, state_file)
                self.assertEqual(state_file.read_text(encoding="utf-8"), content)

    def test_write_state_creates_parent_and_cleans_temp_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "nested" / "state.json"
            trans._write_state(state_file, [{"key": "A"}])
            self.assertTrue(state_file.is_file())

            def failing_replace(self: Path, target: Path) -> Path:
                raise OSError("disk full")

            with patch.object(Path, "replace", failing_replace):
                with self.assertRaises(OSError):
                    trans._write_state(state_file, [{"key": "B"}])
            self.assertEqual(list(state_file.parent.glob("*.tmp")), [])

    def test_model_invokes_hermes_cli_with_retries(self) -> None:
        batch = [{"key": "K1", "title": "T1", "abstract": "Abs"}]
        bad = CompletedProcess([], 0, stdout="[]", stderr="")
        good = CompletedProcess(
            [], 0, stdout='[{"key": "K1", "title": "T1", "abstract_zh": "有效中文摘要"}]', stderr=""
        )
        with tempfile.TemporaryDirectory() as scratch:
            with (
                patch.object(trans, "SCIENTIST_HOME", Path(scratch)),
                patch.object(trans.subprocess, "run", side_effect=[bad, good]) as mock_run,
            ):
                res = trans.translate_batch(batch)
                self.assertEqual(res, {"K1": "有效中文摘要"})
                self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
