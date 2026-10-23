"""发送给分析模型的解析文档载荷辅助函数。

重复导入的条目常常携带同一份 PDF 两次，而 prompt 需要中立的
作者/期刊上下文；这两种处理都不依赖任何 AI 客户端。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_pdf import MinerUError, MinerUParseCache, MinerUParseResult, MinerUTokenMissingError
from zotero_cli.ai_pdf.parsing import PdfParsingService
from zotero_cli.pdf_rag import PdfDocument, resolve_pdf_kinds
from zotero_cli.zotero.access.reader import ZoteroReader
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


def collect_documents(
    reader: ZoteroReader,
    key: str,
    progress: Callable[[str, str], None] | None = None,
) -> list[tuple[str, str, MinerUParseResult]]:
    """收集并解析附件，按正文和补充材料排序，再跨附件去重。"""
    attachments = reader.get_pdf_attachments(key)
    if not attachments:
        raise NoteAnalysisError(f"条目 '{key}' 没有本地 PDF 附件", code="validation_error")

    pdf_paths = [a.path for a in attachments if a.path is not None and a.path.exists()]
    if not pdf_paths:
        raise NoteAnalysisError(f"条目 '{key}' 的 PDF 附件文件不存在", code="validation_error")

    if progress:
        progress("mineru", f"解析/读取缓存：{len(pdf_paths)} PDF(s)")
    try:
        pdf_parses = PdfParsingService(cache=MinerUParseCache()).parse_many(pdf_paths)
    except MinerUError as exc:
        message = str(exc)
        code = "configuration_error" if isinstance(exc, MinerUTokenMissingError) else "network_error"
        raise NoteAnalysisError(message, code=code, retryable=code == "network_error") from exc

    parsed_pairs: list[tuple[Any, MinerUParseResult]] = []
    for att in attachments:
        if att.path is None:
            continue
        parsed = pdf_parses.get(att.path)
        if isinstance(parsed, MinerUParseResult):
            parsed_pairs.append((att, parsed))
    # 图注编号一次性决定整个条目的正文（MS）与支撑信息（SI）：SI 封面
    # 常重复文章标题和文件名，因此只有附件之间的比较才可靠（见 `resolve_pdf_kinds`）。
    kinds = resolve_pdf_kinds(
        [
            PdfDocument(att.key, att.filename or att.key, parsed.markdown, parsed.content_list)
            for att, parsed in parsed_pairs
        ]
    )
    structured_docs: list[tuple[str, str, MinerUParseResult]] = [
        (att.key, kinds[att.key], parsed) for att, parsed in parsed_pairs
    ]
    structured_docs.sort(key=lambda doc: doc[1] != "main")
    # 在整个条目范围内而不是按类别去重重复附件：同一份 PDF 可能被附加两次，
    # 不能因为其中一份被归为支撑信息就两份都保留。保留者维持自身类别。
    kept_indices = _kept_duplicate_indices([doc[2].markdown for doc in structured_docs])
    structured_docs = [structured_docs[index] for index in kept_indices]
    return structured_docs
