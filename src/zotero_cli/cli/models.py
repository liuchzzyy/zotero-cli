"""命令层用于人类可读错误展示的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ErrorInfo:
    message: str
    context: str = ""
    hint: str = ""
    code: str = "runtime_error"
    retryable: bool = False
