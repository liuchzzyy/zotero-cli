"""Zotero Web API 写入的错误类型以及 pyzotero/HTTP 异常转换。"""

from __future__ import annotations

import re
from typing import Any

from pyzotero.zotero_errors import (
    ConflictError,
    CouldNotReachURLError,
    FileDoesNotExistError,
    HTTPError,
    InvalidItemFieldsError,
    MissingCredentialsError,
    ParamNotPassedError,
    PreConditionFailedError,
    PreConditionRequiredError,
    PyZoteroError,
    RequestEntityTooLargeError,
    ResourceNotFoundError,
    TooManyItemsError,
    TooManyRequestsError,
    TooManyRetriesError,
    UnsupportedParamsError,
    UploadError,
    UserNotAuthorisedError,
)

_RETRY_AFTER_PATTERN = re.compile(r"retry[- ]after\D{0,6}(\d+)", re.IGNORECASE)
_HTTP_CODE_PATTERN = re.compile(r"Code:\s*(\d{3})", re.IGNORECASE)


class ZoteroWriteError(Exception):
    """Zotero 写操作失败时抛出。

    属性：
        code：机器可读的错误码（api_error、network_error、not_found、
            auth_invalid、validation_error、conflict、rate_limited）。
        retryable：重试可能成功时为 True（网络抖动、5xx、限流）；
            4xx、校验、权限或冲突类错误为 False。
        committed：请求可能已经到达服务端时为 True（发送后发生网络故障或
            5xx）。该值为 True 时，调用方必须进行核对，不能盲目重试。
        retry_after_seconds：从限流响应中解析出的可选提示。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "api_error",
        retryable: bool = False,
        committed: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.committed = committed
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(value: object) -> int | None:
    """从异常或响应头值中提取 Retry-After 提示。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = _RETRY_AFTER_PATTERN.search(str(value))
    if match is None:
        return None
    return int(match.group(1))


def _friendly_api_error(exc: PyZoteroError) -> ZoteroWriteError:
    """把 pyzotero 异常转换为便于用户理解的 ZoteroWriteError。

    映射遵循代理所依赖的 HTTP 语义：认证失败不可重试，限流与传输故障可重试，
    409/412 属于写入冲突，校验类错误保留可直接处理的提示信息。
    仅当请求可能已经到达服务端时才把 committed 置为 True，
    以便调用方知道盲目重试可能导致变更重复。
    """
    msg = str(exc)
    if isinstance(exc, UserNotAuthorisedError):
        return ZoteroWriteError(
            "Write access denied (403). Check that your API key has write "
            "permissions enabled at https://www.zotero.org/settings/keys",
            code="auth_invalid",
            retryable=False,
            committed=False,
        )
    if isinstance(exc, (TooManyRequestsError, TooManyRetriesError)):
        return ZoteroWriteError(
            f"Zotero rate limit: {msg}",
            code="rate_limited",
            retryable=True,
            committed=False,
            retry_after_seconds=_retry_after_seconds(msg),
        )
    if isinstance(exc, (ConflictError, PreConditionFailedError)):
        return ZoteroWriteError(
            f"Zotero write conflict: {msg}",
            code="conflict",
            retryable=False,
            committed=False,
        )
    if isinstance(exc, (CouldNotReachURLError, UploadError)):
        return ZoteroWriteError(
            f"Network error: {msg}",
            code="network_error",
            retryable=True,
            committed=True,
        )
    if isinstance(exc, ResourceNotFoundError):
        return ZoteroWriteError(f"Zotero API error: {msg}", code="not_found", retryable=False, committed=False)
    if isinstance(exc, HTTPError):
        # pyzotero 对未归类的状态码抛出此异常。5xx 是暂时性的，
        # 且可能已被处理；未归类的 4xx 属于客户端侧拒绝。
        status = _status_from_message(msg)
        retryable = status is None or status >= 500
        return ZoteroWriteError(
            f"Zotero API error: {msg}",
            code="api_error",
            retryable=retryable,
            committed=status is not None and status >= 500,
        )
    if isinstance(exc, UnsupportedParamsError):
        if "Invalid user ID" in msg:
            return ZoteroWriteError(
                "Invalid user ID (400). Your library_id must be the numeric "
                "userID from https://www.zotero.org/settings/keys, not your username. "
                "Run 'zot config init' to fix.",
                code="validation_error",
                retryable=False,
                committed=False,
            )
        return ZoteroWriteError(f"Bad request (400): {msg}", code="validation_error", retryable=False, committed=False)
    if isinstance(
        exc,
        (
            ParamNotPassedError,
            InvalidItemFieldsError,
            TooManyItemsError,
            RequestEntityTooLargeError,
            PreConditionRequiredError,
            FileDoesNotExistError,
            MissingCredentialsError,
        ),
    ):
        return ZoteroWriteError(f"Validation error: {msg}", code="validation_error", retryable=False, committed=False)
    return ZoteroWriteError(f"Zotero API error: {msg}", code="api_error", retryable=False, committed=False)


def _status_from_message(message: str) -> int | None:
    """解析 pyzotero 在 HTTP 错误文本末尾附带的 Code: NNN。"""
    match = _HTTP_CODE_PATTERN.search(message)
    if match is None:
        return None
    return int(match.group(1))


def error_for_http_status(status: int, message: str = "", *, retry_after: object = None) -> ZoteroWriteError:
    """把原始 HTTP 写入响应状态码转换为带类型的 ZoteroWriteError。"""
    detail = f": {message}" if message else ""
    if status in (401, 403):
        return ZoteroWriteError(
            "Write access denied (403). Check that your API key has write permissions enabled "
            "at https://www.zotero.org/settings/keys",
            code="auth_invalid",
            retryable=False,
            committed=False,
        )
    if status == 404:
        return ZoteroWriteError(
            f"Zotero API error: not found{detail}", code="not_found", retryable=False, committed=False
        )
    if status in (409, 412):
        return ZoteroWriteError(
            f"Zotero write conflict{detail}",
            code="conflict",
            retryable=False,
            committed=False,
        )
    if status == 429:
        return ZoteroWriteError(
            f"Zotero rate limit{detail}",
            code="rate_limited",
            retryable=True,
            committed=False,
            retry_after_seconds=_retry_after_seconds(retry_after),
        )
    if status in (400, 413, 422):
        return ZoteroWriteError(
            f"Zotero rejected the write{detail}",
            code="validation_error",
            retryable=False,
            committed=False,
        )
    if status >= 500:
        return ZoteroWriteError(
            f"Zotero API error{detail}",
            code="api_error",
            retryable=True,
            committed=True,
        )
    return ZoteroWriteError(f"Zotero API error{detail}", code="api_error", retryable=False, committed=False)


def response_retry_after(response: Any) -> int | None:
    """返回 httpx/httpx2 响应的 Retry-After 头（若存在）。"""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return _retry_after_seconds(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None


class ZoteroSchemaError(RuntimeError):
    """本地 Zotero userdata schema 超出已验证范围时抛出。"""

    def __init__(
        self,
        version: int,
        *,
        minimum: int = 100,
        maximum: int = 200,
    ) -> None:
        self.schema_version = version
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(
            f"Unsupported Zotero database schema version {version} "
            f"(tested range {minimum}-{maximum}). The database may come from a "
            "newer Zotero release; update zotero-cli or use a supported Zotero version."
        )
