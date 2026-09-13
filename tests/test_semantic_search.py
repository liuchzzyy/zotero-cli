"""Tests for Qdrant local vector store and hybrid FTS5 + vector retrieval."""

from __future__ import annotations

from zotero_cli_agent.config import VectorStoreConfig
from zotero_cli_agent.core.rag import weighted_reciprocal_rank_fusion
from zotero_cli_agent.core.rag_index import RagIndex
from zotero_cli_agent.core.semantic_search import QdrantVectorStore, resolve_vector_store_path


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
    monkeypatch.setattr("zotero_cli_agent.core.semantic_search.vector_store.project_root", lambda: tmp_path)
    cfg = VectorStoreConfig(path=".workspace/_qdrant")
    assert resolve_vector_store_path(cfg) == tmp_path / ".workspace" / "_qdrant"


def test_resolve_vector_store_path_absolute(tmp_path):
    absolute = str(tmp_path / "qdrant")
    cfg = VectorStoreConfig(path=absolute)
    assert resolve_vector_store_path(cfg) == tmp_path / "qdrant"


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
