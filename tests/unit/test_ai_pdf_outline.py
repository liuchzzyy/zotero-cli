"""MinerU Markdown 大纲解析与区段提取的领域层测试。"""

from __future__ import annotations

from zotero_cli.ai_pdf.outline import extract_markdown_section, parse_markdown_outline

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
