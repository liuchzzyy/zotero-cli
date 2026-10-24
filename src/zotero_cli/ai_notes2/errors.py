"""ai-note2 的失败类型；沿用旧流水线的错误码与 committed 语义。"""

from __future__ import annotations

from zotero_cli.ai_notes.errors import NoteAnalysisError


class AiNote2Error(NoteAnalysisError):
    """单篇三链笔记执行中的可呈现失败。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_error",
        retryable: bool = False,
        committed: bool = False,
        parkable: bool = False,
        hint_key: str = "",
    ) -> None:
        super().__init__(message, code=code, retryable=retryable, committed=committed, parkable=parkable)
        self.hint_key = hint_key
