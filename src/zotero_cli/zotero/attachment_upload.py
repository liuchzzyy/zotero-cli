"""可复用的 Zotero Web API 附件上传保护流程。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from zotero_cli.core.mineru import file_sha256
from zotero_cli.core.zotero.idempotency import (
    begin_mutation,
    failure_definitely_rejected,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError


@dataclass(frozen=True)
class AttachmentUploadResult:
    """一次附件上传的成功结果。"""

    attachment_key: str
    cached_envelope: dict | None = None


@dataclass(frozen=True)
class AttachmentUploadFailure(Exception):
    """携带幂等性保护结论的上传失败。"""

    error: ZoteroWriteError
    retryable: bool


def attachment_upload_scope(
    *,
    command: str,
    library_type: str,
    library_id: str,
    parent_key: str,
    file_path: Path,
) -> str:
    """为一个确定的附件文件和父条目创建幂等作用域。"""
    return request_scope(
        command,
        library_type,
        library_id,
        {"parent": parent_key, "file": str(file_path.resolve()), "sha256": file_sha256(file_path)},
    )


def upload_attachment_protected(
    *,
    writer: ZoteroWriter | None = None,
    writer_factory: Callable[[], ZoteroWriter] | None = None,
    parent_key: str,
    file_path: Path,
    idempotency_key: str | None,
    cache_scope: str = "",
) -> AttachmentUploadResult:
    """上传附件，并在指定幂等键时保留未知写入的保护状态。"""
    if idempotency_key:
        cached = begin_mutation(cache_scope, idempotency_key)
        if cached is not None:
            return AttachmentUploadResult("", cached)
    if writer is None:
        if writer_factory is None:
            raise ValueError("writer or writer_factory is required")
        writer = writer_factory()

    try:
        attachment_key = writer.upload_attachment(parent_key, file_path)
    except ZoteroWriteError as error:
        rejected = False
        if idempotency_key:
            rejected = failure_definitely_rejected(
                response=writer._zot.request,
                code=error.code,
                retryable=error.retryable,
                committed=error.committed,
            )
            if rejected:
                try:
                    release_rejected_mutation(cache_scope, idempotency_key)
                except (OSError, sqlite3.Error):
                    pass
        raise AttachmentUploadFailure(
            error=error,
            retryable=False if (error.committed or (idempotency_key and not rejected)) else error.retryable,
        ) from error

    return AttachmentUploadResult(attachment_key)


def cache_attachment_upload(
    *, cache_scope: str, idempotency_key: str | None, envelope: dict
) -> OSError | sqlite3.Error | None:
    """缓存已确认的上传结果；缓存失败不否定已经提交的远端写入。"""
    if not idempotency_key:
        return None
    try:
        store_cached(cache_scope, idempotency_key, envelope)
    except (OSError, sqlite3.Error) as error:
        return error
    return None
