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

from zotero_cli.ai_notes.client import AiClient
from zotero_cli.ai_notes.constants import MAX_ABSTRACT_CHARS, MAX_TITLE_CHARS
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.templates import load_template
from zotero_cli.zotero.models import Item

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

    candidates: list[str] = []
    # 优先提取标准 ```json ... ``` 代码块中的 JSON 字符串
    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, re.IGNORECASE)
    if fenced_match:
        candidates.append(fenced_match.group(1).strip())

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end > start:
        candidates.append(s[start : end + 1])

    candidates.append(s)

    for candidate in candidates:
        for strict in (True, False):
            try:
                parsed = json.loads(candidate, strict=strict)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    raise NoteAnalysisError("无法从 AI 输出中解析出 JSON")


def _sanitize_untrusted(text: str) -> str:
    """Neutralize delimiter-lookalike tags so data cannot close the wrapper."""
    return re.sub(r"</?untrusted-", lambda match: "&lt;" + match.group(0)[1:], text, flags=re.IGNORECASE)


def wrap_untrusted(text: str, label: str) -> str:
    """Wrap untrusted text so prompt instructions can treat it as data only."""
    return f"<untrusted-{label}>\n{_sanitize_untrusted(text)}\n</untrusted-{label}>"


def _cap_metadata(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def build_classify_prompt(item: Item, sample_text: str) -> str:
    """Build the classify prompt with untrusted Zotero/PDF text delimited."""
    template = load_template("classify-item")
    title = _cap_metadata(item.title or "", MAX_TITLE_CHARS)
    abstract = _cap_metadata(item.abstract or "", MAX_ABSTRACT_CHARS)
    return (
        template
        + "\n\n## 待判定的条目\n\n"
        + "标题：\n"
        + wrap_untrusted(title, "title")
        + "\n\n摘要：\n"
        + wrap_untrusted(abstract, "abstract")
        + "\n\n正文开头：\n"
        + wrap_untrusted(sample_text, "document")
        + "\n"
    )


def _chat_error(exc: Exception) -> NoteAnalysisError:
    """Translate provider/transport failures into typed, retry-aware errors."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    name = type(exc).__name__.lower()
    message = f"AI 调用失败：{type(exc).__name__}: {exc}"
    if status in (401, 403) or "authentication" in name or "permissiondenied" in name:
        return NoteAnalysisError(message, code="auth_invalid", retryable=False)
    if status == 429 or "ratelimit" in name:
        return NoteAnalysisError(message, code="rate_limited", retryable=True)
    if isinstance(status, int) and 500 <= status < 600:
        return NoteAnalysisError(message, code="network_error", retryable=True)
    if isinstance(status, int) and 400 <= status < 500:
        return NoteAnalysisError(message, code="validation_error", retryable=False)
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return NoteAnalysisError(message, code="network_error", retryable=True)
    if any(hint in name for hint in ("connection", "connect", "timeout", "transport", "network")):
        return NoteAnalysisError(message, code="network_error", retryable=True)
    return NoteAnalysisError(message, code="runtime_error", retryable=False)


def _chat(
    ai_client: AiClient,
    prompt: str,
    *,
    temperature: float | None = None,
    progress: Callable[[str, str], None] | None = None,
    figures: list[tuple[int, str]] | None = None,
    allowed_models: set[str] | None = None,
) -> str:
    try:
        return ai_client.chat(
            prompt, temperature=temperature, progress=progress, figures=figures, allowed_models=allowed_models
        )
    except NoteAnalysisError:
        raise
    except Exception as e:
        raise _chat_error(e) from e


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
    prompt = build_classify_prompt(item, sample_text)
    answer = _chat(ai_client, prompt, progress=progress)
    try:
        data = extract_json_object(answer)
        return str(data.get("paper_type", "")).strip() or "uncertain"
    except NoteAnalysisError:
        return "uncertain"
