"""Zotero Web API 写入层（基于 pyzotero 1.16 / httpx2）。

ZoteroWriter 定义在此；错误转换在 writer_errors 中，Extra 字段的 short-note
合并逻辑在 short_note_merge 中。

删除语义：Zotero Web API 的 DELETE 会永久删除条目。回收站恢复路径
（原始 PATCH deleted=0）仅适用于被 Zotero Desktop 移入回收站的条目；
CLI 的删除操作不可撤销。
"""

from __future__ import annotations

from pathlib import Path

import httpx2
from pyzotero import zotero
from pyzotero.zotero_errors import PyZoteroError, ResourceNotFoundError

from zotero_cli.zotero.short_note_merge import merge_short_note_into_extra
from zotero_cli.zotero.writer_errors import (
    ZoteroWriteError,
    _friendly_api_error,
    error_for_http_status,
    response_retry_after,
)

SYNC_REMINDER = "Change saved. Run Zotero sync to update local database."

API_TIMEOUT = 30.0  # 秒


class ZoteroWriter:
    def __init__(self, library_id: str, api_key: str, library_type: str = "user", timeout: float = API_TIMEOUT) -> None:
        self._zot = zotero.Zotero(library_id, library_type, api_key)
        if self._zot.client is not None:
            # pyzotero 1.16 使用 httpx2 客户端，其 timeout 必须是
            # float 或 httpx2.Timeout；传入 httpx.Timeout 对象会在 httpcore
            # 深处报错 "'Timeout' object cannot be interpreted as an integer"。
            self._zot.client.timeout = float(timeout)

    # --- 基础设施 ---------------------------------------------------------

    @staticmethod
    def _network_error(exc: BaseException) -> ZoteroWriteError:
        return ZoteroWriteError(
            f"Network error: {exc}",
            code="network_error",
            retryable=True,
            committed=True,
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
        try:
            try:
                template = self._zot.item_template("note")
            except Exception:  # noqa: BLE001 - 退回到最小化注记模板
                template = {"itemType": "note", "note": "", "tags": [], "collections": [], "relations": {}}
            template["note"] = content
            template["parentItem"] = parent_key
            resp = self._zot.create_items([template])
            return self._check_response(resp)
        except ResourceNotFoundError:
            raise ZoteroWriteError(
                f"Parent item '{parent_key}' not found", code="not_found", retryable=False, committed=False
            )
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def update_short_note(self, key: str, short_note: str) -> None:
        """通过 Web API 把 short-note 行合并进条目的 Extra 字段。"""
        try:
            item = self._zot.item(key)
            current = item["data"].get("extra") or ""
            item["data"]["extra"] = merge_short_note_into_extra(current, short_note)
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e
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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_item(self, key: str) -> None:
        """通过 Web API 的 DELETE 端点永久删除条目。"""
        try:
            item = self._zot.item(key)
            self._zot.delete_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def remove_tags(self, key: str, tags: list[str]) -> None:
        """从条目中移除标签。"""
        try:
            item = self._zot.item(key)
            item["data"]["tags"] = [t for t in item["data"].get("tags", []) if t["tag"] not in tags]
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Item '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def add_to_collection(self, item_key: str, collection_key: str) -> None:
        """把条目加入集合，且不移除其已有的集合归属。"""
        try:
            self._zot.addto_collection(collection_key, self._zot.item(item_key))
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

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
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def set_item_collections(self, item_key: str, collection_keys: list[str]) -> None:
        """把条目的集合归属替换为恰好给定的这些键。"""
        try:
            item = self._zot.item(item_key)
            item["data"]["collections"] = sorted({str(key) for key in collection_keys if key})
            self._zot.update_item(item)
        except ResourceNotFoundError:
            raise ZoteroWriteError("Item or collection not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def delete_collection(self, key: str) -> None:
        """永久删除集合（其中的嵌套集合也会被删除）。"""
        try:
            coll = self._zot.collection(key)
            self._zot.delete_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e

    def rename_collection(self, key: str, new_name: str) -> None:
        """重命名集合，保留其父集合和其他字段。"""
        try:
            coll = self._zot.collection(key)
            coll["data"]["name"] = new_name
            self._zot.update_collection(coll)
        except ResourceNotFoundError:
            raise ZoteroWriteError(f"Collection '{key}' not found", code="not_found", retryable=False, committed=False)
        except httpx2.HTTPError as e:
            raise self._network_error(e) from e
        except PyZoteroError as e:
            raise _friendly_api_error(e) from e
