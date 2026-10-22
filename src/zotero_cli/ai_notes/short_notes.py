"""生成、规范化并校验紧凑的 Extra short-note 关键词。

模型以管道分隔的 short-note 字符串作答。先做规范化（常见标点与换行异常），
再为 Zotero 展平（上下标与中英文间距），最后针对即将写入的确切值校验长度
与分段契约，因此补入的填充字符也计入文档所述的 500 字符预算。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.constants import (
    NOTE_TITLE_PREFIX,
    PARSE_RETRY_SUFFIX,
)
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.generation import _chat, extract_json_object, wrap_untrusted
from zotero_cli.ai_notes.persistence import mark_short_note_failure, save_backfilled_short_note
from zotero_cli.ai_notes.templates import (
    fit_text_to_prompt,
    format_template,
    load_template,
)
from zotero_cli.ai_notes.text import prepare
from zotero_cli.pdf_rag import clean_html
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter
from zotero_cli.zotero.models import Item

_SHORT_NOTE_MAX_CHARS = 500
_SHORT_NOTE_MIN_SEGMENTS = 3
# 分段符两侧的空白，用于把 `Zn || MnO2` 还原成 `Zn||MnO2` 后再切分。
_PIPE_GAP = re.compile(r"[ \t]*\|[ \t]*")
# 双竖线在切分前的占位符：`Zn||MnO2` 是电池记法，不是两个空段。
_LITERAL_PIPE_PAIR = "\x00"


def normalize_short_note(text: str) -> str:
    """规范化模型输出中的常见异常（首尾管道符、冒号、换行）。"""
    val = (text or "").strip()
    val = val.strip("|").strip()
    val = val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # `Zn||MnO2` 这类电池记法中的双竖线是字面量，不是分段符。先去掉竖线两侧空格，
    # 把 `Zn || MnO2` 也还原成 `Zn||MnO2`，再用占位符保护，避免被切成空段。
    val = _PIPE_GAP.sub("|", val).replace("||", _LITERAL_PIPE_PAIR)
    parts = [part.strip().replace(_LITERAL_PIPE_PAIR, "||") for part in val.split("|")]
    if parts:
        last = parts[-1]
        if last.startswith("疑问:") and not last.startswith("疑问："):
            parts[-1] = "疑问：" + last[3:].lstrip()
    return " | ".join(parts)


def validate_short_note(text: str) -> str | None:
    """short-note 不合法时返回错误信息，否则返回 None。"""
    value = (text or "").strip()
    if not value:
        return "empty short_note"
    if len(value) > _SHORT_NOTE_MAX_CHARS:
        return f"too long ({len(value)} > {_SHORT_NOTE_MAX_CHARS})"
    if "\n" in value or "\r" in value:
        return "contains newline"
    if any(ch in value for ch in "[]" + chr(96)):
        return "contains forbidden bracket/backtick"
    if value.startswith("|") or value.endswith("|"):
        return "leading/trailing pipe"
    # 与 normalize_short_note 一致：双竖线是电池记法的字面量，切分时不能算作空段。
    parts = [part.strip() for part in value.replace("||", _LITERAL_PIPE_PAIR).split("|")]
    if any(not part for part in parts):
        return "empty pipe segment"
    if len(parts) < _SHORT_NOTE_MIN_SEGMENTS:
        return f"fewer than {_SHORT_NOTE_MIN_SEGMENTS} segments"
    if not parts[-1].startswith("疑问："):
        return "last segment must start with 疑问："
    return None


def _generate_short_note(
    ai_client: AiClient,
    note_text: str,
    progress: Callable[[str, str], None] | None,
) -> str:
    template = load_template("short-note")
    source_text = fit_text_to_prompt(template, note_text)
    prompt = format_template(template, fulltext=wrap_untrusted(source_text, "ai-note"))
    if progress:
        progress("keywords", "生成关键词（short-note）")
    answer = _chat(ai_client, prompt, temperature=0.1, progress=progress)
    try:
        data = extract_json_object(answer)
    except NoteAnalysisError:
        if progress:
            progress("retry", "关键词输出无法解析为 JSON，重试一次")
        data = extract_json_object(_chat(ai_client, prompt + PARSE_RETRY_SUFFIX, temperature=0.1, progress=progress))
    short_note = str(data.get("short_note") or "").strip()
    normalized = normalize_short_note(short_note)
    # 填充会增加字符，因此校验实际写入 Extra 的确切值。
    padded = prepare(normalized)
    error = validate_short_note(padded)
    if error:
        raise NoteAnalysisError(f"关键词格式不合规：{error}", code="runtime_error")
    return padded


def _require_ai_client(ai_client: AiClient | None) -> AiClient:
    if ai_client is None:
        raise NoteAnalysisError("AI note 配置不完整", code="configuration_error")
    return ai_client


def _analyze_short_note_only(
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    ai_client: AiClient | None,
    item: Item,
    key: str,
    progress: Callable[[str, str], None] | None,
    *,
    dry_run: bool = False,
) -> dict:
    """回填模式：从已有 AI note 生成关键词，并写入 Extra short-note。"""
    notes = reader.get_notes(key)
    if not notes:
        raise NoteAnalysisError(f"条目 '{key}' 没有可用的 AI 笔记", code="validation_error")
    # reader.get_notes 按创建顺序返回；标题前缀命中 AI 条目的笔记中选最新一条（我们的工作流总是后创建），
    # 避免旧版工作流的同名笔记（可能更长）抢走选择。
    ai_notes = [n for n in notes if NOTE_TITLE_PREFIX in (n.content or "")]
    if ai_notes:
        chosen = ai_notes[-1]
    else:
        chosen = max(notes, key=lambda n: len(n.content or ""))
    note_text = clean_html((chosen.content or "").strip())
    if not note_text:
        raise NoteAnalysisError(f"条目 '{key}' 的 AI 笔记内容为空", code="validation_error")

    template = load_template("short-note")
    source_text = fit_text_to_prompt(template, note_text)
    result: dict = {
        "status": "short_note_only",
        "item_key": key,
        "item_type": item.item_type,
        "note_key": chosen.key,
    }
    if dry_run:
        prompt = format_template(template, fulltext=wrap_untrusted(source_text, "ai-note"))
        return {
            **result,
            "status": "dry_run",
            "template": "short-note",
            "chars": len(source_text),
            "prompt_preview": prompt[:1200],
        }
    if writer is None:
        raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
    committed = False
    try:
        short_note = _generate_short_note(_require_ai_client(ai_client), note_text, progress)
        save_backfilled_short_note(writer, key, short_note)
        committed = True
        result["short_note"] = "ok"
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, NoteAnalysisError)
            else NoteAnalysisError(
                f"生成关键词失败：{type(exc).__name__}: {exc}", code="runtime_error", committed=committed
            )
        )
        error.committed = error.committed or committed
        if error.committed:
            raise error from exc
        # 生成在任何写入之前失败：尽力打上 ai/no_keywords tag。
        result["short_note"] = "failed"
        result["short_note_error"] = mark_short_note_failure(writer, key, error)
    return result
