"""条目元数据、出版状态、批量写入、删除与恢复服务。"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from urllib.parse import quote

import httpx

from zotero_cli.config import load_config, resolve_crossref_mailto
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.idempotency import failure_definitely_rejected
from zotero_cli.zotero.access.writer import ZoteroWriter

CROSSREF_API_BASE = "https://api.crossref.org/works"


REQUEST_TIMEOUT = 15.0


USER_AGENT_BASE = "zotero-cli (https://github.com/liuchzzyy/zotero-cli)"


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


MAX_ATTEMPTS = 3


RETRY_BACKOFF_SECONDS = 0.5


class MetadataResolveError(Exception):
    """无法访问 Crossref 或返回无法解析的响应时抛出。

    404（Crossref 不认识该 DOI）不算错误——调用方会收到 None。
    """


def _user_agent() -> str:
    mailto = resolve_crossref_mailto(load_config())
    if mailto:
        return f"{USER_AGENT_BASE} (mailto:{mailto})"
    return USER_AGENT_BASE


_JATS_TAG_RE = re.compile(r"<[^>]+>")


def _strip_jats(text: str) -> str:
    """Crossref 摘要常以 JATS XML 形式返回——需展平为纯文本。"""
    cleaned = _JATS_TAG_RE.sub("", text)
    # 压缩去除标签后产生的连续空白。
    return re.sub(r"\s+", " ", cleaned).strip()


def _format_date(date_parts: list[list[int]] | None) -> str | None:
    """把 Crossref date-parts 转换为 Zotero 可接受的近似 ISO 字符串（YYYY、YYYY-MM、YYYY-MM-DD）。"""
    if not date_parts:
        return None
    parts = date_parts[0] if isinstance(date_parts[0], list) else date_parts
    if not parts:
        return None
    formatted: list[str] = []
    for i, part in enumerate(parts):
        if part is None:
            break
        try:
            value = int(part)
        except (TypeError, ValueError):
            return None
        formatted.append(f"{value:02d}" if i > 0 else str(value))
    if not formatted:
        return None
    return "-".join(formatted)


def _map_creators(authors: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if not authors:
        return []
    creators: list[dict[str, str]] = []
    for a in authors:
        given = (a.get("given") or "").strip()
        family = (a.get("family") or "").strip()
        if family or given:
            creators.append({"creatorType": "author", "firstName": given, "lastName": family})
            continue
        name = (a.get("name") or "").strip()
        if name:
            # 机构作者或单名作者（例如联盟）。
            creators.append({"creatorType": "author", "name": name})
    return creators


def _first_str(value: Any) -> str | None:
    """Crossref 字符串字段通常是单元素数组——返回第一个非空条目。"""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
        return None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def map_crossref_to_zotero(message: dict[str, Any]) -> dict[str, Any]:
    """把 Crossref 的 `message` 对象映射为 Zotero `journalArticle` 字段字典。

    只输出 Crossref 中实际存在的字段，因此调用方可以安全地把结果合并到
    Zotero 条目模板之上，而不会用空字符串覆盖模板默认值。
    """
    fields: dict[str, Any] = {}

    title = _first_str(message.get("title"))
    if title:
        fields["title"] = title

    creators = _map_creators(message.get("author"))
    if creators:
        fields["creators"] = creators

    container = _first_str(message.get("container-title"))
    if container:
        fields["publicationTitle"] = container

    short_container = _first_str(message.get("short-container-title"))
    if short_container and short_container != container:
        fields["journalAbbreviation"] = short_container

    for crossref_key, zotero_key in (("volume", "volume"), ("issue", "issue"), ("page", "pages")):
        val = message.get(crossref_key)
        if isinstance(val, str) and val.strip():
            fields[zotero_key] = val.strip()

    # 规范出版日期优先取 print，其次 online，最后 issued。
    date = None
    for date_key in ("published-print", "published-online", "issued"):
        date_data = message.get(date_key)
        if isinstance(date_data, dict):
            date = _format_date(date_data.get("date-parts"))
            if date:
                break
    if date:
        fields["date"] = date

    issn = _first_str(message.get("ISSN"))
    if issn:
        fields["ISSN"] = issn

    publisher = _first_str(message.get("publisher"))
    if publisher:
        fields["publisher"] = publisher

    language = _first_str(message.get("language"))
    if language:
        fields["language"] = language

    abstract = message.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        fields["abstractNote"] = _strip_jats(abstract)

    url = _first_str(message.get("URL"))
    if url:
        fields["url"] = url

    doi = _first_str(message.get("DOI"))
    if doi:
        fields["DOI"] = doi

    return fields


def _get_with_retries(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
    """对 Crossref URL 发起 GET 请求，并对暂时性故障进行有限次重试。"""
    last_error: str = "Crossref request failed"
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError as exc:
            last_error = f"Crossref request failed: {exc}"
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            raise MetadataResolveError(last_error) from exc
        if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            last_error = f"Crossref returned HTTP {response.status_code}"
            continue
        return response
    raise MetadataResolveError(last_error)


def resolve_doi(doi: str, *, timeout: float = REQUEST_TIMEOUT) -> dict[str, Any] | None:
    """在 Crossref 中查询 DOI，并返回 Zotero 形式的元数据。

    返回：
        - 成功时为 Zotero `journalArticle` 字段字典（title、creators 等）。
        - Crossref 返回 404（DOI 确实不存在）时为 None。

    异常：
        网络故障、超时、5xx、JSON 格式错误或其他意外响应时抛出
        MetadataResolveError。调用方应将其视为“无法获取元数据——退回到
        不带元数据的条目”。
    """
    doi = doi.strip()
    if not doi:
        raise MetadataResolveError("Empty DOI")

    encoded_doi = quote(doi, safe="/")
    url = f"{CROSSREF_API_BASE}/{encoded_doi}"
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    resp = _get_with_retries(url, headers=headers, timeout=timeout)

    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise MetadataResolveError(f"Crossref returned HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as error:
        raise MetadataResolveError(f"Crossref returned invalid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise MetadataResolveError("Crossref response must be an object")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise MetadataResolveError("Crossref response missing 'message' object")
    return map_crossref_to_zotero(message)


API_BASE = "https://api.semanticscholar.org/graph/v1"


FIELDS = "externalIds,journal,venue,publicationDate,title,publicationVenue"


RATE_DELAY_NO_KEY = 3.0  # 无 API 密钥时请求之间的间隔秒数


RATE_DELAY_WITH_KEY = 1.0  # 有 API 密钥时请求之间的间隔秒数


SEMANTIC_SCHOLAR_TIMEOUT = 15.0


MAX_RETRY_WAIT = 60.0


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
        self._client = httpx.Client(timeout=SEMANTIC_SCHOLAR_TIMEOUT, headers=headers)

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


def resolve_item_metadata(doi: str, *, resolver: Callable = resolve_doi) -> tuple[dict | None, str | None]:
    """解析 DOI 元数据；查询失败时返回警告，不中断条目创建以避免重复写入。"""
    try:
        fields = resolver(doi)
    except MetadataResolveError as error:
        return None, str(error)
    if fields is None:
        return None, "no_match"
    return fields, None


def summarize_item_metadata(fields: dict) -> dict:
    """提取适合展示的元数据摘要。"""
    summary: dict = {}
    if "title" in fields:
        summary["title"] = fields["title"]
    creators = fields.get("creators") or []
    if creators:
        first = creators[0]
        last = first.get("lastName") or first.get("name") or ""
        suffix = " et al." if len(creators) > 1 else ""
        summary["author"] = f"{last}{suffix}".strip()
    if "publicationTitle" in fields:
        summary["journal"] = fields["publicationTitle"]
    if "date" in fields:
        summary["date"] = fields["date"]
    return summary


def import_items(
    writer: ZoteroWriter,
    lines: list[str],
    *,
    resolve: bool,
    metadata_resolver: Callable,
    progress: Callable[[int, int], None],
    report: Callable[[str], None],
) -> tuple[list[dict], list[dict]]:
    """逐行导入 DOI 或 URL，保留每项的成功与失败结果。"""
    succeeded: list[dict] = []
    failed: list[dict] = []
    for i, entry in enumerate(lines, 1):
        progress(i - 1, len(lines))
        is_doi = not entry.startswith("http")
        try:
            if is_doi:
                extra_fields: dict | None = None
                resolved_summary: dict | None = None
                resolve_warning: str | None = None
                if resolve:
                    extra_fields, resolve_warning = resolve_item_metadata(entry, resolver=metadata_resolver)
                    if extra_fields:
                        resolved_summary = summarize_item_metadata(extra_fields)
                key = writer.add_item(doi=entry, extra_fields=extra_fields)
                row: dict = {"entry": entry, "key": key}
                if resolved_summary is not None:
                    row["resolved"] = resolved_summary
                elif resolve:
                    row["resolved"] = None
                    row["resolve_warning"] = resolve_warning
                succeeded.append(row)
            else:
                key = writer.add_item(url=entry)
                succeeded.append({"entry": entry, "key": key})
            report(f"  [{i}/{len(lines)}] Added: {key} ({entry})")
        except ZoteroWriteError as error:
            failed.append(
                {
                    "entry": entry,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "retryable": error.retryable,
                        "committed": error.committed,
                    },
                }
            )
            report(f"  [{i}/{len(lines)}] Failed: {entry} ({error})")

    return succeeded, failed


if TYPE_CHECKING:
    from zotero_cli.zotero.access.writer import ZoteroWriter


class BatchUpdateRow(TypedDict):
    line: int
    key: str
    fields: dict[str, str]


class UpdateInputError(ValueError):
    """条目更新输入不合法。"""


def _parse_inline_fields(
    *,
    title: str | None,
    date: str | None,
    field: tuple[str, ...],
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if title:
        fields["title"] = title
    if date:
        fields["date"] = date
    for f in field:
        if "=" not in f:
            raise UpdateInputError(f"Invalid field format: '{f}'")
        k, v = f.split("=", 1)
        fields[k] = v
    return fields


def _load_updates_from_jsonl(file_path: Path) -> list[BatchUpdateRow]:
    try:
        raw_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise UpdateInputError(f"Could not read JSONL file '{file_path}': {error}")

    updates: list[BatchUpdateRow] = []
    for line_no, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise UpdateInputError(f"Invalid JSON on line {line_no} of '{file_path}': {error.msg}")
        if not isinstance(payload, dict):
            raise UpdateInputError(f"Line {line_no} of '{file_path}' must be a JSON object")

        key = payload.get("key")
        fields = payload.get("fields")
        if not isinstance(key, str) or not key.strip():
            raise UpdateInputError(f"Line {line_no} of '{file_path}' is missing a non-empty string 'key'")
        if not isinstance(fields, dict) or not fields:
            raise UpdateInputError(f"Line {line_no} of '{file_path}' must contain a non-empty object 'fields'")

        clean_fields: dict[str, str] = {}
        for field_name, field_value in fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                raise UpdateInputError(f"Line {line_no} of '{file_path}' contains an invalid field name")
            if not isinstance(field_value, str):
                raise UpdateInputError(
                    f"Line {line_no} of '{file_path}' field '{field_name}' must be a string in batch mode"
                )
            clean_fields[field_name] = field_value

        updates.append({"line": line_no, "key": key, "fields": clean_fields})

    if not updates:
        raise UpdateInputError(f"JSONL file '{file_path}' is empty or has no valid update rows")

    return updates


def update_item_with_tags(
    writer: ZoteroWriter,
    key: str,
    fields: dict[str, str],
    *,
    tags: list[str] | tuple[str, ...] = (),
) -> None:
    """顺序更新条目字段并在成功后追加标签。

    将「写字段 + 打标签」这一常见组合封装为单一领域操作，
    字段更新失败时绝不打标签；任一调用失败都会抛出 ZoteroWriteError。
    """
    writer.update_item(key, fields)
    if tags:
        try:
            writer.add_tags(key, list(tags))
        except ZoteroWriteError as error:
            raise ZoteroWriteError(
                str(error),
                code=error.code,
                retryable=False,
                committed=True,
                retry_after_seconds=error.retry_after_seconds,
            ) from error


def update_items(
    writer: ZoteroWriter,
    updates: list[BatchUpdateRow],
    *,
    add_tags: list[str],
    progress: Callable[[int, int], None],
    report: Callable[[str], None],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """依次更新条目与标签，逐行收集结果而不中断后续条目。"""
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for index, row in enumerate(updates, 1):
        progress(index - 1, len(updates))
        row_key = row["key"]
        row_fields = row["fields"]
        row_line = row["line"]
        try:
            update_item_with_tags(writer, row_key, row_fields, tags=add_tags)
            succeeded.append({"line": row_line, "key": row_key, "fields": row_fields, "tags_added": list(add_tags)})
            report(f"  [{index}/{len(updates)}] Updated: {row_key}")
        except ZoteroWriteError as error:
            failed.append(
                {
                    "line": row_line,
                    "key": row_key,
                    "fields": row_fields,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "retryable": error.retryable,
                        "committed": error.committed,
                    },
                }
            )
            report(f"  [{index}/{len(updates)}] Failed: {row_key} ({error})")
    return succeeded, failed


ReportItemDeletion = Callable[[dict[str, Any], bool], None]


@dataclass(frozen=True)
class ItemDeletionResult:
    """批量永久删除的成功、失败及确定拒绝次数。"""

    deleted: list[str]
    failed: list[dict[str, Any]]
    rejected_count: int


def delete_items(
    writer: ZoteroWriter,
    keys: list[str],
    *,
    idempotency_key: str | None = None,
    report: ReportItemDeletion | None = None,
) -> ItemDeletionResult:
    """按输入顺序永久删除条目，并标记可安全重试的拒绝。"""
    deleted: list[str] = []
    failed: list[dict[str, Any]] = []
    rejected_count = 0
    for key in keys:
        try:
            writer.delete_item(key)
            deleted.append(key)
            if report:
                report({"key": key}, False)
        except ZoteroWriteError as error:
            rejected = False
            if idempotency_key:
                rejected = failure_definitely_rejected(
                    response=writer._zot.request,
                    code=error.code,
                    retryable=error.retryable,
                    committed=error.committed,
                )
                if rejected:
                    rejected_count += 1
            row = {
                "key": key,
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "committed": error.committed,
                "definitely_rejected": rejected or not error.committed,
            }
            failed.append(row)
            if report:
                report(row, True)
    return ItemDeletionResult(deleted=deleted, failed=failed, rejected_count=rejected_count)


ReportTrashRestore = Callable[[dict[str, Any], bool], None]


@dataclass(frozen=True)
class TrashRestoreResult:
    """批量恢复的成功键和失败详情。"""

    restored: list[str]
    failed: list[dict[str, Any]]


def restore_items(
    writer: ZoteroWriter,
    keys: list[str],
    *,
    report: ReportTrashRestore | None = None,
) -> TrashRestoreResult:
    """按输入顺序恢复回收站条目。"""
    restored: list[str] = []
    failed: list[dict[str, Any]] = []
    for key in keys:
        try:
            writer.restore_from_trash(key)
            restored.append(key)
            if report:
                report({"key": key}, False)
        except ZoteroWriteError as error:
            row = {
                "key": key,
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
                "committed": error.committed,
            }
            failed.append(row)
            if report:
                report(row, True)
    return TrashRestoreResult(restored=restored, failed=failed)
