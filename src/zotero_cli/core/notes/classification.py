"""Classify a parsed item into the analysis template that fits it.

A Zotero book is identified by its item type without a model call; every other
item is judged from its title, abstract and the opening of the parsed main text.
This module also owns the chat wrapper and the model-output JSON extraction that
the rest of the pipeline shares.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from zotero_cli.core.notes.client import AiClient
from zotero_cli.core.notes.errors import NoteAnalysisError
from zotero_cli.core.notes.templates import load_template
from zotero_cli.models import Item

CLASSIFY_CHARS = 4000
BOOK_ITEM_TYPES = {"book", "bookSection"}
# Template selected for each classifier answer; anything else falls back to research_article.
TEMPLATE_BY_TYPE = {
    "research_article": "research-article",
    "review_article": "review-article",
    "book": "book",
}

_FENCE = chr(96) * 3  # markdown code fence (three backticks)


def extract_json_object(text: str) -> dict:
    s = text.strip()
    lines = s.split("\n")
    if lines and lines[0].lstrip().startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    s = "\n".join(lines).strip()

    candidates = [s]
    # 优先提取标准 ```json ... ``` 代码块中的 JSON 字符串
    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_match:
        candidates.insert(0, fenced_match.group(1).strip())

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        candidates.insert(0, s[start : end + 1])

    for candidate in candidates:
        for strict in (True, False):
            try:
                parsed = json.loads(candidate, strict=strict)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    raise NoteAnalysisError("无法从 AI 输出中解析出 JSON")


def _chat(
    ai_client: AiClient,
    prompt: str,
    *,
    temperature: float | None = None,
    progress: Callable[[str, str], None] | None = None,
    figures: list[tuple[int, str]] | None = None,
) -> str:
    try:
        return ai_client.chat(prompt, temperature=temperature, progress=progress, figures=figures)
    except NoteAnalysisError:
        raise
    except Exception as e:
        raise NoteAnalysisError(f"AI 调用失败：{type(e).__name__}: {e}", code="network_error", retryable=True) from e


def _classify(
    item: Item,
    sample_text: str,
    ai_client: AiClient,
    progress: Callable[[str, str], None] | None,
) -> str:
    if item.item_type in BOOK_ITEM_TYPES:
        return "book"
    if progress:
        progress("classify", f"item_type={item.item_type}")
    template = load_template("classify-item")
    prompt = (
        template
        + "\n\n## 待判定的条目\n\n"
        + f"标题：{item.title}\n"
        + f"摘要：{item.abstract or ''}\n"
        + f"正文开头：\n{sample_text}\n"
    )
    answer = _chat(ai_client, prompt, progress=progress)
    try:
        data = extract_json_object(answer)
        return str(data.get("paper_type", "")).strip() or "uncertain"
    except NoteAnalysisError:
        return "uncertain"
