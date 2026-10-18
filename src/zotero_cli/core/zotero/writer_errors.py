"""Error types and pyzotero/HTTP exception translation for Zotero Web API writes."""

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
    """Raised when a Zotero write operation fails.

    Attributes:
        code: machine-readable error code (api_error, network_error, not_found,
            auth_invalid, validation_error, conflict, rate_limited).
        retryable: True when a retry may succeed (network blips, 5xx, rate
            limits); False for 4xx/validation/permission/conflict errors.
        committed: True when the request may already have reached the server
            (network failure or 5xx after sending). Callers must reconcile
            instead of blindly retrying when this is True.
        retry_after_seconds: optional hint parsed from a rate-limit response.
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
    """Extract a Retry-After hint from an exception or response header value."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = _RETRY_AFTER_PATTERN.search(str(value))
    if match is None:
        return None
    return int(match.group(1))


def _friendly_api_error(exc: PyZoteroError) -> ZoteroWriteError:
    """Convert pyzotero exceptions to user-friendly ZoteroWriteError.

    Mapping follows the HTTP semantics agents depend on: auth failures are
    non-retryable, rate limits and transport failures are retryable, 409/412 are
    write conflicts, and validation-style errors keep their actionable messages.
    committed is True only when the request may already have reached the server,
    so callers know a blind retry could duplicate the mutation.
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
        # pyzotero raises this for unclassified statuses. 5xx is transient and
        # may have been processed; unclassified 4xx is a client-side rejection.
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
    """Parse the trailing Code: NNN pyzotero includes in HTTP error text."""
    match = _HTTP_CODE_PATTERN.search(message)
    if match is None:
        return None
    return int(match.group(1))


def error_for_http_status(status: int, message: str = "", *, retry_after: object = None) -> ZoteroWriteError:
    """Translate a raw HTTP write response status into a typed ZoteroWriteError."""
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
    """Return the Retry-After header of an httpx/httpx2 response, if present."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    try:
        return _retry_after_seconds(headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        return None
