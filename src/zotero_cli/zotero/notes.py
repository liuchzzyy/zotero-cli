"""笔记创建的幂等预约、写入与结果重放服务。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import NoReturn

from zotero_cli.output.envelope import envelope_ok
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.writer import ZoteroWriter


class NoteCreationError(Exception):
    """笔记写入失败及其可供调用方呈现的错误属性。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.hint = hint


def raise_note_error(code: str, message: str, *, retryable: bool = False, hint: str = "") -> NoReturn:
    raise NoteCreationError(code, message, retryable=retryable, hint=hint)


def create_note(
    key: str,
    content: str,
    *,
    library_id: str,
    api_key: str,
    library_type: str,
    idempotency_key: str | None,
    writer_factory: Callable[..., ZoteroWriter] = ZoteroWriter,
) -> tuple[dict, bool]:
    """创建笔记并缓存成功结果；未知写入保留预约，缓存命中不构造写入器。"""
    from zotero_cli.zotero.access.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    cache_scope = request_scope("note", library_type, str(library_id), {"parent": key, "content": content})
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as error:
            raise_note_error(
                "conflict",
                str(error),
                retryable=False,
                hint="Reconcile the remote parent's notes before proceeding; do not bypass this with a new key",
            )
        except (OSError, sqlite3.Error):
            raise_note_error(
                "configuration_error",
                "Could not durably reserve note creation; no write attempted",
                retryable=False,
                hint="Check the idempotency state directory and database",
            )
        if cached is not None:
            return cached, True

    writer = None
    try:
        writer = writer_factory(library_id=str(library_id), api_key=api_key, library_type=library_type)
        note_key = writer.add_note(key, content)
    except Exception as exc:
        # pyzotero 解析明确的 HTTP 拒绝时也可能失败；其他解析或本地错误
        # 不能证明远端未提交，因此不暴露任意异常消息。
        write_error = (
            exc
            if isinstance(exc, ZoteroWriteError)
            else ZoteroWriteError("Note creation did not return a usable result")
        )
        # 优先检查客户端保留的响应；无响应时以写入异常的提交状态作依据。
        response = getattr(getattr(writer, "_zot", None), "request", None) if writer is not None else None
        rejected = failure_definitely_rejected(
            response=response,
            code=write_error.code,
            retryable=write_error.retryable,
            committed=write_error.committed,
        )
        if rejected and idempotency_key:
            try:
                release_rejected_mutation(cache_scope, idempotency_key)
            except (OSError, sqlite3.Error):
                rejected = False  # 持久化解除失败时保留写入预约。
        outcome_unknown = not rejected
        raise_note_error(
            write_error.code,
            str(write_error),
            retryable=False if outcome_unknown else write_error.retryable,
            hint=(
                "The write outcome is unknown. Reconcile the remote parent's notes; do not retry with a new key"
                if outcome_unknown
                else "Check item key and API credentials"
            ),
        )

    env = envelope_ok(
        {"note_key": note_key, "parent_key": key, "sync_required": True},
        next=[f"zot zotero note {key}"],
    )
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, sqlite3.Error):
            raise_note_error(
                "configuration_error",
                f"Note '{note_key}' was created but its retry result could not be saved",
                retryable=False,
                hint="Reconcile this note remotely; the pending reservation still blocks blind retries",
            )
    return env, False
