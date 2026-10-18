"""NDJSON streaming and structured progress events.

`stream_items` emits one JSON envelope per line plus a summary line so agents can
process long result sets incrementally. `emit_progress` writes one structured
event per stderr line so multi-minute work is visibly alive instead of silent.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from typing import Any

from zotero_cli.models import Item
from zotero_cli.output.envelope import base_meta, current_request_id, elapsed_ms


def emit_progress(
    event: str,
    *,
    phase: str = "",
    done: int | None = None,
    total: int | None = None,
    **extra: Any,
) -> None:
    """Emit a structured progress event on stderr, one JSON object per line.

    Agents read stderr to track liveness without blocking on the single final
    stdout envelope. Silent multi-minute waits become detectable because the
    absence of `progress` events is itself a signal.
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


def stream_items(items: list[Item], detail: str = "standard", *, total: int | None = None) -> str:
    """Emit items as NDJSON: one JSON envelope per line, then a summary line.

    Designed for agents processing long result sets incrementally: they can
    read one record, act on it, and keep going without loading the full
    response into memory. The final summary line carries the emitted count;
    has_more compares it with the total matching results when supplied.
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
        "meta": base_meta(),
    }
    lines.append(json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines)
