"""Local Qdrant vector store keyed by chunk id."""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from zotero_cli.config import VectorStoreConfig, data_root

try:  # pragma: no cover - availability depends on platform
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_LOCK_SUFFIX = ".prune.lock"
_LOCK_TIMEOUT = 5.0

# Ownership marker written by this CLI when it opens/creates a local store.
MANAGED_STORE_MARKER = ".zot-vector-store"
_STORE_MARKER_VERSION = 1
# Entries a local-mode Qdrant store (plus our marker) owns. Anything else is
# user data and disqualifies the directory from deletion.
_QDRANT_STORE_ENTRIES = frozenset({"meta.json", "collection", ".lock", "raft_state.json"})
_MANAGED_ENTRIES = _QDRANT_STORE_ENTRIES | {MANAGED_STORE_MARKER}


def resolve_vector_store_path(cfg: VectorStoreConfig) -> Path:
    """Resolve the configured vector-store directory relative to the state data root.

    In a checkout this is the repository root (unchanged); for an installed CLI
    it is the deterministic state root instead of the current directory.
    """
    path = Path(cfg.path)
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
def store_lock(store_path: Path, *, timeout: float = _LOCK_TIMEOUT) -> Iterator[bool]:
    """Best-effort advisory lock guarding store creation, pruning, and collection drops.

    The lock file lives next to the store (inside the workspace root) so that a
    store deletion does not invalidate the lock file itself. Yields True when the
    lock was acquired, False when it could not be acquired within timeout (or the
    platform has no advisory locking).
    """
    lock_path = _lock_path(store_path)
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
    """Inspect a store directory without creating or opening anything.

    Returns (looks_like_managed_store, foreign_entries). A directory only counts
    as a store when it carries our ownership marker or Qdrant's meta.json; every
    entry outside the known Qdrant/CLI set is reported as foreign user data.
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
    """Return True for a local Qdrant store this CLI may manage.

    Never creates or opens the store; arbitrary directories (including empty ones
    and directories containing user files) return False.
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
        # Non-fatal: Qdrant's own meta.json still identifies the store.
        pass


def drop_collection(store_path: Path, collection: str, *, prune_empty: bool = False) -> bool:
    """Delete one collection from an existing local store without creating it.

    Refuses arbitrary directories: the path must be a managed local Qdrant store
    with no foreign entries, and it is never created. Returns True when the
    collection (or, with `prune_empty`, the now-empty store) was removed.
    """
    name = _validated_collection_name(collection)
    looks_like_store, foreign = _inspect_store_dir(store_path)
    if not looks_like_store or foreign:
        return False
    removed = False
    with store_lock(store_path, timeout=1.0) as acquired:
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
    """Remove a managed, empty local store directory.

    Safety rules: never creates the store, never opens a non-Qdrant directory,
    refuses directories with foreign user data, and re-checks after closing the
    client so a concurrent writer cannot lose files. The empty check and rmtree
    happen under the same advisory lock used by store creation/writes, so a
    concurrent workspace cannot have its fresh collection removed.
    """
    looks_like_store, foreign = _inspect_store_dir(store_path)
    if not looks_like_store or foreign:
        return False
    with store_lock(store_path, timeout=1.0) as acquired:
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
    """Local Qdrant-backed vector store (no server or account required).

    Each workspace owns one collection inside a shared local directory, so a
    single .workspace/_qdrant directory can host many workspaces without
    cross-contamination.
    """

    def __init__(self, path: Path, collection: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with store_lock(path, timeout=_LOCK_TIMEOUT):
            path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(path))
            _write_store_marker(path)
        self._path = path
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def _ensure_collection(self, vector_size: int) -> None:
        with store_lock(self._path, timeout=_LOCK_TIMEOUT) as acquired:
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
        self._client.delete(collection_name=self._collection, points_selector=ids, wait=True)  # type: ignore[arg-type]

    def delete_all(self) -> None:
        if self._client.collection_exists(self._collection):
            self._client.delete_collection(self._collection)

    def drop_collection(self, collection_name: str) -> bool:
        """Delete another named collection in this local store; return True if it existed."""
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
