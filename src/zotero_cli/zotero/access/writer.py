"""基于 pyzotero 的 Zotero 远程写入封装。"""

from __future__ import annotations

import html
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx2
from pyzotero import zotero
from pyzotero.zotero_errors import PyZoteroError, ResourceNotFoundError

from zotero_cli.config.runtime import section
from zotero_cli.zotero.access.errors import (
    ZoteroWriteError,
    _friendly_api_error,
    error_for_http_status,
    response_retry_after,
)


def merge_short_note_into_extra(extra: str, short_note: str) -> str:
    """把 short-note 行合并进 Zotero Extra 文本，并替换已有的同名行。

    保留其他所有 Extra 行（资助方、译名等），并在末尾追加唯一一行
    "short-note: ..."。
    """
    lines = [line for line in (extra or "").splitlines() if not line.strip().lower().startswith("short-note:")]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(f"short-note: {short_note.strip()}")
    return "\n".join(lines)


SYNC_REMINDER = "Change saved. Run Zotero sync to update local database."


def __getattr__(name: str) -> Any:
    if name == "API_TIMEOUT":
        return float(section("zotero")["api_timeout_seconds"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ZoteroWriter:
    def __init__(
        self,
        library_id: str,
        api_key: str,
        library_type: str = "user",
        timeout: float | None = None,
    ) -> None:
        if timeout is None:
            timeout = float(section("zotero")["api_timeout_seconds"])
        self._zot = zotero.Zotero(library_id, library_type, api_key)
        if self._zot.client is not None:
            # pyzotero 1.16 使用 httpx2 客户端，其 timeout 必须是
            # float 或 httpx2.Timeout；传入 httpx.Timeout 对象会在 httpcore
            # 深处报错 "'Timeout' object cannot be interpreted as an integer"。
            self._zot.client.timeout = float(timeout)
        self.last_write_retries = 0

    # --- 基础设施 ---------------------------------------------------------

    @staticmethod
    def _network_error(exc: BaseException) -> ZoteroWriteError:
        return ZoteroWriteError(
            f"Network error: {exc}",
            code="network_error",
            retryable=True,
            committed=True,
            retry_after_seconds=response_retry_after(getattr(exc, "response", None)),
        )

    @staticmethod
    def _response_error(response: httpx2.Response) -> ZoteroWriteError:
        try:
            body = response.text.strip()[:300]
        except Exception:  # noqa: BLE001 - 响应体格式错误或已被删除
            body = ""
        return error_for_http_status(
            response.status_code,
            body,
            retry_after=response_retry_after(response),
        )

    def _item_url(self, key: str) -> str:
        return f"{self._zot.endpoint}/{self._zot.library_type}/{self._zot.library_id}/items/{key}"

    def _write_headers(self, *, version: int | None = None, json_body: bool = False) -> dict[str, str]:
        headers = dict(self._zot.default_headers())
        if version is not None:
            headers["If-Unmodified-Since-Version"] = str(version)
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _check_response(self, resp: dict) -> str:
        """从写入响应中返回新建条目的键，或抛出带类型的错误。

        Zotero 写入响应可能把新建对象放在 successful[0].key 下，把键映射放在
        success[0] 下，或把带键对象放在 unchanged[0] 下。
        pyzotero 1.16 会出现以上三种形式。
        """
        if not isinstance(resp, dict):
            raise ZoteroWriteError("Unexpected API response", code="api_error", retryable=True, committed=True)

        success_key: str | None = None
        successful = resp.get("successful") or {}
        if isinstance(successful, dict):
            entry = successful.get("0")
            if isinstance(entry, dict) and entry.get("key"):
                success_key = str(entry["key"])
        if success_key is None:
            success = resp.get("success") or {}
            if isinstance(success, dict):
                key = success.get("0")
                if isinstance(key, str) and key:
                    success_key = key
        if success_key is None:
            unchanged = resp.get("unchanged") or {}
            if isinstance(unchanged, dict):
                entry = unchanged.get("0")
                if isinstance(entry, dict) and entry.get("key"):
                    success_key = str(entry["key"])

        failed = resp.get("failed") or {}
        if isinstance(failed, dict) and failed:
            if success_key is not None:
                # 响应自相矛盾：写入可能已经生效，同时服务端又报告失败。
                # 将其视为结果未知，让调用方进行核对而不是盲目重试。
                raise ZoteroWriteError(
                    "Unexpected API response",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            failure = failed.get("0") or {}
            message = failure.get("message") if isinstance(failure, dict) else None
            raise ZoteroWriteError(
                f"API error: {message or 'Unknown API error'}",
                code="api_error",
                retryable=False,
                committed=False,
            )
        if success_key is not None:
            return success_key
        raise ZoteroWriteError("Unexpected API response", code="api_error", retryable=True, committed=True)

    # --- 注记与条目 --------------------------------------------------

    def add_note(self, parent_key: str, content: str) -> str:
        """在条目下创建子注记；返回新注记的键。"""
        self.last_write_retries = 0
        try:
            template = self._zot.item_template("note")
        except Exception:  # noqa: BLE001 - 退回到最小化注记模板
            template = {"itemType": "note", "note": "", "tags": [], "collections": [], "relations": {}}
        template["note"] = content
        template["parentItem"] = parent_key
        title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", content, flags=re.I | re.S)
        title = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1) if title_match else content)).strip()
        for attempt in range(3):
            try:
                return self._check_response(self._zot.create_items([template]))
            except ResourceNotFoundError as error:
                raise ZoteroWriteError(
                    f"Parent item '{parent_key}' not found", code="not_found", retryable=False, committed=False
                ) from error
            except (httpx2.HTTPError, PyZoteroError, ZoteroWriteError) as error:
                failure = (
                    self._network_error(error)
                    if isinstance(error, httpx2.HTTPError)
                    else _friendly_api_error(error)
                    if isinstance(error, PyZoteroError)
                    else error
                )
                if failure.retry_after_seconds is None:
                    failure.retry_after_seconds = response_retry_after(getattr(error, "response", None))
                if not failure.retryable or not failure.committed or attempt == 2:
                    raise failure from error
                # 503/超时后先查同标题子笔记，确认未落地才允许再次 POST。
                try:
                    children = self._zot.everything(self._zot.children(parent_key, limit=100))
                    for child in children:
                        body = child.get("data", {}).get("note", "")
                        match = re.search(r"<h1\b[^>]*>(.*?)</h1>", body, flags=re.I | re.S)
                        child_title = html.unescape(re.sub(r"<[^>]+>", "", match.group(1) if match else body)).strip()
                        if child.get("data", {}).get("itemType") == "note" and child_title == title:
                            return str(child["key"])
                except Exception as verification_error:
                    failure.committed = True
                    raise failure from verification_error
                delay = (
                    failure.retry_after_seconds
                    if failure.retry_after_seconds is not None
                    else 0.25 * 2**attempt + random.uniform(0, 0.1)
                )
                time.sleep(delay)
                self.last_write_retries += 1
        raise AssertionError("重试循环未返回")

    def add_item(
        self,
        doi: str | None = None,
        url: str | None = None,
        *,
        extra_fields: dict[str, object] | None = None,
    ) -> str:
        """根据 DOI 或 URL 创建 Zotero 条目。

        extra_fields 会在提交前合并进条目模板，供通过 Crossref 解析 DOI
        元数据的调用方使用。仅含 URL 的条目在调用方未提供标题时会把 URL
        作为标题，从而在任何回读或同步之前就能识别新条目。
        """
        if not doi and not url:
            raise ValueError("Either doi or url must be provided")
        try:
            if doi:
                template = self._zot.item_template("journalArticle")
                template["DOI"] = doi
                if extra_fields:
                    template.update(extra_fields)
            else:
                template = self._zot.item_template("webpage")
                template["url"] = url
                if extra_fields:
                    template.update(extra_fields)
                if not template.get("title"):
                    template["title"] = url
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def update_short_note(self, key: str, short_note: str) -> None:
        """通过 Web API 把 short-note 行合并进条目的 Extra 字段。"""
        self.last_write_retries = 0
        for attempt in range(3):
            write_started = False
            try:
                item = self._zot.item(key)
                current = item["data"].get("extra") or ""
                item["data"]["extra"] = merge_short_note_into_extra(current, short_note)
                write_started = True
                self._zot.update_item(item)
                return
            except ResourceNotFoundError as error:
                raise ZoteroWriteError(
                    f"Item '{key}' not found", code="not_found", retryable=False, committed=False
                ) from error
            except (httpx2.HTTPError, PyZoteroError) as error:
                failure = (
                    self._network_error(error) if isinstance(error, httpx2.HTTPError) else _friendly_api_error(error)
                )
                if failure.retry_after_seconds is None:
                    failure.retry_after_seconds = response_retry_after(getattr(error, "response", None))
                # 读请求失败尚未发出 PATCH，可安全重试；发送后失败才需要远端核对。
                if not write_started:
                    failure.committed = False
                    failure.definitely_not_sent = True
                if not failure.retryable or attempt == 2:
                    raise failure from error
                if write_started:
                    # Extra 回读已经包含目标行时，停止重发以免覆盖并发更新。
                    try:
                        remote = self._zot.item(key)
                        if f"short-note: {short_note.strip()}" in (remote["data"].get("extra") or "").splitlines():
                            return
                    except Exception as verification_error:
                        failure.committed = True
                        raise failure from verification_error
                delay = (
                    failure.retry_after_seconds
                    if failure.retry_after_seconds is not None
                    else 0.25 * 2**attempt + random.uniform(0, 0.1)
                )
                time.sleep(delay)
                self.last_write_retries += 1

    def update_item(self, key: str, fields: dict[str, str], *, expected_version: int | None = None) -> None:
        """更新字段；可选地在派生负载准备好之后拒绝已发生的变更。"""
        try:
            item = self._zot.item(key)
            if expected_version is not None and item.get("version") != expected_version:
                raise ZoteroWriteError(
                    f"Item '{key}' changed since it was read; refusing a stale update",
                    code="conflict",
                    retryable=False,
                    committed=False,
                )
            for field_name, value in fields.items():
                item["data"][field_name] = value
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def restore_from_trash(self, key: str) -> None:
        """通过 PATCH deleted=0 恢复被 Zotero Desktop 移入回收站的条目。

        pyzotero 1.16 的 update_item 会拒绝只读的 deleted 字段，因此改用
        原始 Web API PATCH，并以条目版本作为前置条件。该操作仅对仍存在于
        回收站中的条目有效；CLI 的删除是永久的，无法恢复。
        """
        try:
            item = self._zot.item(key)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error
        version = item.get("version")
        if not isinstance(version, int):
            raise ZoteroWriteError(
                f"Item '{key}' has no version; refusing an unguarded restore",
                code="conflict",
                retryable=False,
                committed=False,
            )
        client = self._zot.client
        if client is None:
            raise ZoteroWriteError("Zotero client is not initialized", code="api_error", retryable=True, committed=True)
        try:
            response = client.patch(
                self._item_url(key),
                headers=self._write_headers(version=version, json_body=True),
                json={"deleted": 0},
            )
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        if response.status_code >= 300:
            raise self._response_error(response)

    def upload_attachment(self, parent_key: str, file_path: Path) -> str:
        """向已有条目上传文件附件。返回附件键。"""
        if not file_path.exists():
            raise ZoteroWriteError(
                f"File not found: {file_path}", code="validation_error", retryable=False, committed=False
            )
        try:
            resp = self._zot.attachment_simple([str(file_path)], parentid=parent_key)
            if not isinstance(resp, dict):
                raise ZoteroWriteError(
                    "Unexpected empty response from attachment upload",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            for field in ("success", "unchanged"):
                entries = resp.get(field) or []
                if entries:
                    key = entries[0].get("key")
                    if key:
                        return str(key)
            failures = resp.get("failure")
            if isinstance(failures, list) and failures:
                failure = failures[0] if isinstance(failures[0], dict) else {}
                reason = failure.get("error")
                if isinstance(reason, dict) and reason.get("message"):
                    raise ZoteroWriteError(
                        f"Attachment upload failed: {reason['message']}",
                        code="api_error",
                        retryable=False,
                        committed=False,
                    )
                raise ZoteroWriteError(
                    f"Attachment upload failed: {failure.get('message', 'Upload failed')}",
                    code="api_error",
                    retryable=True,
                    committed=True,
                )
            raise ZoteroWriteError(
                "Unexpected empty response from attachment upload",
                code="api_error",
                retryable=True,
                committed=True,
            )
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def delete_item(self, key: str) -> None:
        """通过 Web API 的 DELETE 端点永久删除条目。"""
        try:
            item = self._zot.item(key)
            self._zot.delete_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    # --- 发布回读 --------------------------------------------------------

    def read_item(self, key: str) -> dict:
        """读取条目快照，用于确认写入后的远端状态。"""
        try:
            return self._zot.item(key)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def read_child_notes(self, parent_key: str, limit: int = 100) -> list[dict]:
        """按父条目读取远端子注记，返回原始条目数据。"""
        try:
            return list(self._zot.children(parent_key, itemType="note", limit=limit))
        except ResourceNotFoundError:
            raise ZoteroWriteError(
                f"Parent item '{parent_key}' not found", code="not_found", retryable=False, committed=False
            )
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def add_tags(self, key: str, tags: list[str]) -> None:
        """为条目添加标签，并保留已有的标签名。"""
        try:
            item = self._zot.item(key)
            existing = [t["tag"] for t in item["data"].get("tags", [])]
            new_tags = [{"tag": t} for t in set(existing + tags)]
            item["data"]["tags"] = new_tags
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def remove_tags(self, key: str, tags: list[str]) -> None:
        """从条目中移除标签。"""
        try:
            item = self._zot.item(key)
            item["data"]["tags"] = [t for t in item["data"].get("tags", []) if t["tag"] not in tags]
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    # --- 集合 ------------------------------------------------------

    def create_collection(self, name: str, parent_key: str | None = None) -> str:
        """创建集合，可选地嵌套在父集合键之下。"""
        try:
            payload = [{"name": name, "parentCollection": parent_key or False}]
            resp = self._zot.create_collections(payload)
            return self._check_response(resp)
        except ResourceNotFoundError:
            raise ZoteroWriteError(
                f"Parent collection '{parent_key}' not found", code="not_found", retryable=False, committed=False
            )
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def add_to_collection(self, item_key: str, collection_key: str) -> None:
        """把条目加入集合，且不移除其已有的集合归属。"""
        try:
            self._zot.addto_collection(collection_key, self._zot.item(item_key))
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def move_to_collection(self, item_key: str, collection_key: str, source_collection_key: str | None = None) -> None:
        """把条目移动到集合。

        未提供 source_collection_key 时保持历史行为：把条目加入目标集合，
        并保留所有已有的集合归属。提供源集合时，更新条目的集合归属列表，
        使源集合被移除、目标集合被加入。
        """
        if source_collection_key is None:
            self.add_to_collection(item_key, collection_key)
            return
        try:
            item = self._zot.item(item_key)
            data = item.setdefault("data", {})
            collections = [str(c) for c in data.get("collections", []) if c]
            if source_collection_key not in collections:
                raise ZoteroWriteError(
                    f"Item '{item_key}' is not in source collection '{source_collection_key}'",
                    code="validation_error",
                    retryable=False,
                    committed=False,
                )
            new_collections = [c for c in collections if c != source_collection_key]
            if collection_key not in new_collections:
                new_collections.append(collection_key)
            data["collections"] = new_collections
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def set_item_collections(self, item_key: str, collection_keys: list[str]) -> None:
        """把条目的集合归属替换为恰好给定的这些键。"""
        try:
            item = self._zot.item(item_key)
            item["data"]["collections"] = sorted({str(key) for key in collection_keys if key})
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def delete_collection(self, key: str) -> None:
        """永久删除集合（其中的嵌套集合也会被删除）。"""
        try:
            coll = self._zot.collection(key)
            self._zot.delete_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error

    def rename_collection(self, key: str, new_name: str) -> None:
        """重命名集合，保留其父集合和其他字段。"""
        try:
            coll = self._zot.collection(key)
            coll["data"]["name"] = new_name
            self._zot.update_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as error:
            raise self._network_error(error) from error
        except PyZoteroError as error:
            raise _friendly_api_error(error) from error
