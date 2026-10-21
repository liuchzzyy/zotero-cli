"""发送给分析模型的解析文档载荷辅助函数。

重复导入的条目常常携带同一份 PDF 两次，而 prompt 需要中立的
作者/期刊上下文；这两种处理都不依赖任何 AI 客户端。
"""

from __future__ import annotations

from zotero_cli.zotero.models import Item


def _item_authors(item: Item) -> str:
    return ", ".join(c.full_name for c in item.creators)


def _item_journal(item: Item) -> str:
    return str(item.extra.get("publicationTitle") or item.extra.get("journalAbbreviation") or "")


def _kept_duplicate_indices(parts: list[str]) -> list[int]:
    """去除重复副本后，返回应保留的各部分下标。

    重新导入的条目常携带同一份 PDF 两次，会把论文重复放进 prompt。
    当一部分的长行中至少有 90% 也出现在另一部分时，即判定两者相同；
    较长的一部分胜出，等长时取靠前者。保留下来的部分维持原有顺序，
    因为正文必须始终排在附录之前。
    """
    if len(parts) < 2:
        return list(range(len(parts)))

    def lines(text: str) -> set[str]:
        return {line.strip() for line in text.splitlines() if len(line.strip()) >= 60}

    sigs = [lines(text) for text in parts]

    def repeated(index: int) -> bool:
        return any(
            other != index
            and (len(parts[other]), -other) > (len(parts[index]), -index)
            and sigs[index]
            and len(sigs[index] & sigs[other]) >= 0.9 * len(sigs[index])
            for other in range(len(parts))
        )

    return [index for index in range(len(parts)) if not repeated(index)]


def _drop_duplicate_parts(parts: list[str]) -> list[str]:
    """丢弃同一文档的重复副本，保留最长的一份。"""
    return [parts[index] for index in _kept_duplicate_indices(parts)]


def _sections_to_text(sections: list[object]) -> str:
    """把 sections JSON 载荷展平为紧凑文本，用于关键词提取。

    模型输出不可信，因此跳过结构异常的条目（null 块、字段类型错误），
    而不是中止关键词生成。
    """
    lines: list[str] = []
    for block in sections or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "heading":
            lines.append("## " + str(block.get("text", "")))
        elif btype == "paragraph":
            lines.append(str(block.get("text", "")))
        elif btype == "bullet_list":
            items = block.get("items") or []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict):
                    lines.append("- " + str(item.get("text", "")))
        elif btype == "table":
            headers = block.get("headers") or []
            if isinstance(headers, list) and headers:
                lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            rows = block.get("rows") or []
            for row in rows if isinstance(rows, list) else []:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)
