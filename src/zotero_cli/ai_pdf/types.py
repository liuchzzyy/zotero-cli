"""MinerU 流水线共用的类型、错误与常量。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ProgressCallback = Callable[[str, int, int, int], None]

# 本地输入/输出限制。MinerU 拒绝大于 200 MB 的 PDF；返回的归档在缓冲或
# 解压之前就设上限，使恶意或异常响应无法耗尽内存或磁盘。
_MAX_FILE_BYTES = 200 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_MEMBER_BYTES = 200 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 10_000
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class MinerUError(Exception):
    """云端解析或本地解析包操作失败时抛出。"""


@dataclass(frozen=True)
class MinerUParseResult:
    """一份由 AI note 与 RAG 共用的规范 MinerU 解析包。"""

    source_path: Path
    fingerprint: str
    model_version: str
    root: Path
    markdown_path: Path
    content_list_path: Path
    manifest_path: Path

    @property
    def markdown(self) -> str:
        return self.markdown_path.read_text(encoding="utf-8")

    @property
    def content_list(self) -> Any:
        return json.loads(self.content_list_path.read_text(encoding="utf-8"))

    @property
    def image_paths(self) -> list[Path]:
        raw_dir = self.root / "raw"
        if not raw_dir.exists():
            return []
        return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES)
