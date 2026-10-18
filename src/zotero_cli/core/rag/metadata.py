"""Text normalization and paper-kind metadata helpers for the RAG pipeline."""

from __future__ import annotations

import re

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
    "abstract",
    "introduction",
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
    lead = f"{filename}\n{text[:500]}".lower()
    if (
        any(line.strip(" #\t\r.:") in _SUPPLEMENTARY_HINTS[:6] for line in lead.splitlines())
        and "cite this:" not in lead
    ):
        return "supplementary"
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
