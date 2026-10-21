"""MinerU 共用辅助函数：哈希、DOI 提取、路径校验与重试。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from requests import PreparedRequest
from requests.exceptions import (
    ChunkedEncodingError,
    Timeout,
)
from requests.exceptions import (
    ConnectionError as RequestsConnectionError,
)

from zotero_cli.ai_pdf.types import MinerUError

# 只重试明显属于瞬时的错误；确定性错误（错误 URL、非法载荷、文件缺失）
# 必须立即失败，而不是放大请求量或重复非幂等操作。
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RequestsConnectionError,
    Timeout,
    ChunkedEncodingError,
)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_doi_from_markdown(markdown: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[^\s<>]+", markdown[:30000], flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;:)]}>'\"") if match else None


def _validated_model_version(model_version: str) -> str:
    if (
        not isinstance(model_version, str)
        or model_version in {".", ".."}
        or not re.fullmatch(r"[a-zA-Z0-9._-]{1,255}", model_version)
    ):
        raise MinerUError("MinerU model version must be a single safe path component (not '.' or '..')")
    return model_version


def _url_origin(url: str) -> tuple[str, str, int | None]:
    # 使用 requests 实际发送的 URL，而非 urllib 对 authority 的不同解析结果。
    request = PreparedRequest()
    request.prepare_url(url, None)
    parsed = urlsplit(str(request.url))
    port = parsed.port if parsed.port is not None else {"http": 80, "https": 443}.get(parsed.scheme)
    return parsed.scheme, parsed.hostname or "", port


def _retry(
    function: Callable[[], Any],
    *,
    exceptions: tuple[type[BaseException], ...] = _RETRYABLE_EXCEPTIONS,
    attempts: int = 3,
) -> Any:
    """调用 `function`，仅重试瞬时/幂等失败。"""
    for attempt in range(attempts):
        try:
            return function()
        except exceptions:
            if attempt == attempts - 1:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def _as_mineru_error(exc: Exception) -> Exception:
    return exc if isinstance(exc, (MinerUError, FileNotFoundError)) else MinerUError(f"MinerU request failed: {exc}")
