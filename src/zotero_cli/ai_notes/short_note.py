"""生成、规范化并校验紧凑的 Extra short-note 关键词。

模型以管道分隔的 short-note 字符串作答。先做规范化（常见标点与换行异常），
再为 Zotero 展平（上下标与中英文间距），最后针对即将写入的确切值校验长度
与分段契约，因此补入的填充字符也计入文档所述的 500 字符预算。
"""

from __future__ import annotations

from collections.abc import Callable

from zotero_cli.ai_notes.classification import _chat, extract_json_object, wrap_untrusted
from zotero_cli.ai_notes.client import AiClient
from zotero_cli.ai_notes.constants import PARSE_RETRY_SUFFIX
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.templates import fit_text_to_prompt, format_template, load_template
from zotero_cli.ai_notes.text import prepare

_SHORT_NOTE_MAX_CHARS = 500
_SHORT_NOTE_MIN_SEGMENTS = 3


def normalize_short_note(text: str) -> str:
    """规范化模型输出中的常见异常（首尾管道符、冒号、换行）。"""
    val = (text or "").strip()
    val = val.strip("|").strip()
    val = val.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    parts = [part.strip() for part in val.split("|")]
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
    parts = [part.strip() for part in value.split("|")]
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
