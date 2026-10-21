"""Offline tests for per-collection Chinese abstract + title sync."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

import batch_translate_metadata as sync

EN = "Layer-structured metal oxides are promising cathode materials for potassium-ion batteries. " * 3
ZH = "层状金属氧化物因比容量高、成本低而成为钾离子电池的理想正极材料，但其 redox 行为尚不清楚。" * 3
EN_TITLE = "Operando XAS of MnO2 cathodes"


def _item(key, item_type="journalArticle", abstract="", doi="", title=None, extra=""):
    return {
        "key": key,
        "data": {
            "itemType": item_type,
            "title": f"T-{key}" if title is None else title,
            "DOI": doi,
            "abstractNote": abstract,
            "extra": extra,
        },
    }


def _api(collections, items):
    api = Mock()
    api.collections.return_value = collections
    api.everything.side_effect = lambda x: x
    api.collection_items.return_value = items
    return api


class ResolveCollectionTests(unittest.TestCase):
    COLLECTIONS = [
        {"key": "KEY00001", "data": {"key": "KEY00001", "name": "402 | MnO2_Zn"}},
        {"key": "KEY00002", "data": {"key": "KEY00002", "name": "301 | Solid electrolyte"}},
        {"key": "KEY00003", "data": {"key": "KEY00003", "name": "MnO2 - other"}},
    ]

    def test_accepts_key_exact_name_and_unique_substring(self):
        api = _api(self.COLLECTIONS, [])
        self.assertEqual(sync.resolve_collection(api, "KEY00002"), ("KEY00002", "301 | Solid electrolyte"))
        self.assertEqual(
            sync.resolve_collection(api, "301 | Solid electrolyte"), ("KEY00002", "301 | Solid electrolyte")
        )
        self.assertEqual(sync.resolve_collection(api, "solid electrolyte"), ("KEY00002", "301 | Solid electrolyte"))

    def test_ambiguous_substring_lists_collections_and_exits(self):
        api = _api(self.COLLECTIONS, [])
        with self.assertRaises(SystemExit) as ctx:
            sync.resolve_collection(api, "MnO2")
        self.assertIn("KEY00003", str(ctx.exception))


class InventoryTests(unittest.TestCase):
    cfg = Mock(data_dir=Path("/nonexistent"), crossref_mailto="")

    def _inventory(self, items, overrides=None):
        api = _api([], items)
        overrides = {"OVERRIDE": (EN, "publisher")} if overrides is None else overrides
        with patch.object(sync, "recover_missing_abstract", return_value=(None, "none")) as recover:
            candidates, stats = sync.inventory_collection(
                api, Mock(), self.cfg, collection_key="COLL0001", manual_overrides=overrides
            )
        api.collection_items.assert_called_once_with("COLL0001")
        return candidates, stats, recover

    def test_classifies_abstracts_and_titles(self):
        items = [
            _item("ATTACH01", "attachment"),
            _item("NOTE0001", "note"),
            _item("ENGLISH1", abstract=EN, doi="10.1/x"),
            _item("CHINESE1", abstract=ZH, title="层状 MnO2 正极"),
            _item("OVERRIDE", abstract="DOI: 10.1000/stub"),
        ]
        candidates, stats, recover = self._inventory(items)
        recover.assert_not_called()
        self.assertEqual([c["key"] for c in candidates], ["ENGLISH1", "OVERRIDE"])
        self.assertEqual(candidates[1]["abstract_source"], "publisher")
        self.assertTrue(all(c["title_needs_zh"] for c in candidates))
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["abstract_needs_translation"], 2)
        self.assertEqual(stats["abstract_recovered"], 1)
        self.assertEqual(stats["abstract_already_chinese"], 1)
        self.assertEqual(stats["title_already_chinese"], 1)
        self.assertEqual(stats["title_needs_translation"], 2)

    def test_item_with_translated_title_is_not_a_title_candidate(self):
        items = [_item("TITLED01", abstract=ZH, title=EN_TITLE, extra="funder: ABC\ntranslated-title: 已有标题")]
        candidates, stats, _ = self._inventory(items)
        self.assertEqual(candidates, [])
        self.assertEqual(stats["title_present"], 1)
        self.assertEqual(stats["title_needs_translation"], 0)

    def test_title_only_item_is_still_a_candidate(self):
        items = [_item("TITLEON1", abstract=ZH, title=EN_TITLE)]
        candidates, stats, _ = self._inventory(items)
        self.assertEqual([c["key"] for c in candidates], ["TITLEON1"])
        self.assertNotIn("abstract", candidates[0])
        self.assertTrue(candidates[0]["title_needs_zh"])

    def test_unrecoverable_item_is_counted_not_staged(self):
        candidates, stats, _ = self._inventory([_item("BROKEN01", abstract="", title="层状 MnO2 正极")])
        self.assertEqual(candidates, [])
        self.assertEqual(stats["abstract_unrecoverable"], 1)


class StageTests(unittest.TestCase):
    CANDIDATES = [
        {"key": "ABS00001", "title": EN_TITLE, "doi": "", "abstract": EN},
        {"key": "TIT00001", "title": "Spinel ZnMn2O4 cathodes", "doi": "", "title_needs_zh": True},
    ]

    def _stage(self, state_path, *, with_titles, candidates=None):
        candidates = self.CANDIDATES if candidates is None else candidates
        abs_calls, title_calls = [], []

        def fake_abstracts(batch, _client):
            abs_calls.append([r["key"] for r in batch])
            return {r["key"]: "中文摘要" for r in batch}

        def fake_titles(batch, _client):
            title_calls.append([r["key"] for r in batch])
            return {r["key"]: "中文标题" for r in batch}

        with (
            patch.object(sync, "translate_abstract_batch", side_effect=fake_abstracts),
            patch.object(sync, "translate_title_batch", side_effect=fake_titles),
        ):
            state = sync.stage_translations(candidates, state_path, Mock(), with_titles=with_titles)
        return state, abs_calls, title_calls

    def test_titles_are_skipped_without_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, abs_calls, title_calls = self._stage(Path(tmp) / "s.json", with_titles=False)
        self.assertEqual(abs_calls, [["ABS00001"]])
        self.assertEqual(title_calls, [])
        self.assertEqual(state[0]["abstract_zh"], "中文摘要")
        self.assertNotIn("title_zh", state[1])

    def test_titles_are_staged_with_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, abs_calls, title_calls = self._stage(Path(tmp) / "s.json", with_titles=True)
        self.assertEqual(abs_calls, [["ABS00001"]])
        self.assertEqual(title_calls, [["TIT00001"]])
        self.assertEqual(state[1]["title_zh"], "中文标题")
        self.assertEqual(state[1]["title_status"], "pending")

    def test_existing_translation_is_reused_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "s.json"
            state_path.write_text(
                json.dumps([{**self.CANDIDATES[0], "abstract_zh": "旧摘要", "abstract_status": "updated"}]),
                encoding="utf-8",
            )
            state, abs_calls, title_calls = self._stage(state_path, with_titles=True)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(abs_calls, [])
        self.assertEqual(title_calls, [["TIT00001"]])
        self.assertEqual(state[0]["abstract_zh"], "旧摘要")
        self.assertEqual(state[0]["abstract_status"], "updated")
        self.assertEqual({r["key"] for r in saved}, {"ABS00001", "TIT00001"})


class ApplyTests(unittest.TestCase):
    def _state_file(self, tmp):
        path = Path(tmp) / "s.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "key": "ABS00001",
                        "title": EN_TITLE,
                        "doi": "",
                        "abstract_zh": "中文摘要",
                        "abstract_status": "pending",
                        "title_status": "pending",
                    },
                    {
                        "key": "TIT00001",
                        "title": "Spinel",
                        "doi": "",
                        "title_zh": "中文标题",
                        "abstract_status": "pending",
                        "title_status": "pending",
                    },
                ]
            ),
            encoding="utf-8",
        )
        return path

    def test_abstract_only_run_leaves_titles_untouched(self):
        api, writer = Mock(), Mock()
        api.item.return_value = {"key": "ABS00001", "data": {"abstractNote": "中文摘要"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_file(tmp)
            with patch.object(sync.backfill, "update_one") as update_one:
                counts = sync.apply_updates_from_state(api, writer, path, with_titles=False)
            saved = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
        update_one.assert_not_called()
        writer.update_item.assert_not_called()
        self.assertEqual(counts["abstract_already_up_to_date"], 1)
        self.assertEqual(counts["title_updated"], 0)
        self.assertEqual(saved["TIT00001"]["title_status"], "pending")

    def test_with_titles_writes_extra_and_verifies(self):
        api, writer = Mock(), Mock()
        api.item.return_value = {"key": "ABS00001", "data": {"abstractNote": "中文摘要"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_file(tmp)
            with patch.object(sync.backfill, "update_one", return_value="updated") as update_one:
                counts = sync.apply_updates_from_state(api, writer, path, with_titles=True)
            saved = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
        update_one.assert_called_once()
        self.assertEqual(update_one.call_args.args[2]["key"], "TIT00001")
        self.assertEqual(counts["abstract_already_up_to_date"], 1)
        self.assertEqual(counts["title_updated"], 1)
        self.assertEqual(saved["TIT00001"]["title_status"], "updated")

    def test_failed_title_write_is_recorded_not_raised(self):
        api, writer = Mock(), Mock()
        api.item.return_value = {"key": "ABS00001", "data": {"abstractNote": "中文摘要"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = self._state_file(tmp)
            with patch.object(sync.backfill, "update_one", side_effect=sync.backfill.BackfillError("回读失败")):
                counts = sync.apply_updates_from_state(api, writer, path, with_titles=True)
            saved = {r["key"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
        self.assertEqual(counts["title_failed"], 1)
        self.assertEqual(saved["TIT00001"]["title_status"], "failed")


class StateFileTests(unittest.TestCase):
    def test_default_state_file_lives_under_gitignored_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(sync, "state_dir", return_value=Path(tmp)):
                path = sync.default_state_path("COLL0001")
            self.assertEqual(path, Path(tmp) / "paper2zotero" / "zh_state_COLL0001.json")

    def test_corrupt_checkpoint_is_not_silently_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{unfinished", encoding="utf-8")
            with self.assertRaisesRegex(sync.AbstractTranslationError, "拒绝覆盖"):
                sync.load_state_map(state_path)
            self.assertEqual(state_path.read_text(encoding="utf-8"), "{unfinished")

            state_path.write_text(json.dumps([{"no_key": True}]), encoding="utf-8")
            with self.assertRaisesRegex(sync.AbstractTranslationError, "拒绝覆盖"):
                sync.load_state_map(state_path)

    def test_write_state_cleans_temp_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "nested" / "state.json"

            def failing_replace(self, target):
                raise OSError("disk full")

            with patch.object(Path, "replace", failing_replace):
                with self.assertRaises(OSError):
                    sync.write_state(state_path, [{"key": "A"}])
            self.assertEqual(list(state_path.parent.glob("*.tmp")), [])

    def test_manual_overrides_are_loaded_from_a_local_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            override_path = Path(tmp) / "manual.json"
            override_path.write_text(
                json.dumps(
                    {
                        "KEY00001": [EN, "publisher"],
                        "KEY00002": {"abstract": EN, "source": "manual"},
                        "BROKEN": ["only-one"],
                    }
                ),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, {sync.MANUAL_ABSTRACTS_ENV: str(override_path)}),
                redirect_stderr(stderr),
            ):
                overrides = sync.load_manual_abstract_overrides()
        self.assertEqual(overrides["KEY00001"], (EN, "publisher"))
        self.assertEqual(overrides["KEY00002"], (EN, "manual"))
        self.assertNotIn("BROKEN", overrides)
        self.assertIn("忽略无效的手动摘要覆盖项", stderr.getvalue())

    def test_missing_manual_override_file_only_warns(self):
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, {sync.MANUAL_ABSTRACTS_ENV: "/nonexistent/manual.json"}),
            redirect_stderr(stderr),
        ):
            overrides = sync.load_manual_abstract_overrides()
        self.assertEqual(overrides, {})
        self.assertIn("[warn]", stderr.getvalue())

    def test_recovery_failures_are_logged_not_swallowed(self):
        reader = Mock()
        reader.get_pdf_attachments.return_value = []
        stderr = io.StringIO()
        with (
            patch.object(sync.urllib.request, "urlopen", side_effect=RuntimeError("offline")),
            redirect_stderr(stderr),
        ):
            abstract, source = sync.recover_missing_abstract(
                "KEY00001", "10.1000/x", "Title", reader, Path("/nonexistent"), "cfg@example.invalid"
            )
        self.assertEqual((abstract, source), (None, "none"))
        self.assertIn("[warn] Crossref 摘要恢复失败", stderr.getvalue())
        self.assertIn("offline", stderr.getvalue())

    def test_recovery_uses_configured_mailto_in_user_agent(self):
        requests = []

        def capture(request, **_kwargs):
            requests.append(request)
            raise RuntimeError("stop after capture")

        reader = Mock()
        reader.get_pdf_attachments.return_value = []
        with patch.object(sync.urllib.request, "urlopen", capture):
            sync.recover_missing_abstract(
                "KEY00001", "10.1000/x", "Title", reader, Path("/nonexistent"), "cfg@example.invalid"
            )
        user_agent = requests[0].get_header("User-agent") or ""
        self.assertIn("cfg@example.invalid", user_agent)
        self.assertNotIn("liuchzzyy@gmail.com", user_agent)

    def test_recovery_without_mailto_has_no_personal_address(self):
        requests = []

        def capture(request, **_kwargs):
            requests.append(request)
            raise RuntimeError("stop after capture")

        reader = Mock()
        reader.get_pdf_attachments.return_value = []
        with patch.object(sync.urllib.request, "urlopen", capture):
            sync.recover_missing_abstract("KEY00001", "10.1000/x", "Title", reader, Path("/nonexistent"))
        self.assertEqual(requests[0].get_header("User-agent"), "ZoteroCli/2.0")


if __name__ == "__main__":
    unittest.main()
