"""收集并冻结单次 ai-note2 的输入快照。

顺序固定为：先验证父条目与旧 AI 分析笔记，再验证附件与解析缓存。没有可用
旧笔记时不解析 PDF、不创建模型客户端、不写 Zotero。冻结后的内容只服务本次
运行，后续生成、校验和渲染不再读取可能被替换的缓存。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from zotero_cli.ai_notes.constants import NOTE_TITLE_PREFIX
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_pdf import MinerUError, MinerUParseCache, MinerUTokenMissingError
from zotero_cli.ai_pdf.access.cache import file_sha256
from zotero_cli.ai_pdf.parsing import PdfParsingService
from zotero_cli.pdf_rag import PdfDocument, resolve_pdf_kinds
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.models import Attachment, Item, Note

# 本功能生成的笔记标题前缀，用于避免把自己的输出递归当作旧分析输入。
NOTE2_TITLE_PREFIX = "框架分析 - "

# 首版只支持期刊论文；综述、书籍、会议论文等不套用该三链模板。
SUPPORTED_ITEM_TYPE = "journalArticle"

# 旧 ai-note 生成的标题前缀；只检查首个标题，不在全文搜索。
LEGACY_NOTE_TITLE_PREFIX = NOTE_TITLE_PREFIX

_FIGURE_RE = re.compile(r"(?:Figure|Fig\.?|图)\s*(S?\d+[A-Za-z]?)", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")


@dataclass(frozen=True)
class SourceNote:
    """选定的旧 AI 分析笔记；仅保存本地读取到的文本。"""

    key: str
    text: str
    digest: str
    title: str


@dataclass(frozen=True)
class InputSnapshot:
    """一次运行使用的不可变输入；不包含图片字节或缓存路径。"""

    item: Item
    library_ref: dict[str, str]
    source_note: SourceNote
    documents: list[dict[str, Any]]
    source_blocks: list[dict[str, Any]]
    projection: str
    character_count: int
    attachment_aliases: dict[str, list[str]] = field(default_factory=dict)

    def projection_payload(self) -> str:
        """返回发给模型的文本投影（保留块类型与顺序，无图片路径）。"""
        return self.projection


def first_heading(content: str) -> str:
    """返回 Markdown 或 HTML 笔记的首个标题文本。"""
    for line in (content or "").splitlines():
        match = _HEADING_RE.match(line)
        if match:
            return match.group(1).strip()
        text = line.strip()
        if not text:
            continue
        html_match = re.match(r"<h[1-6][^>]*>(.*?)</h[1-6]>", text, re.IGNORECASE)
        if html_match:
            return html.unescape(re.sub(r"<[^>]+>", "", html_match.group(1))).strip()
        # 首个非空行不是标题时，该笔记没有可识别的首标题。
        return ""
    return ""


def is_legacy_ai_note(content: str) -> bool:
    """仅当首个标题带旧 AI 分析前缀时认定为旧笔记。"""
    return first_heading(content).startswith(LEGACY_NOTE_TITLE_PREFIX)


def is_note2_output(content: str) -> bool:
    """判断是否为 ai-note2 自己的输出，避免递归输入。"""
    return first_heading(content).startswith(NOTE2_TITLE_PREFIX)


def _select_source_note(notes: list[Note], explicit_key: str | None) -> SourceNote:
    if explicit_key:
        selected = next((note for note in notes if note.key == explicit_key), None)
        if selected is None:
            raise AiNote2Error(
                f"注记 '{explicit_key}' 不属于条目或其不存在", code="validation_error", hint_key="source_note_invalid"
            )
        if not selected.content.strip():
            raise AiNote2Error("指定的 AI 分析笔记内容为空", code="validation_error", hint_key="source_note_invalid")
        if is_note2_output(selected.content):
            raise AiNote2Error(
                "指定的注记是本功能的输出，不能作为旧 AI 分析输入",
                code="validation_error",
                hint_key="source_note_invalid",
            )
        return SourceNote(
            key=selected.key,
            text=selected.content,
            digest=sha256(selected.content.encode("utf-8")).hexdigest(),
            title=first_heading(selected.content),
        )

    candidates = [note for note in notes if is_legacy_ai_note(note.content) and note.content.strip()]
    if not candidates:
        raise AiNote2Error("no_ai_note", code="not_found", hint_key="no_ai_note")
    if len(candidates) > 1:
        keys = ", ".join(note.key for note in candidates)
        raise AiNote2Error(
            f"存在多份可识别的旧 AI 分析笔记（{keys}），请用 --source-note 指定",
            code="conflict",
            hint_key="source_note_ambiguous",
        )
    note = candidates[0]
    return SourceNote(
        key=note.key,
        text=note.content,
        digest=sha256(note.content.encode("utf-8")).hexdigest(),
        title=first_heading(note.content),
    )


def _pdf_attachments(reader: ZoteroReader, key: str, requested: list[str]) -> list[Attachment]:
    attachments = [att for att in reader.get_attachments(key) if att.content_type == "application/pdf"]
    if not attachments:
        raise AiNote2Error(f"条目 '{key}' 没有 PDF 附件", code="validation_error", hint_key="pdf_missing")
    by_key = {att.key: att for att in attachments}
    if requested:
        unknown = [item for item in requested if item not in by_key]
        if unknown:
            raise AiNote2Error(
                f"指定附件不属于当前父条目或不是 PDF：{', '.join(unknown)}",
                code="validation_error",
                hint_key="attachment_invalid",
            )
        seen: set[str] = set()
        selected: list[Attachment] = []
        for item in requested:
            if item not in seen:
                seen.add(item)
                selected.append(by_key[item])
        attachments = selected
    missing = [att.key for att in attachments if att.path is None or not att.path.exists()]
    if missing:
        raise AiNote2Error(
            f"PDF 附件文件缺失，无法静默忽略：{', '.join(missing)}",
            code="validation_error",
            hint_key="pdf_missing",
        )
    return attachments


def _block_type(entry: dict[str, Any]) -> str:
    raw = str(entry.get("type") or "").lower()
    if raw == "table":
        return "table"
    if raw in {"equation", "interline_equation"}:
        return "equation"
    if raw == "image":
        return "caption"
    if raw in {"footnote", "page_footnote"}:
        return "footnote"
    if raw == "reference":
        return "reference"
    return "text"


def _join_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(part) for part in value if str(part).strip())
    return "" if value is None else str(value)


_BLOCK_LIMIT = 6_000


def _freeze_blocks(
    parsed: Any,
    *,
    source_set_id: str,
    attachment_key: str,
    pdf_role: str,
) -> list[dict[str, Any]]:
    content = parsed.content_list
    if not isinstance(content, list):
        raise AiNote2Error("MinerU content_list.json 结构无效", code="runtime_error", hint_key="parse_invalid")
    blocks: list[dict[str, Any]] = []
    section_path: list[str] = []
    for entry in content:
        if not isinstance(entry, dict):
            continue
        block_type = _block_type(entry)
        if block_type == "text" and entry.get("text_level"):
            heading = _join_text(entry.get("text")).strip()
            if heading:
                level = max(1, min(3, int(entry.get("text_level") or 1)))
                section_path = section_path[: level - 1] + [heading]
        if block_type == "caption":
            text = _join_text(entry.get("image_caption") or entry.get("img_caption"))
            if not text.strip():
                # 无图注的图片不进入请求，也不保留路径或像素。
                continue
        elif block_type == "table":
            caption = _join_text(entry.get("table_caption"))
            body = _join_text(entry.get("table_body"))
            notes = _join_text(entry.get("table_footnote"))
            text = "\n".join(part for part in (caption, body, notes) if part.strip())
            if not text.strip():
                continue
        else:
            text = _join_text(entry.get("text") or entry.get("latex") or entry.get("math"))
        text = text.strip()
        if not text:
            continue
        if len(text) > _BLOCK_LIMIT:
            text = text[:_BLOCK_LIMIT]
        page_idx = entry.get("page_idx")
        page = int(page_idx) + 1 if isinstance(page_idx, int) and page_idx >= 0 else None
        figure_ids = sorted({match.group(1) for match in _FIGURE_RE.finditer(text)})
        blocks.append(
            {
                "source_set_id": source_set_id,
                "attachment_key": attachment_key,
                "block_id": f"B{len(blocks) + 1:04d}",
                "block_type": block_type,
                "pdf_role": pdf_role,
                "page_pdf": page,
                "page_label": None,
                "section_path": list(section_path),
                "text": text,
                # 不臆造单元格关系：HTML 表格保留原文，结构关系留空。
                "table": None,
                "figure_ids": figure_ids,
            }
        )
    if not blocks:
        raise AiNote2Error("MinerU structured content 为空", code="runtime_error", hint_key="parse_invalid")
    return blocks


def _projection(blocks: list[dict[str, Any]], item: Item) -> str:
    payload = {
        "item": {"key": item.key, "title": item.title, "item_type": item.item_type, "date": item.date, "doi": item.doi},
        "note": "仅包含文本、表格文字、公式文字、图注和脚注；没有任何图片像素或路径。",
        "blocks": [
            {
                "source_set_id": block["source_set_id"],
                "attachment_key": block["attachment_key"],
                "block_id": block["block_id"],
                "block_type": block["block_type"],
                "pdf_role": block["pdf_role"],
                "page_pdf": block["page_pdf"],
                "section_path": block["section_path"],
                "text": block["text"],
                "figure_ids": block["figure_ids"],
            }
            for block in blocks
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def collect_inputs(
    reader: ZoteroReader,
    key: str,
    *,
    library_type: str = "user",
    group_id: str | None = None,
    source_note_key: str | None = None,
    attachment_keys: list[str] | None = None,
    max_extracted_chars: int = 0,
    progress: Any = None,
) -> InputSnapshot:
    """冻结本次输入；失败时抛出可操作错误，不静默丢弃附件或截断文本。"""
    item = reader.get_item(key)
    if item is None:
        raise AiNote2Error(f"条目 '{key}' 不存在", code="not_found")
    if item.item_type in {"note", "attachment", "annotation"}:
        raise AiNote2Error("必须传入父条目，而不是 note 或附件", code="validation_error", hint_key="not_parent_item")
    if item.item_type != SUPPORTED_ITEM_TYPE:
        raise AiNote2Error(
            f"ai-note2 只支持期刊论文（itemType=journalArticle）；当前条目类型为 {item.item_type}",
            code="validation_error",
            hint_key="unsupported_item_type",
        )

    notes = reader.get_notes(key)
    source_note = _select_source_note(notes, source_note_key)

    attachments = _pdf_attachments(reader, key, attachment_keys or [])
    if progress:
        progress("inputs", f"冻结输入：{len(attachments)} 个 PDF 附件")
    service = PdfParsingService(cache=MinerUParseCache())

    grouped: dict[str, list[Attachment]] = {}
    for att in attachments:
        assert att.path is not None
        digest = file_sha256(att.path)
        grouped.setdefault(digest, []).append(att)

    parsed_by_digest: dict[str, Any] = {}
    for digest, group in grouped.items():
        path = group[0].path
        assert path is not None
        try:
            parsed_by_digest[digest] = service.parse(path, progress_callback=lambda *_args: None)
        except MinerUError as exc:
            code = "configuration_error" if isinstance(exc, MinerUTokenMissingError) else "network_error"
            raise AiNote2Error(str(exc), code=code, retryable=code == "network_error", hint_key="parse_failed") from exc

    ordered = [(digest, group) for digest, group in grouped.items()]
    kinds = resolve_pdf_kinds(
        [
            PdfDocument(
                group[0].key,
                group[0].filename or group[0].key,
                parsed_by_digest[digest].markdown,
                parsed_by_digest[digest].content_list,
            )
            for digest, group in ordered
        ]
    )

    documents: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    aliases: dict[str, list[str]] = {}
    for index, (digest, group) in enumerate(ordered, 1):
        primary = group[0]
        role = "MS" if kinds.get(primary.key, "main") == "main" else "SI"
        basis = (
            "唯一 PDF，按正文处理；文献身份未独立核验"
            if len(ordered) == 1
            else "按题注与前置信息启发式判定；不确定时需复核"
        )
        aliases[primary.key] = [att.key for att in group[1:]]
        parsed = parsed_by_digest[digest]
        artifact = parsed.content_list_path.read_bytes()
        documents.append(
            {
                "source_set_id": f"set-{index}",
                "attachment_key": primary.key,
                "attachment_aliases": list(aliases[primary.key]),
                "pdf_sha256": digest,
                "pdf_role": role,
                "document_identity_status": "身份未明确",
                "role_basis": basis,
                "parse_metadata": {
                    "parser": "MinerU",
                    "parser_version": None,
                    "adapter_version": "ai-note2/1",
                    "parsed_at": None,
                    "parse_artifact_digest": sha256(artifact).hexdigest(),
                },
            }
        )
        blocks.extend(_freeze_blocks(parsed, source_set_id=f"set-{index}", attachment_key=primary.key, pdf_role=role))

    projection = _projection(blocks, item)
    characters = len(projection) + len(source_note.text)
    if max_extracted_chars > 0 and characters > max_extracted_chars:
        raise AiNote2Error(
            f"来源文字合计 {characters} 字符，超过 max_extracted_chars={max_extracted_chars}；不静默截断",
            code="validation_error",
            hint_key="input_too_large",
            parkable=True,
        )
    library_id = str(getattr(reader, "_library_id", 1))
    library_ref = {"library_type": library_type, "library_id": str(group_id) if group_id else library_id}
    return InputSnapshot(
        item=item,
        library_ref=library_ref,
        source_note=source_note,
        documents=documents,
        source_blocks=blocks,
        projection=projection,
        character_count=characters,
        attachment_aliases=aliases,
    )
