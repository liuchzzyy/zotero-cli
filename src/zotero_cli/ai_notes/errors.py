"""AI note 分析流水线共用的错误类型。"""

from __future__ import annotations


class NoteAnalysisError(Exception):
    """面向用户的分析失败，携带稳定的错误码与重试提示。

    一旦某次库变更可能已经到达服务器，committed 标志就变为 True。调用方
    据此判断幂等预占是能安全释放（未发生变更），还是必须保持待定（结果未知）。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime_error",
        retryable: bool = False,
        committed: bool = False,
        parkable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.committed = committed
        # 只有确证为输入/预算类失败时，调度器才可将其挂起。
        self.parkable = parkable
