"""AI PDF 流水线共用的数据模型与资源上限。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zotero_cli.config.runtime import section
from zotero_cli.zotero.models import Attachment

ProgressCallback = Callable[[str, int, int, int], None]

# 本地输入/输出限制来自 `[pdf.limits]`；服务商自身上限不能被本地配置提高。
_LIMIT_KEYS = {
    "_MAX_FILE_BYTES": "max_file_bytes",
    "_MAX_ARCHIVE_BYTES": "max_archive_bytes",
    "_MAX_ARCHIVE_MEMBER_BYTES": "max_archive_member_bytes",
    "_MAX_ARCHIVE_TOTAL_BYTES": "max_archive_total_bytes",
    "_MAX_ARCHIVE_ENTRIES": "max_archive_entries",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def __getattr__(name: str) -> Any:
    key = _LIMIT_KEYS.get(name)
    if key is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return section("pdf")["limits"][key]


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
