"""笔记证据、请求预算与应用执行结果的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Evidence:
    text: str
    main_images: list[tuple[int, Path]]
    si_candidates: list[tuple[int, Path]]
    main_count: int
    si_count: int
    attachments: list[str]
    captions: dict[int, str]
    main_text: str


@dataclass(frozen=True)
class RequestPlan:
    model: str
    images: list[tuple[int, str]]
    si_images: list[tuple[int, str]]
    estimated_tokens: int
    text_tokens_upper_bound: int
    request_bytes: int
    image_tokens_reserved: int
    omitted_si: int
    # MinerU 生成但无法附上的图像（1x1 占位图，或超出大小上限的文件）。
    # 它们会在 prompt 中声明，绝不静默丢弃。
    skipped: list[int]


@dataclass(frozen=True)
class AiNoteExecution:
    """一次执行的纯数据结果及其可选幂等提交句柄。"""

    data: dict[str, Any]
    replayed: bool = False
    cached_envelope: dict[str, Any] | None = None
    cache_scope: str | None = None
    idempotency_key: str = ""
