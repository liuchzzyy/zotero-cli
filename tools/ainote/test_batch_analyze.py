"""Unit tests for batch AI note analysis tool."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import batch_analyze as ba


class BatchAnalyzeTests(unittest.TestCase):
    def test_resolve_collection_key(self) -> None:
        reader = MagicMock()
        col_c = SimpleNamespace(key="KEY_C", name="Sub Child", children=[])
        col_b = SimpleNamespace(key="KEY_B", name="Child", children=[col_c])
        col_a = SimpleNamespace(key="KEY_A", name="Root", children=[col_b])
        reader.get_collections.return_value = [col_a]

        self.assertEqual(ba.resolve_collection_key(reader, "KEY_C"), "KEY_C")
        self.assertEqual(ba.resolve_collection_key(reader, "Child"), "KEY_B")
        self.assertEqual(ba.resolve_collection_key(reader, "SUB CHILD"), "KEY_C")
        self.assertIsNone(ba.resolve_collection_key(reader, "Nonexistent"))

    def test_get_collection_items_recursive(self) -> None:
        reader = MagicMock()
        col_c = SimpleNamespace(key="KEY_C", name="Sub Child", children=[])
        col_b = SimpleNamespace(key="KEY_B", name="Child", children=[col_c])
        col_a = SimpleNamespace(key="KEY_A", name="Root", children=[col_b])
        reader.get_collections.return_value = [col_a]

        item_a = SimpleNamespace(key="ITEM_A")
        item_b = SimpleNamespace(key="ITEM_B")
        item_c = SimpleNamespace(key="ITEM_C")

        def _get_items(key: str) -> list[Any]:
            return {"KEY_A": [item_a], "KEY_B": [item_b], "KEY_C": [item_c, item_b]}.get(key, [])

        reader.get_collection_items.side_effect = _get_items

        items = ba.get_collection_items_recursive(reader, "KEY_A")
        # Ensure deduped and all children included
        self.assertEqual([i.key for i in items], ["ITEM_A", "ITEM_B", "ITEM_C"])

    def test_load_and_save_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "state.json"
            self.assertEqual(ba.load_state(state_file), {})

            state_data = {"ITEM1": {"status": "ok", "note_key": "NOTE1"}}
            ba.save_state(state_file, state_data)

            loaded = ba.load_state(state_file)
            self.assertEqual(loaded, state_data)

    @patch("batch_analyze.load_config")
    @patch("batch_analyze.load_ai_note_config")
    @patch("batch_analyze.get_data_dir")
    @patch("batch_analyze.resolve_library_id")
    @patch("batch_analyze.ZoteroReader")
    @patch("batch_analyze.AiClient")
    @patch("batch_analyze.analyze_item")
    def test_run_batch_dry_run_skips_and_processes(
        self,
        mock_analyze_item: MagicMock,
        mock_ai_client: MagicMock,
        mock_reader_cls: MagicMock,
        mock_resolve_lib: MagicMock,
        mock_get_data_dir: MagicMock,
        mock_load_ai_cfg: MagicMock,
        mock_load_cfg: MagicMock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "test_state.json"
            mock_reader = MagicMock()
            mock_reader_cls.return_value = mock_reader

            col = SimpleNamespace(key="COL1", name="AA", children=[])
            mock_reader.get_collections.return_value = [col]

            item1 = SimpleNamespace(key="ITEM1", title="Title 1")
            item2 = SimpleNamespace(key="ITEM2", title="Title 2")
            mock_reader.get_collection_items.return_value = [item1, item2]

            # ITEM1 has pdf, ITEM2 has no pdf
            mock_reader.get_pdf_attachments.side_effect = lambda k: (
                [SimpleNamespace(key="PDF1")] if k == "ITEM1" else []
            )
            mock_reader.get_item.return_value = SimpleNamespace(tags=[])

            mock_analyze_item.return_value = {
                "status": "dry_run",
                "item_key": "ITEM1",
                "paper_type": "research_article",
                "template": "research-article",
                "pdf_count": 1,
                "chars": 1234,
                "figures": 2,
                "figure_refs": 2,
            }

            summary = ba.run_batch("AA", dry_run=True, state_path=state_file)

            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["processed"], 1)

            saved = ba.load_state(state_file)
            self.assertIn("ITEM1", saved)
            self.assertEqual(saved["ITEM1"]["status"], "dry_run")
            self.assertIn("ITEM2", saved)
            self.assertEqual(saved["ITEM2"]["error"], "no_pdf")


if __name__ == "__main__":
    unittest.main()
