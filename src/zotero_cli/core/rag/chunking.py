"""Text chunking for RAG: section-aware plain text and MinerU structured blocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from zotero_cli.core.rag.metadata import clean_html

_SUPPLEMENTARY_LABEL_RE = re.compile(
    r"\b(Figure S\d+[A-Za-z]?|Table S\d+[A-Za-z]?|Scheme S\d+[A-Za-z]?)\b", re.IGNORECASE
)


def _chunk_by_char(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap if overlap else max_chars
    result = []
    start = 0
    while start < len(text):
        end = start + max_chars if start + max_chars <= len(text) else len(text)
        result.append(text[start:end])
        start += step
    return result


def _chunk_by_word(text: str, max_chars: int, overlap: int) -> list[str]:
    words = text.split()
    result: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        word_len = len(word) + 1
        if word_len > max_chars:
            if current:
                result.append(" ".join(current))
                current = []
                current_len = 0
            result.extend(_chunk_by_char(word, max_chars, overlap))
        elif current_len + word_len > max_chars and current:
            result.append(" ".join(current))
            current = [word]
            current_len = word_len
        else:
            current.append(word)
            current_len += word_len
    if current:
        result.append(" ".join(current))
    return result


def _chunk_by_sentence(text: str, max_chars: int, overlap: int) -> list[str]:
    sentences = re.split(r"(?<=[。！？])|(?<=[.?!])(?=\s)", text)
    result: list[str] = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        sent_len = len(sent)
        if sent_len > max_chars:
            if current:
                result.append("".join(current))
                current = []
                current_len = 0
            result.extend(_chunk_by_word(sent, max_chars, overlap))
        elif current_len + sent_len > max_chars and current:
            result.append("".join(current))
            current = [sent]
            current_len = sent_len
        else:
            current.append(sent)
            current_len += sent_len
    if current:
        result.append("".join(current))
    return result


def _validate_chunk_parameters(max_chars: int, overlap: int) -> None:
    if type(max_chars) is not int or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")
    if type(overlap) is not int or not 0 <= overlap < max_chars:
        raise ValueError("overlap must be an integer with 0 <= overlap < max_chars")


def cascade_chunk(text: str, max_chars: int, overlap: int) -> list[str]:
    _validate_chunk_parameters(max_chars, overlap)
    if len(text) <= max_chars:
        return [text]
    return _chunk_by_sentence(text, max_chars, overlap)


def _normalize_heading(heading: str, section_text: str) -> str:
    normalized = heading.strip()
    if not normalized:
        return normalized
    if normalized.lower() != "abbreviation":
        return normalized
    match = _SUPPLEMENTARY_LABEL_RE.search(section_text)
    if match:
        return match.group(1)
    head = section_text[:1200].lower()
    if "experimental section" in head:
        return "Experimental Section"
    return normalized


def _split_supplementary_sections(heading: str, section_text: str) -> list[tuple[str, str]]:
    text = section_text.strip()
    if not text:
        return []
    matches = list(_SUPPLEMENTARY_LABEL_RE.finditer(text))
    if not matches:
        return [(heading, text)]

    sections: list[tuple[str, str]] = []
    first_match = matches[0]
    prelude = text[: first_match.start()].strip()
    if prelude:
        prelude_heading = _normalize_heading(heading, prelude)
        sections.append((prelude_heading, prelude))

    for i, match in enumerate(matches):
        label = match.group(1)
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            sections.append((label, block))
    return sections or [(heading, text)]


def chunk_text(text: str, paper_title: str, max_tokens: int = 500, overlap: int = 50) -> list[str]:
    """Chunk text with a positive token budget and a character-count overlap."""
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")
    max_chars = max_tokens * 4
    _validate_chunk_parameters(max_chars, overlap)
    text = clean_html(text)
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_text = ""
    for line in text.split("\n"):
        if re.match(r"^#{1,3}\s+", line):
            if current_text.strip():
                sections.append((current_heading, current_text.strip()))
            current_heading = re.sub(r"^#{1,3}\s+", "", line).strip()
            current_text = ""
        else:
            current_text += line + "\n"
    if current_text.strip():
        sections.append((current_heading, current_text.strip()))
    if not sections:
        sections = [("", text.strip())]

    expanded_sections: list[tuple[str, str]] = []
    for heading, section_text in sections:
        expanded_sections.extend(_split_supplementary_sections(heading, section_text))
    sections = expanded_sections or sections

    chunks: list[str] = []
    for heading, section_text in sections:
        heading = _normalize_heading(heading, section_text)
        prefix = f"[{paper_title} > {heading}] " if heading else f"[{paper_title}] "
        if len(section_text) <= max_chars:
            chunks.append(prefix + section_text)
        else:
            paragraphs = re.split(r"\n\n+", section_text)
            for para in paragraphs:
                if len(para) <= max_chars:
                    chunks.append(prefix + para)
                else:
                    sub_chunks = cascade_chunk(para, max_chars, overlap)
                    for sc in sub_chunks:
                        chunks.append(prefix + sc)
    return chunks if chunks else [f"[{paper_title}] {text.strip()}"]


@dataclass(frozen=True)
class MinerUStructuredChunk:
    """Searchable text derived from one or more MinerU structured blocks."""

    content: str
    page: int | None
    block_type: str
    section: str


def chunk_mineru_content(
    content_list: Any,
    paper_title: str,
    max_tokens: int = 500,
    overlap: int = 50,
) -> list[MinerUStructuredChunk]:
    """Build RAG chunks from MinerU JSON, preserving section, page, and block type."""
    if type(max_tokens) is not int or max_tokens <= 0:
        raise ValueError("max_tokens must be a positive integer")
    max_chars = max_tokens * 4
    _validate_chunk_parameters(max_chars, overlap)
    blocks = _mineru_blocks(content_list)
    chunks: list[MinerUStructuredChunk] = []
    current_section = ""

    for block in blocks:
        block_type = str(block.get("type") or block.get("block_type") or "text")
        if block_type in {"header", "footer", "page_number"}:
            continue
        text_level = block.get("text_level")
        text = _mineru_block_text(block).strip()
        if not text:
            continue
        if block_type in {"title", "heading"} or isinstance(text_level, int):
            current_section = text
            if block_type in {"title", "heading"}:
                continue

        raw_page = block.get("page_idx", block.get("page_index", block.get("page_no")))
        page: int | None = None
        if isinstance(raw_page, int):
            page = raw_page + 1 if "page_idx" in block or "page_index" in block else raw_page

        label_parts = [paper_title]
        if current_section:
            label_parts.append(current_section)
        details = []
        if page is not None:
            details.append(f"page {page}")
        details.append(block_type)
        prefix = f"[{' > '.join(label_parts)} | {', '.join(details)}] "
        for part in cascade_chunk(text, max_chars, overlap):
            chunks.append(
                MinerUStructuredChunk(
                    content=prefix + part,
                    page=page,
                    block_type=block_type,
                    section=current_section,
                )
            )
    return chunks


def _mineru_blocks(content_list: Any) -> list[dict[str, Any]]:
    if isinstance(content_list, list):
        return [item for item in content_list if isinstance(item, dict)]
    if isinstance(content_list, dict):
        for key in ("content_list", "content", "blocks", "data"):
            nested = content_list.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [content_list]
    return []


def _mineru_block_text(block: dict[str, Any]) -> str:
    values: list[str] = []
    for key in (
        "text",
        "title",
        "table_caption",
        "table_body",
        "table_footnote",
        "image_caption",
        "image_footnote",
        "equation",
        "latex",
    ):
        value = block.get(key)
        rendered = _render_mineru_value(value)
        if rendered:
            values.append(rendered)
    return "\n".join(dict.fromkeys(values))


def _render_mineru_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _render_mineru_value(item)))
    if isinstance(value, dict):
        return "\n".join(part for item in value.values() if (part := _render_mineru_value(item)))
    return ""
