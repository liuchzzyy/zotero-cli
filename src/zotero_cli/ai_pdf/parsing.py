"""缓存优先的本地 PDF 解析服务。"""

from __future__ import annotations

from pathlib import Path

from zotero_cli.ai_pdf.access.cache import MinerUParseCache
from zotero_cli.ai_pdf.errors import MinerUError, MinerUTokenMissingError, PdfCacheReadError
from zotero_cli.ai_pdf.models import MinerUParseResult, ProgressCallback
from zotero_cli.config import load_pdf_config


class PdfParsingService:
    """先读取本地解析包；仅在未命中时请求 MinerU。"""

    def __init__(self, cache: MinerUParseCache | None = None) -> None:
        self._cache = cache or MinerUParseCache()

    def parse(
        self,
        pdf_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> MinerUParseResult:
        """返回规范解析包，并在缺少缓存和令牌时提前失败。"""
        try:
            cached, fingerprint = self._cache.lookup(pdf_path)
        except MinerUError as error:
            raise PdfCacheReadError(str(error)) from error
        if cached is not None:
            return cached
        if not load_pdf_config().mineru_token:
            raise MinerUTokenMissingError("MinerU API token is not configured")
        return self._cache.ensure(pdf_path, progress_callback, fingerprint=fingerprint)

    def parse_many(
        self,
        pdf_paths: list[Path],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[Path, MinerUParseResult | Exception]:
        """批量返回缓存优先的解析结果及每份 PDF 的失败。"""
        return self._cache.ensure_many(pdf_paths, progress_callback)
