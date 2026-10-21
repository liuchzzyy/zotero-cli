"""MinerU Markdown 大纲解析与区段提取的领域层测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zotero_cli.ai_pdf.content import extract_markdown_section, parse_markdown_outline, read_pdf_content
from zotero_cli.ai_pdf.errors import PdfSectionNotFoundError
from zotero_cli.ai_pdf.models import MinerUParseResult

MARKDOWN = """# Introduction

Paragraph one.

## Methods

### Details

More text.

# Results

Final text.
"""


def test_parse_outline_numbers_headings_in_document_order() -> None:
    outline = parse_markdown_outline(MARKDOWN)
    assert outline == [
        (1, "Introduction", 1),
        (2, "Methods", 2),
        (3, "Details", 3),
        (4, "Results", 1),
    ]


def test_parse_outline_strips_trailing_whitespace_and_ignores_prose() -> None:
    outline = parse_markdown_outline("plain paragraph\n##  Section with spaces  \ntext\n")
    assert outline == [(1, "Section with spaces", 2)]


def test_parse_outline_without_headings_is_empty() -> None:
    assert parse_markdown_outline("no headings here\njust text\n") == []


def test_extract_section_includes_nested_subsections() -> None:
    assert extract_markdown_section(MARKDOWN, 2) == "## Methods\n\n### Details\n\nMore text."


def test_extract_section_stops_at_same_or_higher_level() -> None:
    assert extract_markdown_section(MARKDOWN, 1) == (
        "# Introduction\n\nParagraph one.\n\n## Methods\n\n### Details\n\nMore text."
    )


def test_extract_section_out_of_range_is_empty() -> None:
    assert extract_markdown_section(MARKDOWN, 0) == ""
    assert extract_markdown_section(MARKDOWN, 99) == ""


def test_read_pdf_content_reports_missing_section(tmp_path: Path) -> None:
    markdown_path = tmp_path / "document.md"
    markdown_path.write_text(MARKDOWN, encoding="utf-8")
    content_path = tmp_path / "content_list.json"
    content_path.write_text("[]", encoding="utf-8")
    parsed = MinerUParseResult(
        source_path=tmp_path / "paper.pdf",
        fingerprint="a" * 64,
        model_version="vlm",
        root=tmp_path,
        markdown_path=markdown_path,
        content_list_path=content_path,
        manifest_path=tmp_path / "manifest.json",
    )

    with pytest.raises(PdfSectionNotFoundError, match="Section 99 not found"):
        read_pdf_content(parsed, section=99)
