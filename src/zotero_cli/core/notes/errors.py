"""Error type shared by the AI note analysis pipeline."""

from __future__ import annotations


class NoteAnalysisError(Exception):
    """A user-facing analysis failure carrying a stable code and retry hint."""

    def __init__(self, message: str, *, code: str = "runtime_error", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
