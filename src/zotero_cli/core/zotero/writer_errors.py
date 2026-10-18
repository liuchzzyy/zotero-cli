"""Error types and pyzotero exception translation for Zotero Web API writes."""

from __future__ import annotations

import re

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


class ZoteroWriteError(Exception):
    """Raised when a Zotero write operation fails.

    Attributes:
        code: machine-readable error code (api_error, network_error, not_found,
            auth_invalid, validation_error, conflict, rate_limited).
        retryable: True when a retry may succeed (network blips, 5xx, rate
            limits); False for 4xx/validation/permission/conflict errors.
        retry_after_seconds: optional hint parsed from a rate-limit response.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "api_error",
        retryable: bool = False,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def _retry_after_seconds(exc: BaseException) -> int | None:
    """Extract a Retry-After hint from the pyzotero error text when present."""
    match = _RETRY_AFTER_PATTERN.search(str(exc))
    if match is None:
        return None
    return int(match.group(1))


def _friendly_api_error(exc: PyZoteroError) -> ZoteroWriteError:
    """Convert pyzotero exceptions to user-friendly ZoteroWriteError.

    Mapping follows the HTTP semantics agents depend on: auth failures are
    non-retryable, rate limits and transport failures are retryable, 409/412 are
    write conflicts, and validation-style errors keep their actionable messages.
    """
    msg = str(exc)
    if isinstance(exc, UserNotAuthorisedError):
        return ZoteroWriteError(
            "Write access denied (403). Check that your API key has write "
            "permissions enabled at https://www.zotero.org/settings/keys",
            code="auth_invalid",
            retryable=False,
        )
    if isinstance(exc, (TooManyRequestsError, TooManyRetriesError)):
        return ZoteroWriteError(
            f"Zotero rate limit: {msg}",
            code="rate_limited",
            retryable=True,
            retry_after_seconds=_retry_after_seconds(exc),
        )
    if isinstance(exc, (ConflictError, PreConditionFailedError)):
        return ZoteroWriteError(
            f"Zotero write conflict: {msg}",
            code="conflict",
            retryable=False,
        )
    if isinstance(exc, (CouldNotReachURLError, UploadError)):
        return ZoteroWriteError(f"Network error: {msg}", code="network_error", retryable=True)
    if isinstance(exc, ResourceNotFoundError):
        return ZoteroWriteError(f"Zotero API error: {msg}", code="not_found", retryable=False)
    if isinstance(exc, HTTPError):
        # pyzotero raises this for unclassified statuses (5xx): transient.
        return ZoteroWriteError(f"Zotero API error: {msg}", code="api_error", retryable=True)
    if isinstance(exc, UnsupportedParamsError):
        if "Invalid user ID" in msg:
            return ZoteroWriteError(
                "Invalid user ID (400). Your library_id must be the numeric "
                "userID from https://www.zotero.org/settings/keys, not your username. "
                "Run 'zot config init' to fix.",
                code="validation_error",
                retryable=False,
            )
        return ZoteroWriteError(f"Bad request (400): {msg}", code="validation_error", retryable=False)
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
        return ZoteroWriteError(f"Validation error: {msg}", code="validation_error", retryable=False)
    return ZoteroWriteError(f"Zotero API error: {msg}")
