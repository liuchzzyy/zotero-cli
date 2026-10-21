"""仅使用 MinerU 的云端解析，以及规范的共享解析包缓存。

本模块是 `zotero_cli.ai_pdf` 的稳定公开门面；具体实现位于
`client`（HTTP 上传/轮询/下载流程）、`cache`（内容寻址解析包）、
`models` 与 `utils`。
"""

from __future__ import annotations

from zotero_cli.ai_pdf.cache import MinerUParseCache
from zotero_cli.ai_pdf.client import MinerUClient
from zotero_cli.ai_pdf.request_utils import extract_doi_from_markdown, file_sha256
from zotero_cli.ai_pdf.types import MinerUError, MinerUParseResult, ProgressCallback

__all__ = [
    "MinerUClient",
    "MinerUError",
    "MinerUParseCache",
    "MinerUParseResult",
    "ProgressCallback",
    "extract_doi_from_markdown",
    "file_sha256",
]
