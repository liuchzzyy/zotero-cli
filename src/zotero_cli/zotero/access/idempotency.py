"""基于 SQLite 的重放缓存，以及可选的持久化变更预留。

每个幂等变更（新增、更新、删除、附加文件、创建注记）在发送前都会预留
(scope, key)：存在进行中或结果未知的请求时禁止盲目重发，即使缓存 TTL 已过
也是如此。调用方仍可直接使用 get_cached/store_cached，但仅靠这两个函数
并不能提供恰好一次（exactly-once）保证——对于结果重要的写入，应使用
begin_mutation/release_rejected_mutation。预留需要通过核对来解除，
而不是依赖基于时间的过期。

作用域绑定命令、库与请求，但不包含凭据。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import httpx2

from zotero_cli.config import state_dir
from zotero_cli.config.runtime import section


def __getattr__(name: str) -> Any:
    if name == "TTL_SECONDS":
        return float(section("zotero")["idempotency_ttl_seconds"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ttl() -> float:
    return float(getattr(sys.modules[__name__], "TTL_SECONDS"))


# 能够证明服务端拒绝了写入的状态码；此时可以安全解除预留。
# 其他情况（5xx、超时、412）使结果保持未知，因此保留预留。
_REJECTED_STATUS_CODES = frozenset({400, 401, 403, 404, 409, 413, 422, 428, 429})


class PendingMutationError(Exception):
    """同一作用域键已存在进行中或结果不确定的变更。"""


def request_scope(command: str, library_type: str, library_id: str, payload: object) -> str:
    """把重试键绑定到目标库和具体请求，绝不包含凭据。"""
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"{command}:{library_type}:{library_id}:{digest}"


def response_definitely_rejected(response: object) -> bool:
    """仅在响应能够证明远端写入未生效时返回 True。

    pyzotero 会在客户端保留最后一次写请求的响应，因此调用方可以区分
    显式拒绝（可安全解除预留）与结果未知（网络中断、412、5xx、JSON 损坏）。
    """
    if not isinstance(response, (httpx2.Response, httpx.Response)):
        return False
    request = getattr(response, "request", None)
    if request is None or request.method not in {"POST", "PATCH", "PUT", "DELETE"}:
        return False
    if response.status_code in _REJECTED_STATUS_CODES:
        return True
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict) or any(payload.get(field) for field in ("successful", "success", "unchanged")):
        return False
    failed = payload.get("failed")
    failure = failed.get("0") if isinstance(failed, dict) else None
    code = failure.get("code") if isinstance(failure, dict) else None
    return isinstance(code, int) and code in _REJECTED_STATUS_CODES


_DEFINITIVE_FAILURE_CODES = frozenset({"not_found", "validation_error", "auth_invalid"})


def failure_definitely_rejected(*, response: object, code: str, retryable: bool, committed: bool = False) -> bool:
    """当写入错误能够证明远端变更未生效时返回 True。

    优先使用最后一次 HTTP 响应以及带类型错误的 committed 标记；
    否则退回到不可能表示未知结果的错误码（被拒绝的键或非法负载绝不会提交）。
    冲突、5xx 与网络错误仍视为结果不确定。
    """
    if committed:
        return False
    if response_definitely_rejected(response):
        return True
    return code in _DEFINITIVE_FAILURE_CODES and not retryable


def _db_path() -> Path:
    base = state_dir()
    base.mkdir(parents=True, exist_ok=True)
    return base / "idempotency.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        "scope TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, "
        "created_at INTEGER NOT NULL, PRIMARY KEY (scope, key))"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pending ("
        "scope TEXT NOT NULL, key TEXT NOT NULL, created_at INTEGER NOT NULL, "
        "PRIMARY KEY (scope, key))"
    )
    conn.execute("DELETE FROM cache WHERE created_at < ?", (int(time.time()) - _ttl(),))
    conn.commit()
    return conn


def _cached_value(conn: sqlite3.Connection, scope: str, key: str) -> dict | None:
    row = conn.execute(
        "SELECT value, created_at FROM cache WHERE scope=? AND key=?",
        (scope, key),
    ).fetchone()
    if row is None:
        return None
    value_json, created_at = row
    if int(time.time()) - created_at > _ttl():
        return None
    try:
        loaded = json.loads(value_json)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def get_cached(scope: str, key: str) -> dict | None:
    if not key:
        return None
    with closing(_connect()) as conn, conn:
        return _cached_value(conn, scope, key)


def begin_mutation(scope: str, key: str) -> dict | None:
    """在发起 HTTP 之前，原子地重放成功结果或持久化预留一次写入。

    重复预留会按失败关闭（fail closed）处理。该预留没有 TTL，
    因为进程退出或时间流逝都无法确定远端写入是否已提交。
    """
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached_value(conn, scope, key)
        if cached is not None:
            return cached
        try:
            conn.execute(
                "INSERT INTO pending (scope, key, created_at) VALUES (?, ?, ?)",
                (scope, key, int(time.time())),
            )
        except sqlite3.IntegrityError as exc:
            raise PendingMutationError("A write is in progress or its previous outcome is unknown") from exc
    return None


def release_rejected_mutation(scope: str, key: str) -> None:
    """仅在服务端显式拒绝写入后才解除预留。"""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM pending WHERE scope=? AND key=?", (scope, key))


def store_cached(scope: str, key: str, envelope: dict) -> None:
    if not key:
        return
    now = int(time.time())
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache (scope, key, value, created_at) VALUES (?, ?, ?, ?)",
            (scope, key, json.dumps(envelope), now),
        )
        conn.execute("DELETE FROM pending WHERE scope=? AND key=?", (scope, key))


def clear() -> None:
    """清空缓存与预留。仅用于隔离测试，不用于恢复。"""
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM cache")
        conn.execute("DELETE FROM pending")
