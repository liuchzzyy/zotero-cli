"""Error types and pyzotero exception translation for Zotero Web API writes."""

from __future__ import annotations

from pyzotero.zotero_errors import PyZoteroError, UnsupportedParamsError, UserNotAuthorisedError


class ZoteroWriteError(Exception):
    """Raised when a Zotero write operation fails.

    Attributes:
        code: machine-readable error code (api_error, network_error, not_found,
            auth_invalid, validation_error, rate_limited).
        retryable: True when a retry may succeed (network blips, 5xx, rate
            limits); False for 4xx/validation/permission errors.
        retry_after_seconds: optional hint for rate-limited errors.
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


def _friendly_api_error(exc: PyZoteroError) -> ZoteroWriteError:
    """Convert pyzotero exceptions to user-friendly ZoteroWriteError."""
    msg = str(exc)
    if isinstance(exc, UserNotAuthorisedError):
        return ZoteroWriteError(
            "Write access denied (403). Check that your API key has write "
            "permissions enabled at https://www.zotero.org/settings/keys",
            code="auth_invalid",
            retryable=False,
        )
    if isinstance(exc, UnsupportedParamsError):
        if "Invalid user ID" in msg:
            return ZoteroWriteError(
                "Invalid user ID (400). Your library_id must be the numeric "
                "userID from https://www.zotero.org/settings/keys, not your username. "
                "Run 'zot config init' to fix."
            )
        return ZoteroWriteError(f"Bad request (400): {msg}")
    return ZoteroWriteError(f"Zotero API error: {msg}")
