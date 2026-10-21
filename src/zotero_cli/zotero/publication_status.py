"""用于检查预印本发表状态的 Semantic Scholar API 客户端。"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

API_BASE = "https://api.semanticscholar.org/graph/v1"
FIELDS = "externalIds,journal,venue,publicationDate,title,publicationVenue"

# 速率限制
RATE_DELAY_NO_KEY = 3.0  # 无 API 密钥时请求之间的间隔秒数
RATE_DELAY_WITH_KEY = 1.0  # 有 API 密钥时请求之间的间隔秒数
REQUEST_TIMEOUT = 15.0
MAX_RETRY_WAIT = 60.0

# bioRxiv/medRxiv 的 DOI 前缀
BIORXIV_DOI_PREFIX = "10.1101/"


@dataclass
class PublicationStatus:
    """预印本发表状态检查的结果。"""

    preprint_id: str
    source: str  # "arxiv" 或 "doi"（用于 bioRxiv/medRxiv）
    title: str
    is_published: bool
    venue: str | None = None
    journal_name: str | None = None
    doi: str | None = None
    publication_date: str | None = None


class SemanticScholarError(Exception):
    """当无法查询 Semantic Scholar 时抛出。

    404（Semantic Scholar 不知道该论文）不是错误——调用方会得到 None。
    网络故障、速率限制、5xx 响应以及载荷格式错误会抛出此异常，使调用方
    能够报告可重试错误，而不是静默报告“未发表”。
    """

    def __init__(self, message: str, *, retryable: bool = True, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited


@dataclass
class PreprintInfo:
    """提取出的预印本标识符。"""

    preprint_id: str
    source: str  # "arxiv" 或 "doi"
    api_id: str  # 为 Semantic Scholar API 格式化后的 ID


def extract_preprint_info(
    url: str | None = None, doi: str | None = None, extra: str | None = None
) -> PreprintInfo | None:
    """从 URL、DOI 或 extra 字段中提取预印本 ID。

    支持：
      - arXiv: https://arxiv.org/abs/1706.03762, 10.48550/arXiv.1706.03762, arXiv:1706.03762
      - bioRxiv: https://www.biorxiv.org/content/10.1101/..., DOI 10.1101/...
      - medRxiv: https://www.medrxiv.org/content/10.1101/..., DOI 10.1101/...
    """
    # arXiv 模式
    arxiv_patterns = [
        r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)",
        r"arxiv\.org/(?:abs|pdf)/([a-z\-]+/\d{7}(?:v\d+)?)",
        r"10\.48550/arXiv\.(\d{4}\.\d{4,5}(?:v\d+)?)",
        r"arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)",
    ]
    for source in [url or "", doi or "", extra or ""]:
        if not source:
            continue
        for pattern in arxiv_patterns:
            m = re.search(pattern, source, re.IGNORECASE)
            if m:
                aid = re.sub(r"v\d+$", "", m.group(1))
                return PreprintInfo(preprint_id=aid, source="arxiv", api_id=f"arXiv:{aid}")

    # bioRxiv/medRxiv：DOI 以 10.1101/ 开头
    for source in [doi or "", url or ""]:
        if not source:
            continue
        m = re.search(r"(10\.1101/\d{4}\.\d{2}\.\d{2}\.\d+)(?:v\d+)?", source)
        if m:
            biorxiv_doi = m.group(1)
            return PreprintInfo(preprint_id=biorxiv_doi, source="doi", api_id=f"DOI:{biorxiv_doi}")

    return None


class SemanticScholarClient:
    """带速率限制的 Semantic Scholar API 客户端。"""

    def __init__(self, api_key: str | None = None) -> None:
        self._delay = RATE_DELAY_WITH_KEY if api_key else RATE_DELAY_NO_KEY
        self._last_request_time: float = 0
        headers: dict[str, str] = {}
        if api_key:
            headers["x-api-key"] = api_key
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers)

    def close(self) -> None:
        self._client.close()

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_request_time = time.time()

    def _fetch(self, api_url: str) -> dict | None:
        """带速率限制地从 API 获取数据，遇到 429 时重试一次。

        临时故障会抛出 SemanticScholarError，使调用方能够报告可重试错误，
        而不是把服务中断当作“未发表”。
        """
        self._rate_limit()
        try:
            resp = self._client.get(api_url)
        except httpx.HTTPError as exc:
            raise SemanticScholarError(f"Semantic Scholar request failed: {exc}", retryable=True) from exc

        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "")
            try:
                wait = float(retry_after)
            except ValueError:
                try:
                    wait = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    wait = 0.0
            if not math.isfinite(wait):
                wait = 0.0
            if wait > MAX_RETRY_WAIT:
                raise SemanticScholarError(
                    "Semantic Scholar rate limit exceeded; retry later",
                    retryable=True,
                    rate_limited=True,
                )
            time.sleep(max(self._delay * 2, wait))
            self._last_request_time = time.time()
            try:
                resp = self._client.get(api_url)
            except httpx.HTTPError as exc:
                raise SemanticScholarError(f"Semantic Scholar request failed: {exc}", retryable=True) from exc
            if resp.status_code != 200:
                raise SemanticScholarError(
                    f"Semantic Scholar is still rate limiting (HTTP {resp.status_code})",
                    retryable=True,
                    rate_limited=resp.status_code == 429,
                )
        if resp.status_code != 200:
            raise SemanticScholarError(
                f"Semantic Scholar returned HTTP {resp.status_code}",
                retryable=resp.status_code >= 500,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise SemanticScholarError("Semantic Scholar returned invalid JSON", retryable=True) from exc
        if not isinstance(data, dict):
            raise SemanticScholarError("Semantic Scholar returned an unexpected payload", retryable=True)
        return data

    def check_publication(self, info: PreprintInfo) -> PublicationStatus | None:
        """检查预印本是否已正式发表。

        返回 PublicationStatus；若论文未找到则返回 None。
        """
        url = f"{API_BASE}/paper/{info.api_id}?fields={FIELDS}"
        data = self._fetch(url)
        if data is None:
            return None

        title = data.get("title", "")
        venue = data.get("venue") or None
        journal = data.get("journal") or {}
        journal_name = journal.get("name") if isinstance(journal, dict) else None
        pub_date = data.get("publicationDate")
        external_ids = data.get("externalIds")
        formal_doi = external_ids.get("DOI") if isinstance(external_ids, dict) else None

        # 发表载体信息（比 venue 字符串更结构化）
        pub_venue = data.get("publicationVenue") or {}
        if not journal_name and isinstance(pub_venue, dict):
            journal_name = pub_venue.get("name")

        title = title if isinstance(title, str) else ""
        venue = venue if isinstance(venue, str) else None
        journal_name = journal_name if isinstance(journal_name, str) else None
        formal_doi = formal_doi if isinstance(formal_doi, str) else None
        pub_date = pub_date if isinstance(pub_date, str) else None

        # 判断是否已正式发表：
        # - 必须有一个不是预印本 DOI 本身的 DOI
        # - 必须有一个不是预印本服务器名称的载体/期刊
        is_preprint_doi = False
        if formal_doi:
            is_preprint_doi = formal_doi.startswith("10.48550/") or formal_doi.startswith(BIORXIV_DOI_PREFIX)

        preprint_venue_names = {"arxiv", "biorxiv", "medrxiv", "ssrn"}
        venue_is_preprint = False
        if venue and any(name in venue.lower() for name in preprint_venue_names):
            venue_is_preprint = True
        if journal_name and any(name in journal_name.lower() for name in preprint_venue_names):
            venue_is_preprint = True

        is_published = bool((venue or journal_name) and not venue_is_preprint and formal_doi and not is_preprint_doi)

        return PublicationStatus(
            preprint_id=info.preprint_id,
            source=info.source,
            title=title,
            is_published=is_published,
            venue=venue if venue else None,
            journal_name=journal_name,
            doi=formal_doi if formal_doi and not is_preprint_doi else None,
            publication_date=pub_date,
        )
