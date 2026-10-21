"""RAG 流水线：文本切分、metadata、排名融合与 embedding。

本模块是 `zotero_cli.workspace.rag` 的稳定公共门面；具体实现位于 `chunking`、`metadata`、
`fusion` 和 `embeddings`。
"""

from __future__ import annotations

from zotero_cli.workspace.rag.chunking import (
    MinerUStructuredChunk,
    cascade_chunk,
    chunk_mineru_content,
    chunk_text,
)
from zotero_cli.workspace.rag.embeddings import EmbeddingProvider, embed_texts
from zotero_cli.workspace.rag.fusion import reciprocal_rank_fusion, weighted_reciprocal_rank_fusion
from zotero_cli.workspace.rag.metadata import (
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

__all__ = [
    "CaptionCounts",
    "EmbeddingProvider",
    "PdfDocument",
    "MinerUStructuredChunk",
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
