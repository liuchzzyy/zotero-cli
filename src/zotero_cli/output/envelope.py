"""Stable JSON envelope contract: request scope, meta, and envelope builders.

Every machine-readable response zot emits is built here so agents can rely on
one shape. Documented top-level keys:

* ok: True (success), False (error) or "partial" (some operations succeeded,
  some failed). Consumers must treat this as tri-state, not a boolean.
* data: present on success and partial envelopes.
* error: present on error envelopes, with code/message/retryable (+hint/context).
* meta: schema_version, cli_version, request_id/latency_ms in a request scope,
  plus per-command fields (e.g. count, detail).
* dry_run: optional True marker for preview-only commands.
* next: optional non-empty list of safe follow-up command strings, only on
  success/partial envelopes. Agents may run them as-is; they must never be the
  only way to understand the result, and they never imply immediate local
  visibility for remote writes (local reads can lag until Zotero Desktop syncs).

No other top-level keys are part of the contract. The request scope binds
request_id/latency to the current context, and base_meta() folds them into meta
without callers having to pass them around.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from zotero_cli import __version__

SCHEMA_VERSION = "1.2.0"

# Top-level extras accepted by the compatibility shim; both are official keys.
_ALLOWED_EXTRAS = frozenset({"dry_run", "next"})

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


def _clean_next(next: Sequence[str] | None) -> list[str] | None:
    """Validate the official optional 'next' hint list (None when empty)."""
    if next is None:
        return None
    if isinstance(next, str) or not isinstance(next, (list, tuple)):
        raise ValueError("envelope 'next' must be a list of command strings")
    cleaned: list[str] = []
    for hint in next:
        if not isinstance(hint, str) or not hint.strip():
            raise ValueError("envelope 'next' entries must be non-empty strings")
        cleaned.append(hint)
    return cleaned or None


def _merge_legacy_extra(
    extra: dict[str, Any] | None,
    next: Sequence[str] | None,
    dry_run: bool,
) -> tuple[Sequence[str] | None, bool]:
    """Accept the legacy extra={...} shim for the two documented top-level extras."""
    if not extra:
        return next, dry_run
    unknown = set(extra) - _ALLOWED_EXTRAS
    if unknown:
        raise ValueError(f"undocumented envelope extra keys: {sorted(unknown)}")
    if extra.get("dry_run"):
        dry_run = True
    if next is None:
        next = extra.get("next")
    return next, dry_run


def _finalize(
    env: dict[str, Any],
    *,
    meta: dict | None,
    next: Sequence[str] | None,
    dry_run: bool,
) -> dict:
    if dry_run:
        env["dry_run"] = True
    cleaned = _clean_next(next)
    if cleaned:
        env["next"] = cleaned
    env["meta"] = {**base_meta(), **(meta or {})}
    return env


def envelope_ok(
    data: Any,
    meta: dict | None = None,
    extra: dict | None = None,
    *,
    next: Sequence[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Build a success envelope (ok=True).

    next: optional safe follow-up commands (non-empty strings); emitted as the
    top-level next list only when non-empty. extra is a compatibility shim
    accepting only the documented dry_run/next keys; new code should pass
    dry_run=True / next=[...] directly.
    """
    next, dry_run = _merge_legacy_extra(extra, next, dry_run)
    return _finalize({"ok": True, "data": data}, meta=meta, next=next, dry_run=dry_run)


def envelope_error(
    code: str,
    message: str,
    retryable: bool = False,
    **extra: Any,
) -> dict:
    """Build an error envelope (ok=False); 'next' is rejected here by contract."""
    if "next" in extra:
        raise ValueError("'next' is only allowed on success/partial envelopes")
    err: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    for k, v in extra.items():
        if v is not None and v != "":
            err[k] = v
    return {
        "ok": False,
        "error": err,
        "meta": base_meta(),
    }


def envelope_partial(
    succeeded: list,
    failed: list,
    meta: dict | None = None,
    *,
    next: Sequence[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Partial-success envelope with the standard succeeded/failed lists.

    ok is the tri-state string "partial". Details live in
    data.succeeded / data.failed. Consumers must compare explicitly or treat
    anything other than True as not fully successful.
    """
    return _finalize(
        {"ok": "partial", "data": {"succeeded": succeeded, "failed": failed}},
        meta=meta,
        next=next,
        dry_run=dry_run,
    )


def envelope_partial_data(
    data: Any,
    meta: dict | None = None,
    *,
    next: Sequence[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Partial-success envelope with a command-specific data payload.

    For commands that complete part of an operation (e.g. an item is created but
    its attachment upload fails) and want to return their own data shape instead
    of the generic succeeded/failed lists. ok="partial", never ok=False without
    an error object.
    """
    return _finalize({"ok": "partial", "data": data}, meta=meta, next=next, dry_run=dry_run)
