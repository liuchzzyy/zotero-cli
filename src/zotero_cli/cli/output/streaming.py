"""NDJSON 流式输出与结构化进度事件。

`stream_items` 每行输出一个 JSON envelope，并追加一行摘要，使代理能够
增量处理长结果集。`emit_progress` 在 stderr 上每行写一个结构化事件，
让耗时数分钟的工作可见地保持活跃，而不是静默。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from zotero_cli.cli.output.envelope import base_meta, current_request_id, elapsed_ms
from zotero_cli.zotero.models import Item


def emit_progress(
    event: str,
    *,
    phase: str = "",
    done: int | None = None,
    total: int | None = None,
    **extra: Any,
) -> None:
    """在 stderr 上输出结构化进度事件，每行一个 JSON 对象。

    代理可读取 stderr 跟踪活跃状态，而无需阻塞等待最终那个 stdout envelope。
    静默的数分钟等待变得可检测，因为缺少 `progress` 事件本身就是一种信号。
    """
    payload: dict[str, Any] = {"event": event}
    rid = current_request_id()
    if rid:
        payload["request_id"] = rid
    elapsed = elapsed_ms()
    if elapsed is not None:
        payload["elapsed_ms"] = elapsed
    if phase:
        payload["phase"] = phase
    if done is not None:
        payload["done"] = done
    if total is not None:
        payload["total"] = total
    for k, v in extra.items():
        if v is not None:
            payload[k] = v
    sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def stream_items(
    items: list[Item],
    detail: str = "standard",
    *,
    total: int | None = None,
    meta: dict | None = None,
) -> str:
    """以 NDJSON 输出条目：每行一个 JSON envelope，最后一行摘要。

    面向增量处理长结果集的代理：它们可以读取一条记录、对其处理并继续，
    而无需将完整响应载入内存。最后的摘要行携带已输出数量；当提供 total 时，
    has_more 会将其与匹配结果总数比较。
    """
    lines: list[str] = []
    for item in items:
        if detail == "minimal":
            minimal_keys = {"key", "item_type", "title", "creators", "date"}
            payload = {k: v for k, v in asdict(item).items() if k in minimal_keys}
        else:
            payload = asdict(item)
        lines.append(json.dumps({"ok": True, "data": payload}, ensure_ascii=False))
    summary = {
        "ok": True,
        "summary": {"count": len(items), "has_more": total is not None and total > len(items)},
        "meta": {**base_meta(), **(meta or {})},
    }
    lines.append(json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines)
