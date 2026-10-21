"""PDF RAG 文献集清单、切分、向量化与检索能力。"""

from __future__ import annotations

from zotero_cli.pdf_rag.chunking import (
    MinerUStructuredChunk,
    cascade_chunk,
    chunk_mineru_content,
    chunk_text,
)
from zotero_cli.pdf_rag.embeddings import EmbeddingProvider, embed_texts
from zotero_cli.pdf_rag.fusion import reciprocal_rank_fusion, weighted_reciprocal_rank_fusion
from zotero_cli.pdf_rag.metadata import (
    CaptionCounts,
    PdfDocument,
    build_metadata_chunk,
    caption_counts,
    clean_html,
    filter_ranked_results_by_pdf_kind,
    get_pdf_kind_from_source,
    infer_pdf_kind,
    resolve_pdf_kinds,
    tokenize,
)
from zotero_cli.pdf_rag.store import PdfRag, PdfRagItem

__all__ = [
    "CaptionCounts",
    "EmbeddingProvider",
    "MinerUStructuredChunk",
    "PdfDocument",
    "PdfRag",
    "PdfRagItem",
    "build_metadata_chunk",
    "caption_counts",
    "cascade_chunk",
    "chunk_mineru_content",
    "chunk_text",
    "clean_html",
    "embed_texts",
    "filter_ranked_results_by_pdf_kind",
    "get_pdf_kind_from_source",
    "infer_pdf_kind",
    "reciprocal_rank_fusion",
    "resolve_pdf_kinds",
    "tokenize",
    "weighted_reciprocal_rank_fusion",
]
