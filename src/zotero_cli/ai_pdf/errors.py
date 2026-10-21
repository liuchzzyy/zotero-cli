"""AI PDF 领域服务的可分类异常。"""

from __future__ import annotations


class MinerUError(Exception):
    """云端解析或本地解析包操作失败时抛出。"""


class MinerUTokenMissingError(MinerUError):
    """缓存未命中且未配置 MinerU 令牌时抛出。"""


class PdfCacheReadError(MinerUError):
    """本地解析缓存无法安全读取时抛出。"""


class PdfSectionNotFoundError(Exception):
    """请求的 Markdown 区段不存在时抛出。"""


class PdfDocumentError(Exception):
    """Zotero 条目及其 PDF 附件无法解析时抛出。"""

    def __init__(self, message: str, *, kind: str) -> None:
        super().__init__(message)
        self.kind = kind
