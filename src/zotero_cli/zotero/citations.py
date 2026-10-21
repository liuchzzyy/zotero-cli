"""条目记录的引用导出格式化。

查询与连接相关逻辑保留在 zotero_cli.zotero.reader；本模块只负责把已经
完整加载的 zotero_cli.zotero.models.Item 渲染为 BibTeX、CSL-JSON 或 RIS。
ZoteroReader 通过组合 CitationExportMixin 获得这些能力，
调用方仍继续使用 reader.export_citation()。
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

# Zotero 条目类型 -> BibTeX 条目类型。
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

# Zotero 条目类型 -> RIS 文献类型。
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

# Zotero 条目类型 -> CSL-JSON 条目类型。
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
    """去除 Zotero 字段值中的 HTML 标记并解码实体。"""
    return html.unescape(_HTML_TAG_RE.sub("", value)).strip()


def _item_year(item: Item) -> str | None:
    """从 Zotero 保存的日期字符串中返回 4 位年份（若存在）。"""
    if not item.date:
        return None
    match = _YEAR_RE.search(item.date)
    return match.group(1) if match else item.date


def _item_date_parts(item: Item) -> list[list[int]] | None:
    """把 Zotero 保存的日期字符串转换为 CSL date-parts。"""
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
    """为能够加载条目的读取器提供 BibTeX/CSL-JSON/RIS 渲染能力。"""

    @abstractmethod
    def get_item(self, key: str) -> Item | None:
        """返回 *key* 对应的完整条目（由具体读取器实现）。"""

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
        """转义字段值中的 LaTeX/BibTeX 特殊字符。

        这里只进行单次正则替换，避免为反斜杠转义插入的花括号在后续循环中
        被再次转义。
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
        """把 Item 转换为 CSL-JSON 格式（单个条目，而非数组）。"""
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
        """把 Item 转换为 RIS 格式。"""
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
