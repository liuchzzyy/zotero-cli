"""AI 笔记两阶段生成契约测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from tests.support import jpeg_bytes

from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.evidence import OUTPUT_RESERVE, REQUEST_CEILING, SAFETY_RESERVE
from zotero_cli.ai_notes.stages import MAX_PACKET_CHARS
from zotero_cli.ai_pdf import MinerUParseResult
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.models import Attachment, Item


def _setup(tmp_path):
    root = tmp_path / "parsed"
    raw = root / "raw" / "images"
    raw.mkdir(parents=True)
    (raw / "0.jpg").write_bytes(jpeg_bytes())
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    parsed = MinerUParseResult(
        source_path=pdf,
        fingerprint="a" * 64,
        model_version="vlm",
        root=root,
        markdown_path=root / "document.md",
        content_list_path=root / "content_list.json",
        manifest_path=root / "manifest.json",
    )
    parsed.markdown_path.write_text("capacity 230 mAh g-1 at 0.1 A g-1")
    parsed.content_list_path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "capacity 230 mAh g-1 at 0.1 A g-1", "page_idx": 1},
                {"type": "image", "img_path": "images/0.jpg", "page_idx": 2},
            ]
        )
    )
    parsed.manifest_path.write_text("{}")
    att = Attachment("ATT1", "ITEM1", "paper.pdf", "application/pdf", pdf)
    item = Item(
        key="ITEM1",
        item_type="book",
        title="Test",
        creators=[],
        abstract="",
        date="2024",
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="2024",
        date_modified="2024",
        extra={},
    )
    reader = MagicMock()
    reader.get_item.return_value = item
    reader.get_pdf_attachments.return_value = [att]
    writer = MagicMock()
    writer.add_note.return_value = "NOTE1"
    client = MagicMock()
    client.config = AiNoteConfig(api_key="k", base_url="http://test", model="group/deepseek")
    client.get_model_chain.return_value = ["group/deepseek"]
    return reader, writer, client, att, parsed


def _packet():
    return json.dumps(
        {
            "evidence": [
                {
                    "source": "[ATT1 | main | PDF p.2]",
                    "claim": "容量",
                    "conditions": "0.1 A g-1",
                    "data": "230 mAh g-1",
                    "figure_refs": [1],
                }
            ],
            "unresolved": [],
        },
        ensure_ascii=False,
    )


def test_text_then_visual_with_all_main_figures_before_write(tmp_path):
    reader, writer, client, att, parsed = _setup(tmp_path)
    client.chat.side_effect = [
        _packet(),
        json.dumps({"sections": [{"type": "paragraph", "text": f"内容 {i}"} for i in range(12)]}),
    ]
    with patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={att.path: parsed}):
        result = analyze_item(reader, writer, client, "ITEM1", no_short_note=True)
    assert result["status"] == "ok"
    assert client.chat.call_count == 2
    first, second = client.chat.call_args_list
    assert not first.kwargs.get("figures")
    assert "230 mAh g-1" in first.args[0]
    assert len(second.kwargs["figures"]) == 1
    assert "0.1 A g-1" in second.args[0]
    writer.add_note.assert_called_once()


def test_dry_run_does_not_reject_images_only_due_to_maximum_packet_reserve(tmp_path):
    reader, writer, client, att, parsed = _setup(tmp_path)
    # 图数取“实际可发送、但按最大证据包预留就放不下”的区间：正文图不可丢弃。
    count = (REQUEST_CEILING - OUTPUT_RESERVE - SAFETY_RESERVE - MAX_PACKET_CHARS) // 4096 + 10
    parsed.content_list_path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "正文", "page_idx": 0},
                *[{"type": "image", "img_path": "images/0.jpg", "page_idx": i} for i in range(count)],
            ]
        )
    )
    with patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={att.path: parsed}):
        result = analyze_item(reader, writer, client, "ITEM1", dry_run=True)
    assert result["figures"] == count
    assert result["visual_preview_kind"] == "requires_smaller_evidence_packet"
    assert client.chat.call_count == 0
    assert writer.mock_calls == []


def test_actual_packet_allows_main_figures_beyond_preview_reserve(tmp_path):
    reader, writer, client, att, parsed = _setup(tmp_path)
    count = (REQUEST_CEILING - OUTPUT_RESERVE - SAFETY_RESERVE - MAX_PACKET_CHARS) // 4096 + 10
    parsed.content_list_path.write_text(
        json.dumps(
            [
                {"type": "text", "text": "正文", "page_idx": 0},
                *[{"type": "image", "img_path": "images/0.jpg", "page_idx": i} for i in range(count)],
            ]
        )
    )
    client.chat.side_effect = [
        _packet(),
        json.dumps({"sections": [{"type": "paragraph", "text": f"内容 {i}"} for i in range(12)]}),
    ]
    with patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={att.path: parsed}):
        result = analyze_item(reader, writer, client, "ITEM1", no_short_note=True)
    assert result["status"] == "ok"
    assert result["main_images_sent"] == count
    assert client.chat.call_count == 2
    first, second = client.chat.call_args_list
    assert not first.kwargs["figures"]
    assert len(second.kwargs["figures"]) == count
    assert "text-evidence" in second.args[0]
    writer.add_note.assert_called_once()


def test_invalid_source_packet_never_writes(tmp_path):
    reader, writer, client, att, parsed = _setup(tmp_path)
    client.chat.return_value = json.dumps(
        {"evidence": [{"source": "", "claim": "容量", "conditions": "", "data": "230", "figure_refs": [1]}]}
    )
    with (
        patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={att.path: parsed}),
        pytest.raises(NoteAnalysisError, match="证据包"),
    ):
        analyze_item(reader, writer, client, "ITEM1", no_short_note=True)
    assert client.chat.call_count == 1
    writer.add_note.assert_not_called()


def test_visual_image_rejection_never_downgrades_to_text(tmp_path):
    reader, writer, client, att, parsed = _setup(tmp_path)
    client.chat.side_effect = [_packet(), RuntimeError("image input is not supported")]
    with (
        patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={att.path: parsed}),
        pytest.raises(NoteAnalysisError),
    ):
        analyze_item(reader, writer, client, "ITEM1", no_short_note=True)
    writer.add_note.assert_not_called()
