"""AI PDF 流水线共用的数据模型与资源上限。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_cli.zotero.models import Attachment

ProgressCallback = Callable[[str, int, int, int], None]

# 本地输入/输出限制。MinerU 拒绝大于 200 MB 的 PDF；返回的归档在缓冲或
# 解压之前就设上限，使恶意或异常响应无法耗尽内存或磁盘。
_MAX_FILE_BYTES = 200 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_MEMBER_BYTES = 200 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 1024 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 10_000
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class PdfDocument:
    """已验证且选定的 Zotero PDF 附件。"""

    item_key: str
    attachment: Attachment
    pdf_path: Path


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


@dataclass(frozen=True)
class PdfContent:
    """根据请求模式从一份解析包读取的内容。"""

    markdown: str | None = None
    outline: list[dict[str, int | str]] | None = None
    content_list: Any = None
    section: int | None = None


@dataclass(frozen=True)
class ParsedPdfDocument:
    """选定附件及其缓存优先的 MinerU 解析结果。"""

    document: PdfDocument
    parsed: MinerUParseResult
