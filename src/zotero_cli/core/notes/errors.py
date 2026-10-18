"""Error type shared by the AI note analysis pipeline."""

from __future__ import annotations


class NoteAnalysisError(Exception):
    """A user-facing analysis failure carrying a stable code and retry hint.

    The committed flag becomes True once a library mutation may have reached the
    server. Callers use it to decide whether an idempotency reservation can be
    released safely (no mutation) or must stay pending (unknown outcome).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_error",
        retryable: bool = False,
        committed: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.committed = committed
