"""RAG pipeline: text chunking, metadata, rank fusion, and embeddings.

This module is the stable public facade for `zotero_cli.core.rag`; the
implementations live in `chunking`, `metadata`, `fusion`, and `embeddings`.
"""

from __future__ import annotations

from zotero_cli.core.rag.chunking import (
    MinerUStructuredChunk,
    cascade_chunk,
    chunk_mineru_content,
    chunk_text,
)
from zotero_cli.core.rag.embeddings import EmbeddingProvider, embed_texts
from zotero_cli.core.rag.fusion import reciprocal_rank_fusion, weighted_reciprocal_rank_fusion
from zotero_cli.core.rag.metadata import (
    build_metadata_chunk,
    clean_html,
    filter_ranked_results_by_pdf_kind,
    get_pdf_kind_from_source,
    infer_pdf_kind,
    tokenize,
)

__all__ = [
    "EmbeddingProvider",
    "MinerUStructuredChunk",
    "build_metadata_chunk",
    "cascade_chunk",
    "chunk_mineru_content",
    "chunk_text",
    "clean_html",
    "embed_texts",
    "filter_ranked_results_by_pdf_kind",
    "get_pdf_kind_from_source",
    "infer_pdf_kind",
    "reciprocal_rank_fusion",
    "tokenize",
    "weighted_reciprocal_rank_fusion",
]
