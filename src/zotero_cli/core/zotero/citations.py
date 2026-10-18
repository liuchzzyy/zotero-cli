"""Citation export formatting for item records.

Query and connection concerns stay in zotero_cli.core.zotero.reader; this
module only renders an already-hydrated zotero_cli.models.Item as BibTeX,
CSL-JSON, or RIS. ZoteroReader composes CitationExportMixin, so callers keep
using reader.export_citation().
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from zotero_cli.models import Item


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
        """Escape special LaTeX/BibTeX characters in a field value."""
        for char, escaped in (("&", r"\&"), ("%", r"\%"), ("#", r"\#"), ("_", r"\_")):
            value = value.replace(char, escaped)
        return value

    @staticmethod
    def _to_bibtex(item: Item) -> str:
        type_map = {"journalArticle": "article", "book": "book", "thesis": "phdthesis"}
        bib_type = type_map.get(item.item_type, "misc")
        cite_key = item.key.lower()
        esc = CitationExportMixin._escape_bibtex
        authors = " and ".join(
            f"{esc(c.last_name)}, {esc(c.first_name)}" for c in item.creators if c.creator_type == "author"
        )
        lines = [f"@{bib_type}{{{cite_key},"]
        if item.title:
            lines.append(f"  title = {{{esc(item.title)}}},")
        if authors:
            lines.append(f"  author = {{{authors}}},")
        if item.date:
            lines.append(f"  year = {{{item.date}}},")
        if item.doi:
            lines.append(f"  doi = {{{item.doi}}},")
        if item.url:
            lines.append(f"  url = {{{item.url}}},")
        lines.append("}")
        return "\n".join(lines)

    @staticmethod
    def _to_csl_json(item: Item) -> str:
        """Convert an Item to CSL-JSON format (single item, not array)."""
        type_map = {
            "journalArticle": "article-journal",
            "book": "book",
            "bookSection": "chapter",
            "conferencePaper": "paper-conference",
            "thesis": "thesis",
            "report": "report",
            "webpage": "webpage",
            "preprint": "article",
        }
        csl: dict = {
            "id": item.key,
            "type": type_map.get(item.item_type, "article"),
            "title": item.title,
        }
        if item.creators:
            csl["author"] = [
                {"family": c.last_name, "given": c.first_name} for c in item.creators if c.creator_type == "author"
            ]
        if item.date:
            csl["issued"] = {"raw": item.date}
        if item.abstract:
            csl["abstract"] = item.abstract
        if item.doi:
            csl["DOI"] = item.doi
        if item.url:
            csl["URL"] = item.url
        return json.dumps(csl, indent=2, ensure_ascii=False)

    @staticmethod
    def _to_ris(item: Item) -> str:
        """Convert an Item to RIS format."""
        type_map = {
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
        }
        lines = [f"TY  - {type_map.get(item.item_type, 'GEN')}"]
        if item.title:
            lines.append(f"TI  - {item.title}")
        for c in item.creators:
            if c.creator_type == "author":
                lines.append(f"AU  - {c.last_name}, {c.first_name}")
        if item.date:
            lines.append(f"PY  - {item.date}")
        if item.abstract:
            lines.append(f"AB  - {item.abstract}")
        if item.doi:
            lines.append(f"DO  - {item.doi}")
        if item.url:
            lines.append(f"UR  - {item.url}")
        for tag in item.tags:
            lines.append(f"KW  - {tag}")
        lines.append("ER  - ")
        return "\n".join(lines)

    @staticmethod
    def _html_to_markdown(html: str) -> str:
        from markdownify import markdownify as md

        return md(html, strip=["img"]).strip()
