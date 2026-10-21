"""Citation export formatting for item records.

Query and connection concerns stay in zotero_cli.zotero.reader; this
module only renders an already-hydrated zotero_cli.zotero.models.Item as BibTeX,
CSL-JSON, or RIS. ZoteroReader composes CitationExportMixin, so callers keep
using reader.export_citation().
"""

from __future__ import annotations

import html
import json
import re
from abc import ABC, abstractmethod

from zotero_cli.zotero.models import Item

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BIBTEX_ESCAPE_RE = re.compile(r"([\\&%#_{}$])")
_BIBTEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
}
_YEAR_RE = re.compile(r"\b(\d{4})\b")
_DATE_PARTS_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?")

# Zotero item types -> BibTeX entry types.
_BIBTEX_TYPES = {
    "journalArticle": "article",
    "book": "book",
    "bookSection": "incollection",
    "conferencePaper": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "manuscript": "unpublished",
    "preprint": "unpublished",
    "webpage": "misc",
    "computerProgram": "misc",
    "blogPost": "misc",
}

# Zotero item types -> RIS reference types.
_RIS_TYPES = {
    "journalArticle": "JOUR",
    "book": "BOOK",
    "bookSection": "CHAP",
    "conferencePaper": "CONF",
    "thesis": "THES",
    "report": "RPRT",
    "webpage": "ELEC",
    "preprint": "JOUR",
    "patent": "PAT",
    "newspaperArticle": "NEWS",
    "magazineArticle": "MGZN",
    "manuscript": "MANSCPT",
    "computerProgram": "COMP",
    "blogPost": "BLOG",
}

# Zotero item types -> CSL-JSON item types.
_CSL_TYPES = {
    "journalArticle": "article-journal",
    "book": "book",
    "bookSection": "chapter",
    "conferencePaper": "paper-conference",
    "thesis": "thesis",
    "report": "report",
    "webpage": "webpage",
    "preprint": "article",
    "manuscript": "manuscript",
    "computerProgram": "software",
    "blogPost": "post-weblog",
    "patent": "patent",
}


def _clean_text(value: str) -> str:
    """Strip HTML markup and decode entities from a Zotero field value."""
    return html.unescape(_HTML_TAG_RE.sub("", value)).strip()


def _item_year(item: Item) -> str | None:
    """Return the 4-digit year from Zotero's stored date string, if any."""
    if not item.date:
        return None
    match = _YEAR_RE.search(item.date)
    return match.group(1) if match else item.date


def _item_date_parts(item: Item) -> list[list[int]] | None:
    """Convert Zotero's stored date string into CSL date-parts."""
    if not item.date:
        return None
    match = _DATE_PARTS_RE.match(item.date.strip())
    if match is None:
        return None
    parts = [int(match.group(1))]
    for group in (match.group(2), match.group(3)):
        if group is None:
            continue
        value = int(group)
        if value <= 0:
            break
        parts.append(value)
    return [parts]


class CitationExportMixin(ABC):
    """BibTeX/CSL-JSON/RIS rendering for a reader that can load items."""

    @abstractmethod
    def get_item(self, key: str) -> Item | None:
        """Return the hydrated item for *key* (implemented by the concrete reader)."""

    def export_citation(self, key: str, fmt: str = "bibtex") -> str | None:
        item = self.get_item(key)
        if item is None:
            return None
        if fmt == "bibtex":
            return self._to_bibtex(item)
        if fmt in ("csl", "csl-json", "json"):
            return self._to_csl_json(item)
        if fmt == "ris":
            return self._to_ris(item)
        return None

    @staticmethod
    def _escape_bibtex(value: str) -> str:
        """Escape special LaTeX/BibTeX characters in a field value.

        A single regex pass is used so the braces inserted for a backslash
        escape are not escaped again on a later loop iteration.
        """
        return _BIBTEX_ESCAPE_RE.sub(lambda match: _BIBTEX_ESCAPES[match.group(1)], value)

    @staticmethod
    def _to_bibtex(item: Item) -> str:
        bib_type = _BIBTEX_TYPES.get(item.item_type, "misc")
        cite_key = item.key.lower()
        esc = CitationExportMixin._escape_bibtex
        authors = " and ".join(
            f"{esc(_clean_text(c.last_name))}, {esc(_clean_text(c.first_name))}"
            for c in item.creators
            if c.creator_type == "author"
        )
        lines = [f"@{bib_type}{{{cite_key},"]
        title = _clean_text(item.title)
        if title:
            lines.append(f"  title = {{{esc(title)}}},")
        if authors:
            lines.append(f"  author = {{{authors}}},")
        year = _item_year(item)
        if year:
            lines.append(f"  year = {{{esc(year)}}},")
        if item.doi:
            lines.append(f"  doi = {{{esc(item.doi)}}},")
        if item.url:
            lines.append(f"  url = {{{esc(item.url)}}},")
        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _to_csl_json(item: Item) -> str:
        """Convert an Item to CSL-JSON format (single item, not array)."""
        csl: dict = {
            "id": item.key,
            "type": _CSL_TYPES.get(item.item_type, "article"),
            "title": _clean_text(item.title),
        }
        authors = [
            {"family": _clean_text(c.last_name), "given": _clean_text(c.first_name)}
            for c in item.creators
            if c.creator_type == "author"
        ]
        if authors:
            csl["author"] = authors
        date_parts = _item_date_parts(item)
        if date_parts:
            csl["issued"] = {"date-parts": date_parts}
        if item.abstract:
            csl["abstract"] = _clean_text(item.abstract)
        if item.doi:
            csl["DOI"] = item.doi
        if item.url:
            csl["URL"] = item.url
        return json.dumps(csl, indent=2, ensure_ascii=False)

    @staticmethod
    def _to_ris(item: Item) -> str:
        """Convert an Item to RIS format."""
        lines = [f"TY  - {_RIS_TYPES.get(item.item_type, 'GEN')}"]
        title = _clean_text(item.title)
        if title:
            lines.append(f"TI  - {title}")
        for c in item.creators:
            if c.creator_type == "author":
                lines.append(f"AU  - {_clean_text(c.last_name)}, {_clean_text(c.first_name)}")
        year = _item_year(item)
        if year:
            lines.append(f"PY  - {year}")
        if item.abstract:
            lines.append(f"AB  - {_clean_text(item.abstract)}")
        if item.doi:
            lines.append(f"DO  - {item.doi}")
        if item.url:
            lines.append(f"UR  - {item.url}")
        for tag in item.tags:
            lines.append(f"KW  - {_clean_text(tag)}")
        lines.append("ER  - ")
        return "\n".join(lines)

    @staticmethod
    def _html_to_markdown(html_value: str) -> str:
        from markdownify import markdownify as md

        return md(html_value, strip=["img"]).strip()
