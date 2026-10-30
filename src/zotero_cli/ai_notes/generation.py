"""笔记提示词字段处理、模型生成与格式修正重试。"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime

from zotero_cli.ai_notes import constants as note_constants
from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.constants import COMPACT_RETRY_SUFFIX, PARSE_RETRY_SUFFIX, SPARSE_RETRY_SUFFIX
from zotero_cli.ai_notes.documents import _item_authors, _item_journal
from zotero_cli.ai_notes.errors import NoteAnalysisError, OutputTruncatedError
from zotero_cli.ai_notes.evidence import model_supports_request, output_ceiling
from zotero_cli.ai_notes.templates import format_template
from zotero_cli.config import AiNoteConfig
from zotero_cli.config.paths import state_dir
from zotero_cli.zotero.models import Item

_MISSING_FIELD = "未知"
logger = logging.getLogger(__name__)


def _wrap_field(value: str, label: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return wrap_untrusted(text or _MISSING_FIELD, label)


def _retry_models(
    prompt: str, figures: list[tuple[int, str]], allowed_models: set[str], max_tokens: int = 0
) -> set[str]:
    """重试文本变化后，只保留原已验证且仍容得下同组图片的路由。"""
    return {
        model for model in allowed_models if model_supports_request(model, prompt, len(figures), max_tokens=max_tokens)
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
    """统一处理首次、格式重试及截断后的压缩请求。"""
    truncated_count = 0
    compact_used = False
    repair = {"repaired": False, "salvaged": False}

    def parse_sections(answer: str, *, finish_reason: str = "") -> list[object]:
        parsed = extract_json_object(
            answer,
            item_key=str(getattr(client, "item_key", "")),
            stage="visual",
            model=str(getattr(client, "last_model", "")),
            finish_reason=finish_reason or str(getattr(client, "last_finish_reason", "")),
        )
        repair.update(parsed.get("_repair", {}))
        blocks = parsed.get("sections", [])
        return blocks if isinstance(blocks, list) else []

    def chat_and_parse(request_prompt: str, models: set[str]) -> list[object]:
        nonlocal truncated_count, compact_used
        try:
            answer = _chat(
                client,
                request_prompt,
                progress=progress,
                figures=figures,
                allowed_models=models,
                require_images=bool(figures),
            )
            return parse_sections(answer)
        except OutputTruncatedError as error:
            truncated_count += 1
            # 服务端截断时仅接受足量的完整 block；其余路径统一压缩重试。
            if error.response_text:
                try:
                    return parse_sections(error.response_text, finish_reason="length")
                except OutputTruncatedError:
                    pass
            if truncated_count >= 2 or compact_used:
                raise NoteAnalysisError(
                    "输出超过模型上限，压缩重试后仍被截断；未写入任何笔记",
                    code="output_truncated",
                    parkable=True,
                ) from error
            compact_used = True
            return compact_retry()

    def compact_retry() -> list[object]:
        compact_prompt, limit = compact_retry_prompt(prompt, client.config)
        if progress:
            progress("retry", f"输出达到 {limit} token 上限被截断，要求压缩后重试一次")
        compact_models = _retry_models(compact_prompt, figures, allowed_models, client.config.max_tokens)
        if not compact_models:
            raise NoteAnalysisError("输出压缩重试超过已验证的模型预算", code="validation_error", parkable=True)
        return chat_and_parse(compact_prompt, compact_models)

    try:
        sections = chat_and_parse(prompt, allowed_models)
    except NoteAnalysisError as error:
        if error.code == "output_truncated":
            raise
        if progress:
            progress("retry", "AI 输出无法解析为 JSON，追加格式要求重试一次")
        # 格式重试必须使用同一组图片，视觉成稿不允许纯文本降级。
        retry_prompt = prompt + PARSE_RETRY_SUFFIX
        retry_models = _retry_models(retry_prompt, figures, allowed_models, client.config.max_tokens)
        if not retry_models:
            raise NoteAnalysisError("输出格式重试超过已验证的模型预算", code="validation_error", parkable=True)
        try:
            sections = chat_and_parse(retry_prompt, retry_models)
        except NoteAnalysisError as retry_error:
            raise NoteAnalysisError(
                f"AI 输出两次都无法解析为 JSON（{retry_error}）",
                code=retry_error.code,
                parkable=retry_error.parkable,
            ) from retry_error
    setattr(client, "last_repair", repair)
    if len(sections) < note_constants.MIN_SECTIONS:
        if progress:
            progress("retry", f"输出过简略（{len(sections)} 个 block），追加内容充实度要求重试一次")
        retry_prompt = prompt + SPARSE_RETRY_SUFFIX
        retry_models = _retry_models(retry_prompt, figures, allowed_models, client.config.max_tokens)
        if retry_models:
            try:
                retried = chat_and_parse(retry_prompt, retry_models)
                if isinstance(retried, list) and len(retried) > len(sections):
                    sections = retried
            except NoteAnalysisError as error:
                if error.code == "output_truncated":
                    raise
    setattr(client, "last_repair", repair)
    return sections


def build_analysis_prompt(item: Item, template: str, fulltext: str) -> str:
    """以不可信数据边界包裹元数据和正文，构造完整生成提示词。"""
    wrapped_fulltext = wrap_untrusted(fulltext, "document")
    return format_template(
        template,
        title=_wrap_field(item.title, "title", note_constants.MAX_TITLE_CHARS),
        authors=_wrap_field(_item_authors(item), "authors", note_constants.MAX_AUTHORS_CHARS),
        journal=_wrap_field(_item_journal(item), "journal", note_constants.MAX_JOURNAL_CHARS),
        date=_wrap_field(item.date or "", "date", note_constants.MAX_DATE_CHARS),
        doi=_wrap_field(item.doi or "", "doi", note_constants.MAX_DOI_CHARS),
        fulltext=wrapped_fulltext,
    )


def _repair_json(candidate: str) -> str:
    """只在字符串内修复非法反斜杠与控制字符，避免改动结构字符。"""
    candidate = candidate.translate({ord("“"): '"', ord("”"): '"', ord("„"): '"'})
    candidate = re.sub(
        r'("(?:\\.|[^"\\])*")|,\s*([}\]])',
        lambda match: match.group(1) if match.group(1) is not None else match.group(2),
        candidate,
    )
    output: list[str] = []
    inside = False
    index = 0
    while index < len(candidate):
        char = candidate[index]
        if (
            inside
            and char == "\\"
            and any(
                candidate.startswith(command, index + 1)
                for command in (
                    "times",
                    "text",
                    "mathrm",
                    "mathbf",
                    "frac",
                    "alpha",
                    "beta",
                    "gamma",
                    "delta",
                    "sigma",
                    "omega",
                    "lambda",
                    "cdot",
                )
            )
        ):
            output.append("\\\\")
            index += 1
            continue
        if inside and char == "\\" and index + 1 < len(candidate) and candidate[index + 1] in '"\\/bfnrtu':
            output.extend((char, candidate[index + 1]))
            index += 2
            continue
        if char == '"':
            if inside:
                tail = candidate[index + 1 :].lstrip()
                if tail and tail[0] not in ",}]:":
                    output.append('\\"')
                    index += 1
                    continue
            inside = not inside
        if inside and char == "\\" and (index + 1 == len(candidate) or candidate[index + 1] not in '"\\/bfnrtu'):
            output.append("\\\\")
        elif inside and ord(char) < 32:
            output.append(json.dumps(char)[1:-1])
            index += 1
            continue
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _salvage_json(candidate: str) -> tuple[dict | None, int]:
    """仅保留已闭合的顶层数组对象，截断尾部绝不作为完整笔记直接写入。"""
    match = re.search(r'"(sections|evidence)"\s*:\s*\[', candidate)
    if match is None:
        return None, 0
    kind = match.group(1)
    start = match.end()
    depth = 0
    inside = False
    escaped = False
    ends: list[int] = []
    for index in range(start, len(candidate)):
        char = candidate[index]
        if inside:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                inside = False
            continue
        if char == '"':
            inside = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                ends.append(index + 1)
    if not ends:
        return None, 0
    prefix = candidate[: ends[-1]] + "]}"
    try:
        parsed = json.loads(prefix)
    except json.JSONDecodeError:
        return None, 0
    blocks = parsed.get(kind) if isinstance(parsed, dict) else None
    if not isinstance(blocks, list):
        return None, 0
    if kind == "sections":
        # 仅将渲染器能生成实际内容的章节算作截断恢复量。
        blocks = [
            block
            for block in blocks
            if isinstance(block, dict)
            and (
                (block.get("type") in {"heading", "paragraph"} and bool(str(block.get("text", "")).strip()))
                or (block.get("type") == "bullet_list" and bool(block.get("items")))
                or (block.get("type") == "table" and bool(block.get("rows")))
            )
        ]
        parsed[kind] = blocks
    valid = sum(isinstance(block, dict) for block in blocks)
    if valid < (note_constants.MIN_SECTIONS if kind == "sections" else 1):
        return None, valid
    if kind == "evidence":
        parsed.setdefault("unresolved", [])
    return parsed, valid


def _write_parse_debug(text: str, *, item_key: str, stage: str, model: str, finish_reason: str, detail: str) -> None:
    """原始响应只写入被 Git 忽略的有界本地目录，便于定位长输出中的破损处。"""
    if os.environ.get("ZOT_AINOTE_DEBUG", "1").lower() in {"0", "false", "off"}:
        return
    debug_dir = state_dir().parent / "debug" / "ainote"
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", item_key or "unknown")
    safe_stage = re.sub(r"[^A-Za-z0-9_-]", "_", stage or "unknown")
    filename = f"{safe_key}-{safe_stage}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.txt"
    metadata = f"item_key={item_key}\nstage={stage}\nmodel={model}\nfinish_reason={finish_reason}\nresponse_length={len(text)}\nbraces={text.count('{')}/{text.count('}')}\njson_error={detail}\n\n"
    (debug_dir / filename).write_text(metadata + text, encoding="utf-8")
    for path in sorted(debug_dir.glob("*.txt"), key=lambda entry: entry.stat().st_mtime_ns, reverse=True)[20:]:
        path.unlink()


def extract_json_object(
    text: str, *, item_key: str = "", stage: str = "", model: str = "", finish_reason: str = ""
) -> dict:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1, flags=re.I)
    stripped = re.sub(r"\s*```\s*$", "", stripped, count=1)
    candidates: list[tuple[str, str]] = [("完整响应", stripped)]
    fenced_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text, flags=re.I)
    if fenced_match:
        candidates.insert(0, ("JSON 围栏", fenced_match.group(1)))
    # 围栏内部再出现反引号时，按平衡花括号提取，避免正则过早结束。
    for start in (index for index, char in enumerate(stripped) if char == "{"):
        before = stripped[:start].rstrip()
        if before and before[-1] in "[,:{":
            continue
        depth = 0
        inside = False
        escaped = False
        for end in range(start, len(stripped)):
            char = stripped[end]
            if inside:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    inside = False
            elif char == '"':
                inside = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append((f"花括号区间 {start}:{end + 1}", stripped[start : end + 1]))
                    break
    parse_error: json.JSONDecodeError | None = None
    last_name = "完整响应"
    last_candidate = stripped
    for repaired in (False, True):
        for name, candidate in candidates:
            target = _repair_json(candidate) if repaired else candidate
            for strict in (True, False):
                try:
                    parsed = json.loads(target, strict=strict)
                    if isinstance(parsed, dict):
                        if repaired or not strict:
                            parsed["_repair"] = {"repaired": True, "salvaged": False}
                        return parsed
                except json.JSONDecodeError as error:
                    parse_error, last_name, last_candidate = error, name, target
    salvaged, count = _salvage_json(_repair_json(stripped))
    if salvaged is not None:
        logger.warning("[salvage] %s %s 恢复 %s 个 block", item_key, stage, count)
        salvaged["_repair"] = {"repaired": True, "salvaged": True}
        return salvaged
    position = parse_error.pos if parse_error else 0
    window = last_candidate[max(0, position - 80) : position + 80].replace("\n", "\\n").replace("\r", "\\r")
    detail = (
        f"候选 {last_name}，{parse_error.msg if parse_error else '非 JSON 对象'}，解析位置 {position}，附近 {window!r}"
    )
    try:
        _write_parse_debug(
            text, item_key=item_key, stage=stage, model=model, finish_reason=finish_reason, detail=detail
        )
    except OSError:
        logger.warning("AI 输出调试文件写入失败")
    truncation = finish_reason == "length" or text.count("{") > text.count("}") or (stripped.count('"') % 2 == 1)
    message = f"无法从 AI 输出中解析出 JSON；响应长度 {len(text)} 字符，{detail}"
    if truncation:
        raise OutputTruncatedError(message + "，疑似截断", parkable=True)
    raise NoteAnalysisError(message)


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
