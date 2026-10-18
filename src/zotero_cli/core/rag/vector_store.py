"""Local Qdrant vector store keyed by chunk id."""

from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from zotero_cli.config import VectorStoreConfig, project_root


def resolve_vector_store_path(cfg: VectorStoreConfig) -> Path:
    """Resolve the configured vector-store directory relative to the project root."""
    path = Path(cfg.path)
    return path if path.is_absolute() else project_root() / path


class QdrantVectorStore:
    """Local Qdrant-backed vector store (no server or account required).

    Each workspace owns one collection inside a shared local directory, so a
    single .workspace/_qdrant directory can host many workspaces without
    cross-contamination.
    """

    def __init__(self, path: Path, collection: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self._client = QdrantClient(path=str(path))
        self._collection = collection

    @property
    def collection(self) -> str:
        return self._collection

    def _ensure_collection(self, vector_size: int) -> None:
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

    def count(self) -> int:
        if not self._client.collection_exists(self._collection):
            return 0
        return int(self._client.count(collection_name=self._collection).count)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
