"""Stable JSON envelope contract: request scope, meta, and envelope builders.

Every machine-readable response `zot` emits is built here so agents can rely on
one shape: `{"ok": ..., "data"|"error": ..., "meta": ...}`. The request scope binds
`request_id` / latency to the current context, and `base_meta()` folds them into
`meta` without callers having to pass them around.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from zotero_cli import __version__

SCHEMA_VERSION = "1.2.0"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_request_id", default=None)
_request_start: contextvars.ContextVar[float | None] = contextvars.ContextVar("_request_start", default=None)


@contextmanager
def request_scope() -> Iterator[str]:
    """Bind a request_id and start time to the current context.

    Envelopes emitted inside this scope automatically carry request_id and
    latency_ms in meta, so commands don't have to pass them explicitly.
    """
    rid = uuid.uuid4().hex[:12]
    rid_tok = _request_id.set(rid)
    start_tok = _request_start.set(time.monotonic())
    try:
        yield rid
    finally:
        _request_id.reset(rid_tok)
        _request_start.reset(start_tok)


def current_request_id() -> str | None:
    """Return the request_id bound to the active request scope, if any."""
    return _request_id.get()


def elapsed_ms() -> int | None:
    """Return milliseconds elapsed in the active request scope, if any."""
    start = _request_start.get()
    if start is None:
        return None
    return int((time.monotonic() - start) * 1000)


def base_meta() -> dict[str, Any]:
    """Return the meta fields every envelope carries, including request context."""
    meta: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "cli_version": __version__}
    rid = current_request_id()
    if rid:
        meta["request_id"] = rid
    elapsed = elapsed_ms()
    if elapsed is not None:
        meta["latency_ms"] = elapsed
    return meta


def envelope_ok(data: Any, meta: dict | None = None, extra: dict | None = None) -> dict:
    env: dict[str, Any] = {"ok": True, "data": data}
    if extra:
        env.update(extra)
    env["meta"] = {**base_meta(), **(meta or {})}
    return env


def envelope_error(
    code: str,
    message: str,
    retryable: bool = False,
    **extra: Any,
) -> dict:
    err: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    for k, v in extra.items():
        if v is not None and v != "":
            err[k] = v
    return {
        "ok": False,
        "error": err,
        "meta": base_meta(),
    }


def envelope_partial(succeeded: list, failed: list, meta: dict | None = None) -> dict:
    """Partial-success envelope.

    Contract: ok is a tri-state value, not a boolean: True (success), False
    (error) or the string "partial" (some operations succeeded, some failed;
    details in data.succeeded / data.failed). Consumers must compare explicitly
    or treat anything other than True as not fully successful. The shape is
    pinned by tests/cli/test_contract.py.
    """
    return {
        "ok": "partial",
        "data": {"succeeded": succeeded, "failed": failed},
        "meta": {**base_meta(), **(meta or {})},
    }
