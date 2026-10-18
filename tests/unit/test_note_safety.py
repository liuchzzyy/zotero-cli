"""AI-note previews and figure inputs must not cross safety boundaries."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.config import AiNoteConfig, AppConfig
from zotero_cli.core.mineru import MinerUParseResult
from zotero_cli.core.notes.errors import NoteAnalysisError
from zotero_cli.core.notes.figures import load_figures, mark_figures
from zotero_cli.core.notes.pipeline import analyze_item
from zotero_cli.models import Attachment, Item, Note

# A real 8x8 JPEG; a legal comment segment brings it above the figure-size floor.
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
    monkeypatch.setattr("zotero_cli.core.notes.pipeline.MinerUParseCache", cache)
    reader = MagicMock()
    reader.get_item.return_value = _item()
    reader.get_pdf_attachments.return_value = [Attachment("PDF1", "ITEM1", "paper.pdf", "application/pdf", pdf)]
    writer = MagicMock()
    writer.add_note.return_value = "NOTE1"
    ai_client = MagicMock()
    ai_client.config = AiNoteConfig()
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
    assert ai_client.chat.call_count == 3


@pytest.mark.parametrize("escape", ["traversal", "absolute", "file-symlink", "directory-symlink", "prefix-sibling"])
def test_figure_references_never_read_outside_parse_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape: str
) -> None:
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
    else:
        ref = "images/../../raw-other/private.jpg"
    reads: list[Path] = []
    read_bytes = Path.read_bytes

    def track_reads(path: Path) -> bytes:
        reads.append(path.resolve())
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", track_reads)
    text, refs = mark_figures(f"![]({ref})\n![](images/normal.jpg)", raw)
    loaded = load_figures(refs, limit=12)

    assert reads == [normal.resolve()]
    assert len(loaded) == 1
    number, data_url = loaded[0]
    assert f"【图 {number}】" in text
    assert data_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == normal_bytes


def test_figure_loader_never_opens_nonregular_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = tmp_path / "raw"
    directory = raw / "images" / "directory.jpg"
    directory.mkdir(parents=True)
    # Large directories can fall inside the image size window; size is not a type check.
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
    _, refs = mark_figures("![](images/directory.jpg)\n![](images/normal.jpg)", raw)
    loaded = load_figures(refs, limit=12)

    assert reads == [normal]
    assert len(loaded) == 1
    assert base64.b64decode(loaded[0][1].split(",", 1)[1]) == normal_bytes


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
    ai_client.chat.return_value = json.dumps({"sections": sections})

    with pytest.raises(NoteAnalysisError) as caught:
        analyze_item(reader, writer, ai_client, "ITEM1", no_short_note=True)

    assert caught.value.code == "runtime_error"
    assert writer.mock_calls == []


def test_cli_short_note_dry_run_uses_real_analysis_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = MagicMock()
    reader.get_item.return_value = _item()
    reader.get_notes.return_value = [Note("LATEST", "ITEM1", "<p>AI条目分析 - Current evidence.</p>")]
    ai_client = MagicMock()
    writer_constructor = MagicMock(side_effect=AssertionError("dry-run must not construct a writer"))
    monkeypatch.setattr("zotero_cli.commands.notes.ai_analyze.load_config", lambda **kwargs: AppConfig())
    monkeypatch.setattr(
        "zotero_cli.commands.notes.ai_analyze.load_ai_note_config",
        lambda: AiNoteConfig(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model"),
    )
    monkeypatch.setattr("zotero_cli.commands.notes.ai_analyze.ZoteroReader", lambda *args, **kwargs: reader)
    monkeypatch.setattr("zotero_cli.commands.notes.ai_analyze.ZoteroWriter", writer_constructor)
    monkeypatch.setattr("zotero_cli.commands.notes.ai_analyze.AiClient", lambda config: ai_client)

    result = CliRunner().invoke(main, ["--json", "ai_analyze", "ITEM1", "--dry-run", "--short-note-only"])

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "dry_run"
    assert envelope["data"]["note_key"] == "LATEST"
    assert envelope["data"]["prompt_preview"]
    ai_client.chat.assert_not_called()
    writer_constructor.assert_not_called()
    reader.close.assert_called_once()
