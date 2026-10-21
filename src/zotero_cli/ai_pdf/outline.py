"""MinerU Markdown 产物的标题大纲解析。

解析只依赖 Markdown 文本本身，不涉及命令参数或终端输出，
因此放在领域包内，供命令层与后续的笔记生成流程共用。
"""

from __future__ import annotations

import re

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def parse_markdown_outline(markdown: str) -> list[tuple[int, str, int]]:
    """按文档顺序返回 `(序号, 标题, 层级)` 三元组列表。"""
    return [
        (index, match.group(2).strip(), len(match.group(1)))
        for index, match in enumerate(_HEADING_PATTERN.finditer(markdown), 1)
    ]


def extract_markdown_section(markdown: str, section_number: int) -> str:
    """取出第 N 个标题所辖的区段，直到遇到同级或更高级标题为止。

    编号越界时返回空串，让调用方把「无此区段」当作正常的空结果处理。
    """
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
