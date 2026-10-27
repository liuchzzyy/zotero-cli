"""笔记提示词字段处理、模型生成与格式修正重试。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.constants import (
    COMPACT_RETRY_SUFFIX,
    MAX_AUTHORS_CHARS,
    MAX_DATE_CHARS,
    MAX_DOI_CHARS,
    MAX_JOURNAL_CHARS,
    MAX_TITLE_CHARS,
    MIN_SECTIONS,
    PARSE_RETRY_SUFFIX,
    SPARSE_RETRY_SUFFIX,
)
from zotero_cli.ai_notes.documents import _item_authors, _item_journal
from zotero_cli.ai_notes.errors import NoteAnalysisError, OutputTruncatedError
from zotero_cli.ai_notes.evidence import OUTPUT_RESERVE, model_supports_request, output_ceiling
from zotero_cli.ai_notes.templates import format_template
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.models import Item

_MISSING_FIELD = "未知"
_FENCE = chr(96) * 3


def _wrap_field(value: str, label: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return wrap_untrusted(text or _MISSING_FIELD, label)


def _retry_models(
    prompt: str, figures: list[tuple[int, str]], allowed_models: set[str], output_reserve: int = OUTPUT_RESERVE
) -> set[str]:
    """重试文本变化后，只保留原已验证且仍容得下同组图片的路由。"""
    return {
        model
        for model in allowed_models
        if model_supports_request(model, prompt, len(figures), output_reserve=output_reserve)
    }


def compact_retry_prompt(prompt: str, config: AiNoteConfig) -> tuple[str, int]:
    """按路由输出上限构造压缩重试提示词：显式配置优先，否则用代理报告的模型上限。"""
    limit = int(config.max_tokens) if config.max_tokens > 0 else output_ceiling(config.model)
    # 中文与 JSON 结构约每 token 1–1.5 字符；按上限的 60% 估算字符目标。
    return prompt + COMPACT_RETRY_SUFFIX.format(limit=limit, chars=int(limit * 0.6)), limit


def _chat_with_retry(
    client: AiClient,
    prompt: str,
    *,
    figures: list[tuple[int, str]],
    allowed_models: set[str],
    progress: Callable[[str, str], None] | None,
) -> list[object]:
    """执行模型对话并解析 sections JSON，针对格式与内容稀疏各做一轮重试。"""
    output_reserve = max(OUTPUT_RESERVE, client.config.max_tokens)
    try:
        answer = _chat(
            client,
            prompt,
            progress=progress,
            figures=figures,
            allowed_models=allowed_models,
            require_images=bool(figures),
        )
    except OutputTruncatedError:
        # 输出达到上限：换模型只会重复同一上限，改为要求更紧凑的 JSON 重试一次。
        compact_prompt, limit = compact_retry_prompt(prompt, client.config)
        if progress:
            progress("retry", f"输出达到 {limit} token 上限被截断，要求压缩后重试一次")
        compact_models = _retry_models(compact_prompt, figures, allowed_models, output_reserve)
        if not compact_models:
            raise NoteAnalysisError("输出压缩重试超过已验证的模型预算", code="validation_error", parkable=True)
        try:
            answer = _chat(
                client,
                compact_prompt,
                progress=progress,
                figures=figures,
                allowed_models=compact_models,
                require_images=bool(figures),
            )
        except OutputTruncatedError as error:
            raise NoteAnalysisError(
                f"输出超过模型上限（{limit} token），压缩重试后仍被截断；未写入任何笔记",
                code="output_truncated",
            ) from error
    try:
        sections = extract_json_object(answer).get("sections", [])
    except NoteAnalysisError:
        if progress:
            progress("retry", "AI 输出无法解析为 JSON，追加格式要求重试一次")
        # 格式重试必须使用同一组图片，视觉成稿不允许纯文本降级。
        retry_prompt = prompt + PARSE_RETRY_SUFFIX
        retry_models = _retry_models(retry_prompt, figures, allowed_models, output_reserve)
        if not retry_models:
            raise NoteAnalysisError("输出格式重试超过已验证的模型预算", code="validation_error", parkable=True)
        retry_answer = _chat(
            client,
            retry_prompt,
            progress=progress,
            figures=figures,
            allowed_models=retry_models,
            require_images=bool(figures),
        )
        try:
            sections = extract_json_object(retry_answer).get("sections", [])
        except NoteAnalysisError as retry_error:
            snippet = retry_answer[:300].replace("\n", " ")
            raise NoteAnalysisError(
                f"AI 输出两次都无法解析为 JSON（响应开头：{snippet}...）",
                code="runtime_error",
            ) from retry_error
    if not isinstance(sections, list):
        sections = []
    if len(sections) < MIN_SECTIONS:
        if progress:
            progress("retry", f"输出过简略（{len(sections)} 个 block），追加内容充实度要求重试一次")
        retry_prompt = prompt + SPARSE_RETRY_SUFFIX
        retry_models = _retry_models(retry_prompt, figures, allowed_models, output_reserve)
        if retry_models:
            try:
                retry_answer = _chat(
                    client,
                    retry_prompt,
                    progress=progress,
                    figures=figures,
                    allowed_models=retry_models,
                    require_images=bool(figures),
                )
                retried = extract_json_object(retry_answer).get("sections", [])
                if isinstance(retried, list) and len(retried) > len(sections):
                    sections = retried
            except NoteAnalysisError:
                pass
    return sections


def build_analysis_prompt(item: Item, template: str, fulltext: str) -> str:
    """以不可信数据边界包裹元数据和正文，构造完整生成提示词。"""
    wrapped_fulltext = wrap_untrusted(fulltext, "document")
    return format_template(
        template,
        title=_wrap_field(item.title, "title", MAX_TITLE_CHARS),
        authors=_wrap_field(_item_authors(item), "authors", MAX_AUTHORS_CHARS),
        journal=_wrap_field(_item_journal(item), "journal", MAX_JOURNAL_CHARS),
        date=_wrap_field(item.date or "", "date", MAX_DATE_CHARS),
        doi=_wrap_field(item.doi or "", "doi", MAX_DOI_CHARS),
        fulltext=wrapped_fulltext,
    )


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

    # 围栏与 JSON 同行且缺少结尾围栏时，上一步的按行剥离会清空正文；
    # 因此再从原始响应中取一次花括号区间。
    raw_start, raw_end = text.find("{"), text.rfind("}")
    if raw_start != -1 and raw_end > raw_start:
        candidates.append(text[raw_start : raw_end + 1])

    candidates.append(s)

    parse_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        for strict in (True, False):
            try:
                parsed = json.loads(candidate, strict=strict)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError as error:
                parse_error = error
    opened, closed = text.count("{"), text.count("}")
    detail = (
        f"；响应长度 {len(text)} 字符，解析位置 {parse_error.pos}" if parse_error else f"；响应长度 {len(text)} 字符"
    )
    if opened > closed:
        detail += f"，疑似截断（花括号 {opened} 个 '{{' / {closed} 个 '}}'；响应被服务端截断）"
    raise NoteAnalysisError(f"无法从 AI 输出中解析出 JSON{detail}")


def _sanitize_untrusted(text: str) -> str:
    """中和形似分隔符的标签，避免数据内容闭合包裹标记。"""
    return re.sub(r"</?untrusted-", lambda match: "&lt;" + match.group(0)[1:], text, flags=re.IGNORECASE)


def wrap_untrusted(text: str, label: str) -> str:
    """包裹不可信文本，使 prompt 指令只能将其视为数据。"""
    return f"<untrusted-{label}>\n{_sanitize_untrusted(text)}\n</untrusted-{label}>"


def _chat_error(exc: Exception) -> NoteAnalysisError:
    """把服务商/传输失败转换为带类型的可重试错误。"""
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
    require_images: bool = False,
) -> str:
    try:
        return ai_client.chat(
            prompt,
            temperature=temperature,
            progress=progress,
            figures=figures,
            allowed_models=allowed_models,
            require_images=require_images,
        )
    except NoteAnalysisError:
        raise
    except Exception as error:
        raise _chat_error(error) from error
