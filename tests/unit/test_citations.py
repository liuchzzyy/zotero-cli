"""Citation export formatting regressions for M-05."""

from __future__ import annotations

import json

from zotero_cli.core.zotero.citations import CitationExportMixin
from zotero_cli.models import Creator, Item


class _Reader(CitationExportMixin):
    def __init__(self, item: Item) -> None:
        self._item = item

    def get_item(self, key: str) -> Item | None:
        return self._item if key == self._item.key else None


def _item(**overrides: object) -> Item:
    data: dict[str, object] = {
        "key": "ATTN001",
        "item_type": "journalArticle",
        "title": "Attention Is All You Need",
        "creators": [Creator("Ashish", "Vaswani", "author")],
        "abstract": "We propose a new architecture.",
        "date": "2017-06-12 2017-06-12",
        "url": "https://example.org/paper",
        "doi": "10.5555/attention",
        "tags": ["transformer"],
        "collections": ["COLML01"],
        "date_added": "2024-01-01",
        "date_modified": "2024-01-02",
        "extra": {"publicationTitle": "NeurIPS"},
    }
    data.update(overrides)
    return Item(**data)  # type: ignore[arg-type]


def _reader(**overrides: object) -> _Reader:
    return _Reader(_item(**overrides))


def test_bibtex_uses_year_only() -> None:
    bibtex = _reader().export_citation("ATTN001", "bibtex")
    assert bibtex is not None
    assert "  year = {2017}," in bibtex
    assert "2017-06-12" not in bibtex


def test_ris_uses_year_only() -> None:
    ris = _reader(date="2012-05-00 2012-05").export_citation("ATTN001", "ris")
    assert ris is not None
    assert "PY  - 2012" in ris
    assert "2012-05-00" not in ris


def test_csl_uses_date_parts_instead_of_raw() -> None:
    csl = _reader(date="2012-05-00 2012-05").export_citation("ATTN001", "csl-json")
    assert csl is not None
    payload = json.loads(csl)
    assert payload["issued"] == {"date-parts": [[2012, 5]]}
    assert "raw" not in payload["issued"]


def test_csl_month_and_day_are_kept_when_present() -> None:
    payload = json.loads(_reader(date="2024-07-13 2024-07-13").export_citation("ATTN001", "csl-json") or "{}")
    assert payload["issued"] == {"date-parts": [[2024, 7, 13]]}


def test_html_markup_and_entities_are_cleaned_in_every_format() -> None:
    reader = _reader(title="Battery <sub>2</sub> &amp; Ga&ndash;Sn alloys")
    bibtex = reader.export_citation("ATTN001", "bibtex") or ""
    ris = reader.export_citation("ATTN001", "ris") or ""
    csl = json.loads(reader.export_citation("ATTN001", "csl-json") or "{}")
    assert "<sub>" not in bibtex and "<sub>" not in ris
    assert csl["title"] == "Battery 2 & Ga–Sn alloys"
    assert "Battery 2 \\& Ga–Sn alloys" in bibtex
    assert "Battery 2 & Ga–Sn alloys" in ris


def test_html_is_stripped_from_abstracts_and_creator_names() -> None:
    item = _item(
        abstract="<p>Plain <i>abstract</i></p>",
        creators=[Creator("A<b>sh</b>ish", "Vas&ndash;wani", "author")],
    )
    bibtex = _Reader(item).export_citation("ATTN001", "bibtex") or ""
    assert "author = {Vas–wani, Ashish}" in bibtex
    assert "<p>" not in bibtex and "<i>" not in bibtex


def test_common_item_types_map_to_specific_bibtex_entries() -> None:
    expected = {
        "bookSection": "@incollection",
        "conferencePaper": "@inproceedings",
        "report": "@techreport",
        "preprint": "@unpublished",
    }
    for item_type, entry in expected.items():
        bibtex = _reader(item_type=item_type).export_citation("ATTN001", "bibtex") or ""
        assert bibtex.startswith(entry), (item_type, bibtex.splitlines()[0])


def test_common_item_types_map_to_specific_csl_and_ris_types() -> None:
    blog = _reader(item_type="blogPost")
    assert json.loads(blog.export_citation("ATTN001", "csl-json") or "{}")["type"] == "post-weblog"
    assert (blog.export_citation("ATTN001", "ris") or "").startswith("TY  - BLOG")
    program = _reader(item_type="computerProgram")
    assert json.loads(program.export_citation("ATTN001", "csl-json") or "{}")["type"] == "software"
    assert (program.export_citation("ATTN001", "ris") or "").startswith("TY  - COMP")


def test_bibtex_escapes_braces_and_backslashes() -> None:
    bibtex = _reader(title="A {weird} title").export_citation("ATTN001", "bibtex") or ""
    assert "\\{" in bibtex and "\\}" in bibtex


def test_bibtex_backslash_is_escaped_without_double_escaping() -> None:
    bibtex = _reader(title=r"back\slash with {braces}").export_citation("ATTN001", "bibtex") or ""
    # Single-pass escaping: the braces introduced by the backslash escape stay
    # unescaped, so one token is emitted instead of a nested double escape.
    assert r"\textbackslash{}" in bibtex
    assert r"\textbackslash\{\}" not in bibtex


def test_unknown_format_and_missing_key_return_none() -> None:
    assert _reader().export_citation("ATTN001", "xml") is None
    assert _reader().export_citation("NONEXIST", "bibtex") is None
