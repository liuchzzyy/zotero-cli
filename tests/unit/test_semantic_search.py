"""Qdrant 本地向量库与 FTS5 + 向量混合检索测试。"""

from __future__ import annotations

import pytest
from qdrant_client.models import Distance, VectorParams

from zotero_cli.config import VectorStoreConfig
from zotero_cli.workspace.rag import weighted_reciprocal_rank_fusion
from zotero_cli.workspace.rag.index import RagIndex
from zotero_cli.workspace.rag.vector_store import (
    MANAGED_STORE_MARKER,
    QdrantVectorStore,
    drop_collection,
    is_local_vector_store,
    prune_empty_store,
    resolve_vector_store_path,
    store_lock,
)


def test_qdrant_upsert_search_delete(tmp_path):
    store = QdrantVectorStore(tmp_path / "qdrant", "test")
    try:
        store.upsert(
            [1, 2],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [{"source": "pdf:main:a"}, {"source": "pdf:supplementary:b"}],
        )
        assert store.count() == 2
        hits = store.search([1.0, 0.0, 0.0], limit=2)
        assert hits[0][0] == 1
        assert hits[0][2]["source"] == "pdf:main:a"
        store.delete([1])
        assert store.count() == 1
    finally:
        store.close()


def test_qdrant_delete_all(tmp_path):
    store = QdrantVectorStore(tmp_path / "qdrant", "test")
    try:
        store.upsert([1, 2], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        store.delete_all()
        assert store.count() == 0
        assert store.search([1.0, 0.0, 0.0]) == []
    finally:
        store.close()


def test_qdrant_empty_collection_search(tmp_path):
    store = QdrantVectorStore(tmp_path / "qdrant", "test")
    try:
        assert store.search([1.0, 0.0, 0.0]) == []
        assert store.list_ids() == []
    finally:
        store.close()


def test_resolve_vector_store_path_relative(tmp_path, monkeypatch):
    monkeypatch.setattr("zotero_cli.workspace.rag.vector_store.data_root", lambda: tmp_path)
    config = VectorStoreConfig(path=".workspace/_qdrant")
    assert resolve_vector_store_path(config) == tmp_path / ".workspace" / "_qdrant"


def test_resolve_vector_store_path_absolute(tmp_path):
    absolute = str(tmp_path / "qdrant")
    config = VectorStoreConfig(path=absolute)
    assert resolve_vector_store_path(config) == tmp_path / "qdrant"


def test_hybrid_retrieval_fts5_plus_qdrant(tmp_path):
    idx = RagIndex(tmp_path / "idx.sqlite")
    store = QdrantVectorStore(tmp_path / "qdrant", "ws_test")
    try:
        c1 = idx.insert_chunk("A", "pdf:main:a:f.pdf", "attention mechanism in transformers")
        c2 = idx.insert_chunk("B", "pdf:main:b:g.pdf", "zinc ion battery electrolyte")
        store.upsert(
            [c1, c2],
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [
                {"item_key": "A", "source": "pdf:main:a:f.pdf"},
                {"item_key": "B", "source": "pdf:main:b:g.pdf"},
            ],
        )

        bm25 = idx.search_bm25("attention mechanism", limit=10)
        hits = store.search([1.0, 0.0, 0.0], limit=10)
        chunks_by_id = idx.get_chunks_by_ids([cid for cid, _score, _payload in hits])
        semantic = [(cid, score, chunks_by_id[cid]) for cid, score, _payload in hits if cid in chunks_by_id]

        merged = weighted_reciprocal_rank_fusion([bm25, semantic], weights=[0.2, 0.8], k=60)
        assert merged[0][0] == c1
    finally:
        store.close()
        idx.close()


def test_drop_collection_never_creates_missing_store(tmp_path):
    store_path = tmp_path / "missing" / "_qdrant"
    assert drop_collection(store_path, "ws_gone") is False
    assert not store_path.exists()
    assert prune_empty_store(store_path) is False
    assert not store_path.exists()


def test_drop_collection_removes_only_requested_collection(tmp_path):
    store_path = tmp_path / "_qdrant"
    keep = QdrantVectorStore(store_path, "ws_keep")
    try:
        keep.upsert([1], [[1.0, 0.0]])
    finally:
        keep.close()
    gone = QdrantVectorStore(store_path, "ws_gone")
    try:
        gone.upsert([2], [[1.0, 0.0]])
    finally:
        gone.close()

    assert drop_collection(store_path, "ws_gone") is True
    assert drop_collection(store_path, "ws_gone") is False

    check = QdrantVectorStore(store_path, "ws_keep")
    try:
        assert check.count() == 1
    finally:
        check.close()
    assert prune_empty_store(store_path) is False


def test_drop_collection_with_prune_removes_empty_store(tmp_path):
    store_path = tmp_path / "_qdrant"
    store = QdrantVectorStore(store_path, "ws_last")
    try:
        store.upsert([1], [[1.0]])
    finally:
        store.close()

    assert drop_collection(store_path, "ws_last", prune_empty=True) is True
    assert not store_path.exists()


def test_drop_collection_validates_name(tmp_path):
    with pytest.raises(ValueError, match="collection"):
        drop_collection(tmp_path, "")
    with pytest.raises(ValueError, match="collection"):
        drop_collection(tmp_path, None)  # type: ignore[arg-type]


def test_instance_drop_collection_deletes_named_collection(tmp_path):
    store = QdrantVectorStore(tmp_path / "_qdrant", "ws_a")
    try:
        store._client.create_collection(
            collection_name="ws_b", vectors_config=VectorParams(size=2, distance=Distance.COSINE)
        )
        assert store.drop_collection("ws_b") is True
        assert store.drop_collection("ws_b") is False
    finally:
        store.close()


def test_managed_store_writes_ownership_marker(tmp_path):
    store_path = tmp_path / "_qdrant"
    store = QdrantVectorStore(store_path, "ws_marker")
    try:
        store.upsert([1], [[1.0]])
    finally:
        store.close()

    marker = store_path / MANAGED_STORE_MARKER
    assert marker.is_file()
    assert is_local_vector_store(store_path) is True
    with store_lock(store_path) as acquired:
        assert acquired is True


def test_prune_empty_store_refuses_arbitrary_directory_with_user_files(tmp_path):
    victim = tmp_path / "not-a-vector-store"
    victim.mkdir()
    (victim / "important.txt").write_text("user data")
    (victim / "subdir").mkdir()
    (victim / "subdir" / "more.txt").write_text("more user data")
    before = sorted(str(path.relative_to(victim)) for path in victim.rglob("*"))
    parent_before = sorted(path.name for path in tmp_path.iterdir())

    assert is_local_vector_store(victim) is False
    assert prune_empty_store(victim) is False
    assert drop_collection(victim, "ws_gone") is False

    assert victim.is_dir()
    assert sorted(str(path.relative_to(victim)) for path in victim.rglob("*")) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == parent_before


def test_prune_empty_store_refuses_empty_arbitrary_directory(tmp_path):
    victim = tmp_path / "empty-user-dir"
    victim.mkdir()
    parent_before = sorted(path.name for path in tmp_path.iterdir())

    assert is_local_vector_store(victim) is False
    assert prune_empty_store(victim) is False
    assert drop_collection(victim, "ws_gone") is False

    assert victim.is_dir()
    assert list(victim.iterdir()) == []
    assert sorted(path.name for path in tmp_path.iterdir()) == parent_before


def test_store_with_foreign_entry_is_never_pruned(tmp_path):
    store_path = tmp_path / "_qdrant"
    store = QdrantVectorStore(store_path, "ws_foreign")
    try:
        store.upsert([1], [[1.0]])
    finally:
        store.close()
    (store_path / "user-notes.txt").write_text("keep me")

    assert is_local_vector_store(store_path) is False
    assert prune_empty_store(store_path) is False
    assert drop_collection(store_path, "ws_foreign", prune_empty=True) is False
    assert store_path.is_dir()
    assert (store_path / "user-notes.txt").read_text() == "keep me"


def test_managed_store_with_collections_is_not_pruned_but_drop_prunes_last(tmp_path):
    store_path = tmp_path / "_qdrant"
    store = QdrantVectorStore(store_path, "ws_last")
    try:
        store.upsert([1], [[1.0]])
    finally:
        store.close()

    assert is_local_vector_store(store_path) is True
    assert prune_empty_store(store_path) is False
    assert store_path.is_dir()

    assert drop_collection(store_path, "ws_last", prune_empty=True) is True
    assert not store_path.exists()
