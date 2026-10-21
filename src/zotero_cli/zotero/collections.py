"""集合重组计划的输入验证与依赖排序。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zotero_cli.zotero.writer import ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def validate_reorganization_plan(plan: Any) -> list[dict[str, Any]]:
    """验证整个计划并将父集合排在子集合之前。"""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    collections = plan.get("collections", [])
    if not isinstance(collections, list):
        raise ValueError("collections must be a list")

    parents: dict[str, str | None] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row, coll in enumerate(collections, 1):
        if not isinstance(coll, dict):
            raise ValueError(f"collections[{row}] must be an object")
        name = coll.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"collections[{row}] requires a non-empty string name")
        if name in parents:
            raise ValueError(f"Duplicate collection name '{name}' in plan")
        parent_name = coll.get("parent")
        if parent_name is not None and (not isinstance(parent_name, str) or not parent_name.strip()):
            raise ValueError(f"collections[{row}].parent must be a non-empty collection name or null")
        parents[name] = parent_name
        by_name[name] = coll
        items = coll.get("items", [])
        if not isinstance(items, list) or any(
            not isinstance(key, str) or not key.isascii() or not key.isalnum() for key in items
        ):
            raise ValueError(f"collections[{row}].items must be a list of non-empty alphanumeric item keys")
    for name, parent_name in parents.items():
        if parent_name is not None and parent_name not in parents:
            raise ValueError(f"Unknown parent '{parent_name}' for collection '{name}'")

    ordered: list[dict[str, Any]] = []
    scheduled: set[str] = set()
    for name in parents:
        pending: list[str] = []
        visiting: set[str] = set()
        current: str | None = name
        while current is not None and current not in scheduled:
            if current in visiting:
                raise ValueError(f"Collection parent cycle involving '{current}'")
            visiting.add(current)
            pending.append(current)
            current = parents[current]
        for current in reversed(pending):
            ordered.append(by_name[current])
            scheduled.add(current)
    return ordered


def execute_reorganization(
    writer: ZoteroWriter,
    collections: list[dict[str, Any]],
    report: Callable[[str, bool], None],
) -> tuple[dict[str, Any], int]:
    """顺序创建集合，并对每个条目只执行一次归属替换。"""
    created_collections: dict[str, str] = {}  # 用名称索引已创建集合的键
    created_results: list[dict[str, Any]] = []
    moved_results: list[dict[str, Any]] = []
    failed_results: list[dict[str, Any]] = []

    for coll in collections:
        name = coll["name"]
        parent_name = coll.get("parent")
        parent_key = created_collections.get(parent_name) if parent_name else None
        try:
            if parent_name is not None and parent_key is None:
                raise ZoteroWriteError(
                    f"Parent collection '{parent_name}' was not created; skipping '{name}'",
                    code="validation_error",
                    retryable=False,
                    committed=False,
                )
            col_key = writer.create_collection(name, parent_key=parent_key)
        except ZoteroWriteError as e:
            failed_results.append(
                {
                    "operation": "create_collection",
                    "name": name,
                    "parent_name": parent_name,
                    "parent_key": parent_key,
                    "code": e.code,
                    "message": str(e),
                    "committed": e.committed,
                }
            )
            report(f"Failed to create collection '{name}': {e}", True)
            continue
        created_collections[name] = col_key
        created_results.append({"name": name, "key": col_key, "parent_name": parent_name, "parent_key": parent_key})
        report(f"Created collection '{name}' ({col_key})", False)

    # 先汇总每个条目的全部目标集合，再以单次写入替换原有归属。
    targets: dict[str, list[tuple[str, str]]] = {}
    for coll in collections:
        name = coll["name"]
        created_key = created_collections.get(name)
        if not created_key:
            continue
        for item_key in coll.get("items", []):
            targets.setdefault(item_key, []).append((name, created_key))

    for item_key in sorted(targets):
        entries = targets[item_key]
        target_keys = [col_key for _name, col_key in entries]
        try:
            writer.set_item_collections(item_key, target_keys)
        except ZoteroWriteError as e:
            failed_results.append(
                {
                    "operation": "move_item",
                    "item_key": item_key,
                    "collection_names": [name for name, _key in entries],
                    "collection_keys": target_keys,
                    "code": e.code,
                    "message": str(e),
                    "committed": e.committed,
                }
            )
            report(f"Failed to move {item_key}: {e}", True)
            continue
        for name, col_key in entries:
            moved_results.append(
                {
                    "item_key": item_key,
                    "collection_name": name,
                    "collection_key": col_key,
                    "replaced_existing_memberships": True,
                }
            )
        names = ", ".join(f"'{name}'" for name, _key in entries)
        report(f"  Moved {item_key} -> {names} (replaced other memberships)", False)

    summary = {
        "collections_requested": len(collections),
        "collections_created": len(created_collections),
        "items_moved": len(moved_results),
        "failures": len(failed_results),
    }
    data = {
        "created_collections": created_results,
        "items_moved": moved_results,
        "failed": failed_results,
        "summary": summary,
        "replaced_existing_memberships": True,
        "sync_required": True,
    }
    return data, len(targets)
