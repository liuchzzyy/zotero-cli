"""Unit tests for the pdf --outline / --section Markdown helpers (M-19 coverage)."""

from __future__ import annotations

from zotero_cli.commands.attachments.pdf import _extract_section, _parse_outline

MARKDOWN = """# Introduction

Paragraph one.

## Methods

### Details

More text.

# Results

Final text.
"""


def test_parse_outline_numbers_headings_in_document_order() -> None:
    outline = _parse_outline(MARKDOWN)
    assert outline == [
        (1, "Introduction", 1),
        (2, "Methods", 2),
        (3, "Details", 3),
        (4, "Results", 1),
    ]


def test_parse_outline_strips_trailing_whitespace_and_ignores_prose() -> None:
    outline = _parse_outline("plain paragraph\n##  Section with spaces  \ntext\n")
    assert outline == [(1, "Section with spaces", 2)]


def test_parse_outline_without_headings_is_empty() -> None:
    assert _parse_outline("no headings here\njust text\n") == []


def test_extract_section_includes_nested_subsections() -> None:
    assert _extract_section(MARKDOWN, 2) == "## Methods\n\n### Details\n\nMore text."


def test_extract_section_stops_at_same_or_higher_level() -> None:
    assert _extract_section(MARKDOWN, 1) == (
        "# Introduction\n\nParagraph one.\n\n## Methods\n\n### Details\n\nMore text."
    )


def test_extract_section_out_of_range_is_empty() -> None:
    assert _extract_section(MARKDOWN, 0) == ""
    assert _extract_section(MARKDOWN, 99) == ""
