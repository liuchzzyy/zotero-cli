"""AI 笔记预览与图片输入都不得越过安全边界。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_pdf import MinerUParseResult
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.models import Attachment, Item, Note

# 一张真实的 8x8 JPEG；加上合法注释段后超过图片体积下限。
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAPTGF2YzYzLjEuMTAxAP/bAEMACAQEBAQEBQUFBQUFBgYGBgYGBgYGBgYGBgcHBwgICAcHBwYG"
    "BwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/EAEwAAQEAAAAAAAAAAAAAAAAAAAAGAQEBAAAAAAAAAAAAAAAAAAAGBxABAAAAAAAAAAAA"
    "AAAAAAAAABEBAAAAAAAAAAAAAAAAAAAAAP/AABEIAAgACAMBIgACEQADEQD/2gAMAwEAAhEDEQA/AIsATX9//9k="
)


def _write_jpeg(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    comment = b"figure fixture " * 512
    payload = _JPEG[:2] + b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment + _JPEG[2:]
    path.write_bytes(payload)
    return payload


def _item() -> Item:
    return Item(
        key="ITEM1",
        item_type="journalArticle",
        title="Safety test",
        creators=[],
        abstract=None,
        date=None,
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )


@pytest.fixture
def analysis_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock, MagicMock]:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test fixture")
    root = tmp_path / "parse"
    root.mkdir()
    markdown = root / "document.md"
    markdown.write_text("Introduction. Current evidence.", encoding="utf-8")
    (root / "content_list.json").write_text(
        json.dumps([{"type": "text", "text": "Introduction. Current evidence.", "page_idx": 0}]), encoding="utf-8"
    )
    parsed = MinerUParseResult(
        source_path=pdf,
        fingerprint="a" * 64,
        model_version="vlm",
        root=root,
        markdown_path=markdown,
        content_list_path=root / "content_list.json",
        manifest_path=root / "manifest.json",
    )
    cache = MagicMock()
    cache.return_value.ensure_many.return_value = {pdf: parsed}
    monkeypatch.setattr("zotero_cli.ai_notes.documents.MinerUParseCache", cache)
    reader = MagicMock()
    reader.get_item.return_value = _item()
    reader.get_pdf_attachments.return_value = [Attachment("PDF1", "ITEM1", "paper.pdf", "application/pdf", pdf)]
    writer = MagicMock()
    writer.add_note.return_value = "NOTE1"
    ai_client = MagicMock()
    ai_client.config = AiNoteConfig()
    ai_client.get_model_chain.return_value = ["group/deepseek"]
    return reader, writer, ai_client


@pytest.mark.parametrize("has_writer", [True, False], ids=["fake-writer", "no-credentials"])
def test_short_note_dry_run_previews_without_chat_or_writes(has_writer: bool) -> None:
    reader = MagicMock()
    reader.get_item.return_value = _item()
    reader.get_notes.return_value = [
        Note("OLD", "ITEM1", "<h1>AI条目分析 - Old</h1><p>Outdated evidence.</p>"),
        Note("LATEST", "ITEM1", "<h1>AI条目分析 - Safety test</h1><p>Current evidence.</p>"),
        Note("OTHER", "ITEM1", "<p>Ordinary note.</p>"),
    ]
    writer = MagicMock() if has_writer else None
    ai_client = MagicMock()
    ai_client.chat.return_value = '{"short_note": "体系 | 机制 | 疑问：待验证"}'

    result = analyze_item(reader, writer, ai_client, "ITEM1", dry_run=True, short_note_only=True)

    ai_client.chat.assert_not_called()
    if writer is not None:
        assert writer.mock_calls == []
    assert result["status"] == "dry_run"
    assert result["item_key"] == "ITEM1"
    assert result["item_type"] == "journalArticle"
    assert result["note_key"] == "LATEST"
    assert result["template"] == "short-note"
    assert result["chars"] > 0
    assert 0 < len(result["prompt_preview"]) <= 1200
    assert "Current evidence." in result["prompt_preview"]
    assert "Outdated evidence." not in result["prompt_preview"]
    assert "<untrusted-ai-note>" in result["prompt_preview"]
    reader.get_pdf_attachments.assert_not_called()


@pytest.mark.parametrize("has_writer", [True, False], ids=["fake-writer", "no-credentials"])
def test_analysis_dry_run_previews_without_classification_chat(
    analysis_inputs: tuple[MagicMock, MagicMock, MagicMock], has_writer: bool
) -> None:
    reader, writer, ai_client = analysis_inputs
    ai_client.chat.return_value = '{"paper_type": "research_article"}'

    result = analyze_item(reader, writer if has_writer else None, ai_client, "ITEM1", dry_run=True)

    ai_client.chat.assert_not_called()
    assert writer.mock_calls == []
    assert result["status"] == "dry_run"
    assert result["template"] == "research-article"
    assert result["pdf_count"] == 1
    assert 0 < len(result["prompt_preview"]) <= 1200


def test_classification_network_failure_is_retryable_without_tagging(
    analysis_inputs: tuple[MagicMock, MagicMock, MagicMock],
) -> None:
    reader, writer, ai_client = analysis_inputs
    ai_client.chat.side_effect = [
        ConnectionError("temporary classification outage"),
        '{"paper_type": "research_article"}',
        json.dumps(
            {
                "evidence": [
                    {
                        "source": "[PDF1 | main | PDF p.1]",
                        "claim": "Current evidence",
                        "conditions": "未知",
                        "data": "未知",
                        "figure_refs": [],
                    }
                ],
                "unresolved": [],
            }
        ),
        json.dumps({"sections": [{"type": "paragraph", "text": "Evidence."}] * 12}),
    ]

    with pytest.raises(NoteAnalysisError) as caught:
        analyze_item(reader, writer, ai_client, "ITEM1", no_short_note=True)

    assert caught.value.code == "network_error"
    assert caught.value.retryable is True
    assert writer.mock_calls == []

    result = analyze_item(reader, writer, ai_client, "ITEM1", no_short_note=True)

    assert result["status"] == "ok"
    writer.add_note.assert_called_once()
    assert ai_client.chat.call_count == 4


@pytest.mark.parametrize(
    "escape", ["traversal", "absolute", "file-symlink", "directory-symlink", "prefix-sibling", "no-symlink"]
)
def test_structured_image_paths_never_read_outside_parse_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
    from types import SimpleNamespace

    from zotero_cli.ai_notes.evidence import build_evidence, plan_request

    raw = tmp_path / "parse" / "raw"
    normal = raw / "images" / "normal.jpg"
    normal_bytes = _write_jpeg(normal)
    outside = tmp_path / "parse" / "raw-other" / "private.jpg"
    _write_jpeg(outside)
    if escape == "traversal":
        outside = tmp_path / "parse" / "private.jpg"
        _write_jpeg(outside)
        ref = "images/../../private.jpg"
    elif escape == "absolute":
        ref = str(outside)
    elif escape == "file-symlink":
        (raw / "images" / "link.jpg").symlink_to(outside)
        ref = "images/link.jpg"
    elif escape == "directory-symlink":
        (raw / "images" / "linked").symlink_to(outside.parent, target_is_directory=True)
        ref = "images/linked/private.jpg"
    elif escape == "prefix-sibling":
        ref = "images/../../raw-other/private.jpg"
    else:
        ref = "images/normal.jpg"

    reads: list[Path] = []
    read_bytes = Path.read_bytes

    def track_reads(path: Path) -> bytes:
        reads.append(path.resolve())
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", track_reads)
    blocks = [
        {"type": "image", "img_path": ref, "page_idx": 0},
        {"type": "image", "img_path": "images/normal.jpg", "page_idx": 0},
    ]
    parsed = SimpleNamespace(root=tmp_path / "parse", content_list=blocks)
    if escape == "no-symlink":
        evidence = build_evidence([("ATT", "main", parsed)])
        plan = plan_request(evidence, "prompt", "group/deepseek")
        assert [number for number, _ in plan.images] == [1, 2]
        assert reads.count(normal.resolve()) == 1
    else:
        with pytest.raises(ValueError, match="escapes cache"):
            build_evidence([("ATT", "main", parsed)])
        assert reads == []
    assert normal_bytes is not None


def test_structured_image_loader_never_opens_nonregular_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from zotero_cli.ai_notes.evidence import build_evidence, plan_request

    raw = tmp_path / "raw"
    directory = raw / "images" / "directory.jpg"
    directory.mkdir(parents=True)
    # 大目录也可能落入图片体积区间，体积并不等于类型校验。
    for index in range(512):
        (directory / f"entry-{index}").touch()
    assert directory.stat().st_size >= 4096
    normal = raw / "images" / "normal.jpg"
    normal_bytes = _write_jpeg(normal)
    reads: list[Path] = []
    read_bytes = Path.read_bytes

    def track_reads(path: Path) -> bytes:
        reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", track_reads)
    blocks = [
        {"type": "image", "img_path": "images/directory.jpg", "page_idx": 0},
        {"type": "image", "img_path": "images/normal.jpg", "page_idx": 0},
    ]
    evidence = build_evidence([("ATT", "main", SimpleNamespace(root=raw.parent, content_list=blocks))])
    # is_file() 在读取任何字节之前就拒绝目录；主图缺失时
    # 整个请求失败，而不是静默丢弃。
    with pytest.raises(ValueError, match="required main image 1 is unavailable"):
        plan_request(evidence, "prompt", "group/deepseek")
    assert reads == []
    assert normal_bytes is not None


@pytest.mark.parametrize(
    "sections",
    [
        [None] * 12,
        [{}] * 12,
        [{"type": "paragraph", "text": "  "}] * 12,
        [{"type": "bullet_list", "items": []}, {"type": "table", "rows": []}] * 6,
    ],
    ids=["null-blocks", "empty-blocks", "blank-paragraphs", "empty-containers"],
)
def test_analysis_without_renderable_body_never_writes_a_title_only_note(
    analysis_inputs: tuple[MagicMock, MagicMock, MagicMock], sections: list[object]
) -> None:
    reader, writer, ai_client = analysis_inputs
    reader.get_item.return_value.item_type = "book"
    ai_client.chat.side_effect = [
        json.dumps(
            {
                "evidence": [
                    {
                        "source": "[PDF1 | main | PDF p.1]",
                        "claim": "Current evidence",
                        "conditions": "未知",
                        "data": "未知",
                        "figure_refs": [],
                    }
                ],
                "unresolved": [],
            }
        ),
        json.dumps({"sections": sections}),
        json.dumps({"sections": sections}),
    ]

    with pytest.raises(NoteAnalysisError) as caught:
        analyze_item(reader, writer, ai_client, "ITEM1", no_short_note=True)

    assert caught.value.code == "runtime_error"
    assert writer.mock_calls == []
