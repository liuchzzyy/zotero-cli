"""通过 Crossref 将 DOI 解析为 Zotero 元数据。

Zotero Web API 的 `create_items` 端点不会自动把 DOI 解析成完整元数据
（这由 Zotero 桌面端的转换器完成）。当 `zot zotero add --doi` 提交一个
不带元数据的条目时，得到的结果是仅设置 DOI 字段的空条目。本模块填补这一空白：
根据 DOI 获取 Crossref 记录，并映射到 Zotero 的 `journalArticle` 字段集，
使 API 创建的条目已带有 title/creators/journal 等字段。

Crossref 免费、无需密钥，并为通过 User-Agent 表明身份的客户端提供
"polite pool"（礼貌池）。在环境中设置 `ZOT_CROSSREF_MAILTO`
即可被路由到该礼貌池。
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import httpx

from zotero_cli.config import load_config, resolve_crossref_mailto

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
