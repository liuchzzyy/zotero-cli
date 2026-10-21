"""稳定的 JSON envelope 契约：请求作用域、meta 以及 envelope 构建函数。

zot 输出的每个机器可读响应都在此处构建，因此代理可以依赖同一种结构。
文档化的顶层键：

* ok：True（成功）、False（错误）或 "partial"（部分操作成功、部分失败）。
  使用方必须将其视为三态，而不是布尔值。
* data：出现在成功与 partial envelope 中。
* error：出现在错误 envelope 中，含 code/message/retryable（以及 hint/context）。
* meta：schema_version、cli_version，请求作用域内的 request_id/latency_ms，
  以及各命令字段（例如 count、detail）。
* dry_run：可选，预览类命令的 True 标记。
* next：可选，安全的后续命令字符串组成的非空列表，仅出现在成功/partial
  envelope 中。代理可以直接执行它们；但它们绝不能是理解结果的唯一途径，
  也绝不意味着远程写入在本地立即可见（本地读取可能滞后，直到 Zotero Desktop 同步）。

其他顶层键都不属于该契约。请求作用域将 request_id/latency 绑定到当前上下文，
base_meta() 会把它们合入 meta，调用方无需自行传递。
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

# 兼容层接受的顶层额外键；两者都是正式键。
_ALLOWED_EXTRAS = frozenset({"dry_run", "next"})

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_request_id", default=None)
_request_start: contextvars.ContextVar[float | None] = contextvars.ContextVar("_request_start", default=None)


@contextmanager
def request_scope() -> Iterator[str]:
    """将 request_id 与起始时间绑定到当前上下文。

    在此作用域内输出的 envelope 会自动在 meta 中带上 request_id 与
    latency_ms，因此命令无需显式传递它们。
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
    """返回绑定到当前请求作用域的 request_id；若不存在则为 None。"""
    return _request_id.get()


def elapsed_ms() -> int | None:
    """返回当前请求作用域已流逝的毫秒数；若不存在则为 None。"""
    start = _request_start.get()
    if start is None:
        return None
    return int((time.monotonic() - start) * 1000)


def base_meta() -> dict[str, Any]:
    """返回每个 envelope 都会携带的 meta 字段，包含请求上下文。"""
    meta: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "cli_version": __version__}
    rid = current_request_id()
    if rid:
        meta["request_id"] = rid
    elapsed = elapsed_ms()
    if elapsed is not None:
        meta["latency_ms"] = elapsed
    return meta


def _clean_next(next: Sequence[str] | None) -> list[str] | None:
    """校验正式的可选 'next' 提示列表（为空时返回 None）。"""
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
    """仅接受两个已文档化顶层额外键的旧式 extra={...} 兼容层。"""
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
    """构建成功 envelope（ok=True）。

    next：可选的安全后续命令（非空字符串）；仅当非空时作为顶层 next 列表
    输出。extra 是兼容层，只接受已文档化的 dry_run/next 键；新代码应直接
    传入 dry_run=True / next=[...]。
    """
    next, dry_run = _merge_legacy_extra(extra, next, dry_run)
    return _finalize({"ok": True, "data": data}, meta=meta, next=next, dry_run=dry_run)


def envelope_error(
    code: str,
    message: str,
    retryable: bool = False,
    **extra: Any,
) -> dict:
    """构建错误 envelope（ok=False）；按契约此处拒绝 'next'。"""
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
    """使用标准 succeeded/failed 列表的部分成功 envelope。

    ok 是三态字符串 "partial"。详情位于 data.succeeded / data.failed。
    使用方必须显式比较，或将任何非 True 的值视为未完全成功。
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
    """使用命令特定数据载荷的部分成功 envelope。

    适用于完成操作一部分的命令（例如条目已创建但附件上传失败），它们希望
    返回自己的数据结构，而不是通用的 succeeded/failed 列表。此时
    ok="partial"，且绝不会在没有 error 对象的情况下出现 ok=False。
    """
    return _finalize({"ok": "partial", "data": data}, meta=meta, next=next, dry_run=dry_run)
