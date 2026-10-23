"""笔记、简记与标签写回，以及提交状态的错误转换。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zotero_cli.ai_notes.constants import ANALYZED_TAG, KEYWORDS_TAG, NO_KEYWORDS_TAG
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.zotero.access.writer import ZoteroWriter

_DEFINITE_REJECTION_CODES = frozenset(
    {"auth_invalid", "auth_missing", "validation_error", "not_found", "configuration_error", "confirmation_required"}
)


def _writer_call(action: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """执行 writer 变更，把原始失败转换为带类型的流水线错误。

    抛出的错误保留 writer 的错误码/可重试提示，并标记该变更为已提交，
    使结果可能已到达 Zotero 时不会释放幂等预占。
    """
    try:
        return callback(*args, **kwargs)
    except NoteAnalysisError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "runtime_error"))
        retryable = bool(getattr(exc, "retryable", False))
        raise NoteAnalysisError(
            f"{action}失败：{type(exc).__name__}: {exc}",
            code=code,
            retryable=retryable,
            committed=code not in _DEFINITE_REJECTION_CODES,
        ) from exc


def save_analysis_note(writer: ZoteroWriter, key: str, html_note: str, *, no_tag: bool) -> str:
    """创建分析笔记后打标；后续失败保留已写入状态。"""
    note_key = _writer_call("写入 note", writer.add_note, key, html_note)
    if not no_tag:
        try:
            _writer_call("写入 tag ai/noted", writer.add_tags, key, [ANALYZED_TAG])
        except NoteAnalysisError as tag_error:
            raise NoteAnalysisError(
                f"note 已写入（key {note_key}），但 ai/noted tag 写入失败：{tag_error}；"
                "请勿直接重跑以免生成重复 note，先手工补 tag",
                code=tag_error.code,
                retryable=False,
                committed=True,
            ) from tag_error

    return note_key


def save_short_note(writer: ZoteroWriter, key: str, short_note: str) -> tuple[str, str]:
    """写入简记后打标；标签失败时保留已写入状态并要求核对。"""
    _writer_call("写入 Extra short-note", writer.update_short_note, key, short_note)
    try:
        _writer_call("写入 tag ai/keywords", writer.add_tags, key, [KEYWORDS_TAG])
    except NoteAnalysisError as error:
        raise NoteAnalysisError(
            f"short-note 已写入（item {key}），但 ai/keywords tag 写入失败：{error}；请勿直接重跑，先核对远端 tag",
            code=error.code,
            retryable=False,
            committed=True,
        ) from error
    return "ok", ""


def mark_short_note_failure(writer: ZoteroWriter, key: str, error: NoteAnalysisError) -> str:
    """记录简记失败标签，返回包含标签失败信息的摘要。"""
    message = str(error)[:160]
    try:
        _writer_call("写入 tag ai/no_keywords", writer.add_tags, key, [NO_KEYWORDS_TAG])
    except NoteAnalysisError as tag_error:
        message = f"{message}; tag 写入失败：{tag_error}"[:160]
    return message


def save_backfilled_short_note(writer: ZoteroWriter, key: str, short_note: str) -> None:
    """回填简记后打标，失败时明确报告已经完成的写入。"""
    _writer_call("写入 Extra short-note", writer.update_short_note, key, short_note)
    try:
        _writer_call("写入 tag ai/keywords", writer.add_tags, key, [KEYWORDS_TAG])
    except NoteAnalysisError as error:
        raise NoteAnalysisError(
            f"short-note 已写入（item {key}），但 ai/keywords tag 写入失败：{error}；请勿直接重跑，先手工补 tag",
            code=error.code,
            retryable=False,
            committed=True,
        ) from error
