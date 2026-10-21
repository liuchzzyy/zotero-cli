"""PDF 导入、导出、选择与附件幂等上传服务。"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import zotero_cli.ai_pdf as ai_pdf_module
import zotero_cli.config as config_module
from zotero_cli.ai_pdf import file_sha256
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.zotero.access import idempotency as idempotency_module
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.idempotency import (
    begin_mutation,
    failure_definitely_rejected,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.zotero.access.writer import ZoteroWriter
from zotero_cli.zotero.items import MetadataResolveError, resolve_doi, summarize_item_metadata
from zotero_cli.zotero.models import Attachment


class AddFromPdfError(Exception):
    """从 PDF 添加条目时的可分类失败。

    `code` 与 `retryable` / `committed` 直接对应输出契约里的错误字段，
    命令层据此选择退出码与提示文案。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool = False,
        committed: bool = False,
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.committed = committed
        self.hint = hint


@dataclass
class AddFromPdfResult:
    """条目创建结果；附件失败时仍返回条目键并附带失败信息。

    `attachment_error` 非空表示条目已创建但附件未上传成功，调用方应把这种情况
    报告为部分成功，而不是整体失败。
    """

    key: str
    doi: str
    resolve_requested: bool = True
    resolved_summary: dict[str, Any] | None = None
    resolve_warning: str | None = None
    attachment_key: str | None = None
    attachment_error: ZoteroWriteError | None = None
    cached_envelope: dict[str, Any] | None = field(default=None, repr=False)
    cache_failed: bool = False

    def data_payload(self) -> dict[str, Any]:
        """返回成功 envelope 的 data 部分，供命令层组装输出与幂等缓存复用。"""
        data: dict[str, Any] = {"key": self.key, "doi": self.doi, "sync_required": True}
        if self.resolved_summary is not None:
            data["resolved"] = self.resolved_summary
        elif self.resolve_requested:
            data["resolved"] = None
            data["resolve_warning"] = self.resolve_warning
        if self.attachment_key:
            data["attachment_key"] = self.attachment_key
        return data

    def success_envelope(self) -> dict[str, Any]:
        """构造写入成功时缓存下来的 envelope，供幂等重放使用。"""
        return envelope_ok(self.data_payload())


def _pdf_scope_payload(pdf_path: Path, doi_override: str | None, resolve: bool) -> dict[str, Any]:
    """以路径、大小、修改时间和覆盖参数标识 PDF 幂等请求。"""
    try:
        stat = pdf_path.stat()
        size: int | None = stat.st_size
        mtime_ns: int | None = stat.st_mtime_ns
    except OSError:
        size = None
        mtime_ns = None
    return {"pdf": str(pdf_path.resolve()), "size": size, "mtime_ns": mtime_ns, "doi": doi_override, "resolve": resolve}


def _raise(code: str, message: str, **kwargs: Any) -> NoReturn:
    raise AddFromPdfError(message, code=code, **kwargs)


def add_item_from_pdf(
    pdf_path: Path,
    *,
    library_id: str,
    api_key: str,
    library_type: str = "user",
    doi_override: str | None = None,
    resolve: bool = True,
    idempotency_key: str | None = None,
    metadata_resolver: Callable[[str], dict | None] = resolve_doi,
    writer_factory: Callable[..., ZoteroWriter] = ZoteroWriter,
) -> AddFromPdfResult:
    """解析 PDF 后创建条目并上传附件。

    命中幂等缓存时直接返回 `cached_envelope`，调用方无需再做任何写入。
    附件上传失败不会抛出异常，而是通过 `attachment_error` 报告，因为此时
    条目已经创建，属于部分成功。
    """
    cache_scope = idempotency_module.request_scope(
        "add-pdf",
        library_type,
        str(library_id),
        _pdf_scope_payload(pdf_path, doi_override, resolve),
    )

    if idempotency_key:
        try:
            cached = idempotency_module.begin_mutation(cache_scope, idempotency_key)
        except idempotency_module.PendingMutationError as exc:
            _raise(
                "conflict",
                str(exc),
                retryable=False,
                hint="Reconcile the remote library before retrying; do not bypass this with a new key",
            )
        except (OSError, sqlite3.Error):
            _raise(
                "configuration_error",
                "Could not durably reserve item creation; no write attempted",
                retryable=False,
                hint="Check the idempotency state directory and database",
            )
        if cached is not None:
            return AddFromPdfResult(
                key=str(cached.get("data", {}).get("key", "?")),
                doi=doi_override or "",
                resolve_requested=resolve,
                cached_envelope=cached,
            )

    def release() -> None:
        """仅在能证明服务端拒绝写入时解除预留。"""
        if not idempotency_key:
            return
        try:
            idempotency_module.release_rejected_mutation(cache_scope, idempotency_key)
        except (OSError, sqlite3.Error):
            pass

    doi = doi_override
    if not doi:
        cache = ai_pdf_module.MinerUParseCache()
        if cache.get(pdf_path) is None and not config_module.load_pdf_config().mineru_token:
            release()
            _raise(
                "configuration_error",
                "MinerU API token is not configured",
                hint="Set [pdf].mineru_token in .zot/config.toml or pass --doi",
            )
        try:
            parsed = cache.ensure(pdf_path)
        except ai_pdf_module.MinerUError as exc:
            release()
            _raise("network_error", str(exc), retryable=True)
        doi = ai_pdf_module.extract_doi_from_markdown(parsed.markdown)
    if not doi:
        release()
        _raise(
            "validation_error",
            "No DOI found in PDF",
            hint="Use --doi to specify the DOI manually: zot zotero add --pdf paper.pdf --doi '10.1234/...'",
        )

    extra_fields: dict[str, Any] | None = None
    resolved_summary: dict[str, Any] | None = None
    resolve_warning: str | None = None
    if resolve:
        try:
            fields = metadata_resolver(doi)
            if fields:
                extra_fields = fields
                resolved_summary = summarize_item_metadata(fields)
            else:
                resolve_warning = "no_match"
        except MetadataResolveError as error:
            resolve_warning = str(error)

    writer = writer_factory(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        key = writer.add_item(doi=doi, extra_fields=extra_fields)
    except ZoteroWriteError as error:
        rejected = False
        if idempotency_key:
            rejected = idempotency_module.failure_definitely_rejected(
                response=writer._zot.request,
                code=error.code,
                retryable=error.retryable,
                committed=error.committed,
            )
            if rejected:
                release()
        outcome_unknown = error.committed or (bool(idempotency_key) and not rejected)
        _raise(
            error.code,
            str(error),
            retryable=False if outcome_unknown else error.retryable,
            committed=error.committed,
            hint=(
                "The write outcome is unknown. Reconcile the library before retrying; do not reuse this key blindly"
                if outcome_unknown
                else "Check API credentials and network"
            ),
        )

    attachment_key: str | None = None
    attachment_error: ZoteroWriteError | None = None
    try:
        attachment_key = writer.upload_attachment(key, pdf_path)
    except ZoteroWriteError as error:
        attachment_error = error

    result = AddFromPdfResult(
        key=key,
        doi=doi,
        resolve_requested=resolve,
        resolved_summary=resolved_summary,
        resolve_warning=resolve_warning,
        attachment_key=attachment_key,
        attachment_error=attachment_error,
    )
    if idempotency_key and attachment_error is None:
        try:
            idempotency_module.store_cached(cache_scope, idempotency_key, result.success_envelope())
        except (OSError, sqlite3.Error):
            # 缓存失败不影响已完成的写入，由调用方决定是否提示。
            result.cache_failed = True
    return result


def select_pdf_attachment(attachments: list[Attachment], attachment_key: str | None = None) -> Attachment:
    """有多个 PDF 时要求明确指定附件键，避免误用正文或补充材料。"""
    if attachment_key is not None:
        for attachment in attachments:
            if attachment.key == attachment_key:
                return attachment
        raise LookupError(f"PDF attachment '{attachment_key}' not found")
    if not attachments:
        raise LookupError("No PDF attachment found")
    if len(attachments) > 1:
        raise ValueError("Multiple PDFs; select one with --attachment KEY")
    return attachments[0]


def export_pdf(source_path: Path, destination_path: Path) -> None:
    """以排他方式复制 PDF；仅清理本次创建但未完成的目标文件。"""
    created = False
    try:
        with source_path.open("rb") as source, destination_path.open("xb") as destination:
            created = True
            shutil.copyfileobj(source, destination)
    except Exception:
        if created:
            try:
                destination_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


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
