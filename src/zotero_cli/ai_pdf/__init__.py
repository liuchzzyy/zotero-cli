"""MinerU-only cloud parsing and the canonical shared parse package cache.

This module is the stable public facade for `zotero_cli.ai_pdf`; the
implementations live in `client` (HTTP upload/poll/download flow), `cache`
(content-addressed parse packages), `models`, and `utils`.
"""

from __future__ import annotations

from zotero_cli.ai_pdf.cache import MinerUParseCache
from zotero_cli.ai_pdf.client import MinerUClient
from zotero_cli.ai_pdf.models import MinerUError, MinerUParseResult, ProgressCallback
from zotero_cli.ai_pdf.utils import extract_doi_from_markdown, file_sha256

__all__ = [
    "MinerUClient",
    "MinerUError",
    "MinerUParseCache",
    "MinerUParseResult",
    "ProgressCallback",
    "extract_doi_from_markdown",
    "file_sha256",
]
