"""以 chunk id 为键的本地 Qdrant 向量 store。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from zotero_cli.config import VectorStoreConfig, data_root
from zotero_cli.config.runtime import section

try:  # pragma: no cover - availability depends on platform
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_LOCK_SUFFIX = ".prune.lock"


def __getattr__(name: str) -> Any:
    if name == "_LOCK_TIMEOUT":
        return float(section("vector_store")["lock_timeout_seconds"])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _policy(name: str) -> float:
    return float(getattr(sys.modules[__name__], name))


# 本 CLI 在打开/创建本地 store 时写入的所有权标记。
MANAGED_STORE_MARKER = ".zot-vector-store"
_STORE_MARKER_VERSION = 1
# 本地模式 Qdrant store（加上我们的标记）拥有的条目。其余任何内容都是用户数据，
# 会使该目录无法被删除。
_QDRANT_STORE_ENTRIES = frozenset({"meta.json", "collection", ".lock", "raft_state.json"})
_MANAGED_ENTRIES = _QDRANT_STORE_ENTRIES | {MANAGED_STORE_MARKER}


def resolve_vector_store_path(config: VectorStoreConfig) -> Path:
    """相对于状态数据根目录解析配置的向量 store 目录。

    在源码检出中即为仓库根目录（保持原样）；对于已安装的 CLI，则是确定性的状态根目录，
    而不再是当前目录。
    """
    path = Path(config.path)
    return path if path.is_absolute() else data_root() / path


def _validated_collection_name(collection: str) -> str:
    if not isinstance(collection, str) or not collection.strip():
        raise ValueError("collection must be a non-empty string")
    return collection


def _close_quietly(client: QdrantClient) -> None:
    try:
        client.close()
    except Exception:
        pass


def _lock_path(store_path: Path) -> Path:
    return store_path.parent / f".{store_path.name}{_LOCK_SUFFIX}"


@contextmanager
def store_lock(store_path: Path, *, timeout: float | None = None) -> Generator[bool]:
    """保护 store 创建、清理与 collection 删除的尽力而为的咨询锁。

    锁文件位于 store 旁边（pdf_rag 根目录内），这样删除 store 不会使锁文件本身失效。
    获取到锁时产出 True；在超时内未能获取（或平台不支持咨询锁）时产出 False。
    """
    lock_path = _lock_path(store_path)
    if timeout is None:
        timeout = _policy("_LOCK_TIMEOUT")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield False
        return
    if fcntl is None:  # pragma: no cover - Windows fallback
        try:
            yield True
        finally:
            os.close(fd)
        return
    acquired = False
    deadline = time.monotonic() + max(timeout, 0.0)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _inspect_store_dir(store_path: Path) -> tuple[bool, set[str]]:
    """在不创建或打开任何内容的情况下检查 store 目录。

    返回 (looks_like_managed_store, foreign_entries)。只有带有我们的所有权标记或 Qdrant 的
    meta.json 的目录才算 store；已知 Qdrant/CLI 集合之外的条目都会作为外部用户数据报告。
    """
    if not store_path.is_dir() or store_path.is_symlink():
        return False, set()
    try:
        entries = {entry.name for entry in os.scandir(store_path)}
    except OSError:
        return False, set()
    has_marker = MANAGED_STORE_MARKER in entries
    has_qdrant_meta = "meta.json" in entries
    return (has_marker or has_qdrant_meta), entries - _MANAGED_ENTRIES


def is_local_vector_store(store_path: Path) -> bool:
    """当这是本 CLI 可以管理的本地 Qdrant store 时返回 True。

    绝不创建或打开 store；任意目录（包括空目录和包含用户文件的目录）都返回 False。
    """
    looks_like_store, foreign = _inspect_store_dir(store_path)
    return looks_like_store and not foreign


def _write_store_marker(store_path: Path) -> None:
    marker = store_path / MANAGED_STORE_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(json.dumps({"version": _STORE_MARKER_VERSION}), encoding="utf-8")
    except OSError:
        # 非致命：Qdrant 自身的 meta.json 仍能标识该 store。
        pass


def drop_collection(store_path: Path, collection: str, *, prune_empty: bool = False) -> bool:
    """从已有的本地 store 删除一个 collection，且不创建 store。

    拒绝任意目录：路径必须是受管理的本地 Qdrant store 且没有外部条目，并且绝不创建它。
    当 collection（或在 `prune_empty` 下已清空的 store）被删除时返回 True。
    """
    name = _validated_collection_name(collection)
    looks_like_store, foreign = _inspect_store_dir(store_path)
    if not looks_like_store or foreign:
        return False
    removed = False
    with store_lock(store_path, timeout=float(section("vector_store")["prune_lock_timeout_seconds"])) as acquired:
        if acquired:
            looks_like_store, foreign = _inspect_store_dir(store_path)
            if not looks_like_store or foreign:
                return False
            try:
                client = QdrantClient(path=str(store_path))
            except Exception:
                return False
            try:
                removed = bool(client.collection_exists(name))
                if removed:
                    client.delete_collection(name)
            finally:
                _close_quietly(client)
    if prune_empty and removed:
        prune_empty_store(store_path)
    return removed


def _store_has_collections(store_path: Path) -> bool | None:
    try:
        client = QdrantClient(path=str(store_path))
    except Exception:
        return None
    try:
        return bool(client.get_collections().collections)
    except Exception:
        return None
    finally:
        _close_quietly(client)


def prune_empty_store(store_path: Path) -> bool:
    """删除受管理的空本地 store 目录。

    安全规则：绝不创建 store，绝不打开非 Qdrant 目录，拒绝含有外部用户数据的目录，
    并在关闭 client 后重新检查，使并发写入方不会丢失文件。空目录检查与 rmtree 在同一把
    咨询锁下执行，该锁也用于 store 创建与写入，因此并发 pdf_rag 的新建 collection 不会被删除。
    """
    looks_like_store, foreign = _inspect_store_dir(store_path)
    if not looks_like_store or foreign:
        return False
    with store_lock(store_path, timeout=float(section("vector_store")["prune_lock_timeout_seconds"])) as acquired:
        if not acquired:
            return False
        looks_like_store, foreign = _inspect_store_dir(store_path)
        if not looks_like_store or foreign:
            return False
        has_collections = _store_has_collections(store_path)
        if has_collections is None or has_collections:
            return False
        looks_like_store, foreign = _inspect_store_dir(store_path)
        if not looks_like_store or foreign:
            return False
        try:
            shutil.rmtree(store_path)
        except OSError:
            return False
    return True


class QdrantVectorStore:
    """基于 Qdrant 的本地向量 store（无需服务器或账户）。

    每个 pdf_rag 在共享的本地目录中拥有一个 collection，因此单个 .zot/pdf_rag/_qdrant 目录
    可以承载多个 pdf_rag 而不会相互污染。
    """

    def __init__(self, path: Path, collection: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with store_lock(path, timeout=_policy("_LOCK_TIMEOUT")):
            path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))
            _write_store_marker(path)
        self._path = path
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def _ensure_collection(self, vector_size: int) -> None:
        with store_lock(self._path, timeout=_policy("_LOCK_TIMEOUT")) as acquired:
            if not acquired:  # pragma: no cover - defensive
                raise RuntimeError("could not lock the local vector store")
            if not self._client.collection_exists(self._collection):
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
                )
            else:
                vectors_config = self._client.get_collection(self._collection).config.params.vectors
                if not isinstance(vectors_config, VectorParams) or vectors_config.size != vector_size:
                    raise ValueError("Vector dimension does not match the existing collection")

    def upsert(self, ids: list[int], vectors: list[list[float]], payloads: list[dict] | None = None) -> None:
        if len(ids) != len(vectors) or (payloads is not None and len(ids) != len(payloads)):
            raise ValueError("ids, vectors, and payloads must have equal lengths")
        if not ids:
            return
        vector_size = len(vectors[0])
        if vector_size == 0 or any(len(vector) != vector_size for vector in vectors):
            raise ValueError("All vectors must have the same nonzero dimension")
        self._ensure_collection(vector_size)
        if payloads is None:
            payloads = [{} for _ in ids]
        points = [
            PointStruct(id=chunk_id, vector=vector, payload=payload)
            for chunk_id, vector, payload in zip(ids, vectors, payloads)
        ]
        self._client.upsert(collection_name=self._collection, points=points, wait=True)

    def search(self, query_vector: list[float], limit: int = 150) -> list[tuple[int, float, dict]]:
        if not self._client.collection_exists(self._collection):
            return []
        hits = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=limit,
        ).points
        return [(int(hit.id), float(hit.score), dict(hit.payload or {})) for hit in hits]

    def list_ids(self) -> list[int]:
        if not self._client.collection_exists(self._collection):
            return []
        ids: list[int] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection, limit=10000, offset=offset, with_vectors=False
            )
            ids.extend(int(point.id) for point in points)
            if offset is None:
                return ids

    def delete(self, ids: list[int]) -> None:
        if not ids or not self._client.collection_exists(self._collection):
            return
        # qdrant 运行期接受整数 ID 列表，但其类型注解要求更宽的联合类型。
        points_selector = ids
        self._client.delete(collection_name=self._collection, points_selector=points_selector, wait=True)  # ty: ignore[invalid-argument-type]

    def delete_all(self) -> None:
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)

    def drop_collection(self, collection_name: str) -> bool:
        """删除此本地 store 中另一个指定名称的 collection；若其存在则返回 True。"""
        name = _validated_collection_name(collection_name)
        removed = bool(self._client.collection_exists(name))
        if removed:
            self._client.delete_collection(name)
        return removed

    def count(self) -> int:
        if not self._client.collection_exists(self._collection):
            return 0
        return int(self._client.count(collection_name=self._collection).count)

    def close(self) -> None:
        _close_quietly(self._client)
