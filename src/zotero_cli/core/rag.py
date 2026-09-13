from __future__ import annotations

import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from zotero_cli.config import EmbeddingConfig
from zotero_cli.core.providers.gitee import GiteeEmbeddingProvider

_SUPPLEMENTARY_HINTS = (
    "electronic supplementary material",
    "supplementary material",
    "supplementary information",
    "supporting information",
    "supporting info",
    "supporting data",
    "figure s1",
    "table s1",
)

_MAIN_PAPER_HINTS = (
    "cite this:",
    "received ",
    "accepted ",
    "doi:",
    "broader context",
)

_SUPPLEMENTARY_LABEL_RE = re.compile(
    r"\b(Figure S\d+[A-Za-z]?|Table S\d+[A-Za-z]?|Scheme S\d+[A-Za-z]?)\b", re.IGNORECASE
)


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in text.lower().split():
        word = re.sub(r"[.,;:!?()\"'\[\]{}]+$", "", word)
        word = re.sub(r"^[.,;:!?()\"'\[\]{}]+", "", word)
        if word:
            tokens.append(word)
    return tokens


def build_metadata_chunk(title: str, authors: str, abstract: str | None, tags: list[str]) -> str:
    parts = [f"Title: {title}", f"Authors: {authors}"]
    if abstract:
        parts.append(f"Abstract: {abstract}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def infer_pdf_kind(text: str, filename: str = "") -> str:
    head = f"{filename}\n{text[:2500]}".lower()
    extended = f"{filename}\n{text[:10000]}".lower()
    if any(hint in head for hint in _MAIN_PAPER_HINTS):
        return "main"
    if "experimental section" in head:
        return "supplementary"
    if any(hint in extended for hint in _SUPPLEMENTARY_HINTS):
        return "supplementary"
    return "main"


def get_pdf_kind_from_source(source: str) -> str | None:
    if source.startswith("pdf:main:"):
        return "main"
    if source.startswith("pdf:supplementary:"):
        return "supplementary"
    return None


def filter_ranked_results_by_pdf_kind(
    results: list[tuple[int, float, dict]],
    pdf_kind: str | None,
) -> list[tuple[int, float, dict]]:
    if not pdf_kind or pdf_kind == "any":
        return results
    filtered: list[tuple[int, float, dict]] = []
    for cid, score, chunk in results:
        kind = get_pdf_kind_from_source(chunk.get("source", ""))
        if kind == pdf_kind:
            filtered.append((cid, score, chunk))
    return filtered


def clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<td[^>]*>(.*?)</td>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"<th[^>]*>(.*?)</th>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<tr[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</table>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<table[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&apos;", "'", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    sentences = re.split(r"(?<=[。！？])|(?<=[.?!])\s+", text)
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


def cascade_chunk(text: str, max_chars: int, overlap: int) -> list[str]:
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
    max_chars = max_tokens * 4
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
    blocks = _mineru_blocks(content_list)
    chunks: list[MinerUStructuredChunk] = []
    current_section = ""
    max_chars = max_tokens * 4

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


def reciprocal_rank_fusion(*rankings: list[tuple[int, float, dict]], k: int = 60) -> list[tuple[int, float, dict]]:
    return weighted_reciprocal_rank_fusion(rankings, weights=None, k=k)


def weighted_reciprocal_rank_fusion(
    rankings: Sequence[list[tuple[int, float, dict]]],
    *,
    weights: Sequence[float] | None = None,
    k: int = 60,
) -> list[tuple[int, float, dict]]:
    scores: dict[int, float] = {}
    chunk_map: dict[int, dict] = {}
    if weights is None:
        weights = [1.0] * len(rankings)
    for ranking, weight in zip(rankings, weights):
        for rank, (chunk_id, _score, chunk) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * (1.0 / (k + rank + 1))
            chunk_map[chunk_id] = chunk
    results = [(cid, score, chunk_map[cid]) for cid, score in scores.items()]
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def embed_texts(
    texts: list[str],
    config: EmbeddingConfig,
    progress_callback: Callable[[int, int], None] | None = None,
    *,
    input_type: str = "document",
) -> list[list[float]] | None:
    if not config.is_configured:
        return None
    provider = GiteeEmbeddingProvider(
        api_key=config.api_key,
        url=config.url,
        model=config.model,
        batch_size=config.batch_size,
    )
    try:
        return provider.embed(texts, progress_callback, input_type=input_type)
    except Exception as e:
        sys.stderr.write(
            f"\r{' ' * 60}\r"
            f"  [WARN] Embedding provider '{config.provider}' failed: "
            f"{type(e).__name__}: {e}. Falling back to BM25-only.\n"
        )
        return None
