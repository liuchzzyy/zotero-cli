"""MinerU Markdown 产物的标题大纲解析。

解析只依赖 Markdown 文本本身，不涉及命令参数或终端输出，
因此放在领域包内，供命令层与后续的笔记生成流程共用。
"""

from __future__ import annotations

import re

from zotero_cli.ai_pdf.errors import PdfSectionNotFoundError
from zotero_cli.ai_pdf.models import MinerUParseResult, PdfContent

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s<>]+", re.IGNORECASE)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def extract_doi_from_markdown(markdown: str) -> str | None:
    """从 MinerU Markdown 开头提取首个 DOI。"""
    match = _DOI_PATTERN.search(markdown[:30000])
    return match.group(0).rstrip(".,;:)]}>'\"") if match else None


def parse_markdown_outline(markdown: str) -> list[tuple[int, str, int]]:
    """按文档顺序返回 `(序号, 标题, 层级)` 三元组列表。"""
    return [
        (index, match.group(2).strip(), len(match.group(1)))
        for index, match in enumerate(_HEADING_PATTERN.finditer(markdown), 1)
    ]


def extract_markdown_section(markdown: str, section_number: int) -> str:
    """取出第 N 个标题所辖的区段，直到遇到同级或更高级标题为止。"""
    matches = list(_HEADING_PATTERN.finditer(markdown))
    if section_number < 1 or section_number > len(matches):
        return ""
    target = matches[section_number - 1]
    target_level = len(target.group(1))
    end = len(markdown)
    for match in matches[section_number:]:
        if len(match.group(1)) <= target_level:
            end = match.start()
            break
    return markdown[target.start() : end].strip()


def read_pdf_content(
    parsed: MinerUParseResult,
    *,
    outline: bool = False,
    section: int | None = None,
    structured: bool = False,
) -> PdfContent:
    """按一种内容模式读取解析包，并把区段缺失表达为领域错误。"""
    if structured:
        return PdfContent(content_list=parsed.content_list)
    markdown = parsed.markdown
    if section is not None:
        content = extract_markdown_section(markdown, section)
        if not content:
            raise PdfSectionNotFoundError(f"Section {section} not found")
        return PdfContent(markdown=content, section=section)
    if outline:
        headings = [
            {"number": number, "text": text, "level": level} for number, text, level in parse_markdown_outline(markdown)
        ]
        return PdfContent(outline=headings)
    return PdfContent(markdown=markdown)
