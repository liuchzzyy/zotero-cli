"""workspace 混合检索命令（FTS5 + Qdrant，可选 rerank）。"""

from __future__ import annotations

import typer

from zotero_cli.cli.commands.workspace._output import (
    emit_result,
    emit_warning,
    output_json,
    workspace_directory_or_exit,
)
from zotero_cli.cli.commands.workspace.indexing import open_vector_store_for_read
from zotero_cli.cli.output.renderers import format_workspace_query
from zotero_cli.config import load_embedding_config, load_rerank_config, load_semantic_search_config
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.workspace.rag import embed_texts, filter_ranked_results_by_pdf_kind, weighted_reciprocal_rank_fusion
from zotero_cli.workspace.rag.index import RagIndex
from zotero_cli.workspace.rag.rerank import UnsupportedRerankerProviderError, rerank_chunks
from zotero_cli.workspace.rag.vector_store import QdrantVectorStore
from zotero_cli.workspace.store import workspace_index_path


def _search_run(
    idx: RagIndex,
    vector_store: QdrantVectorStore | None,
    question: str,
    *,
    candidate_k: int,
    bm25_weight: float,
    semantic_weight: float,
    rrf_k: int,
    mode: str,
) -> tuple[list[tuple[int, float, dict]], str, list[str], bool]:
    """执行检索并返回 (merged, executed_mode, degradation_notes, semantic_available)。"""
    degradation: list[str] = []
    has_vectors = False
    if vector_store is not None:
        try:
            has_vectors = vector_store.count() > 0
        except (OSError, RuntimeError, ValueError) as exc:
            degradation.append(f"vector store unavailable ({type(exc).__name__}); BM25 results are used")
    effective_mode = ("hybrid" if has_vectors else "bm25") if mode == "auto" else mode
    semantic_available = has_vectors
    bm25_results: list[tuple[int, float, dict]] = []
    semantic_results: list[tuple[int, float, dict]] = []
    if effective_mode in ("bm25", "hybrid"):
        bm25_results = idx.search_bm25(question, limit=candidate_k)
    if effective_mode in ("semantic", "hybrid"):
        if not has_vectors:
            semantic_available = False
            degradation.append("no vectors in the workspace index; semantic retrieval was skipped")
        else:
            emb_cfg = load_embedding_config(apply_env_overrides=True)
            if not emb_cfg.is_configured:
                semantic_available = False
                degradation.append("embedding provider is not configured; semantic retrieval was skipped")
            elif vector_store is not None:
                try:
                    q_vecs = embed_texts([question], emb_cfg, input_type="query")
                    if q_vecs and q_vecs[0]:
                        hits = vector_store.search(q_vecs[0], limit=candidate_k)
                        hit_ids = [cid for cid, _score, _payload in hits]
                        chunks_by_id = idx.get_chunks_by_ids(hit_ids)
                        semantic_results = [
                            (cid, score, chunks_by_id[cid]) for cid, score, _payload in hits if cid in chunks_by_id
                        ]
                    else:
                        semantic_available = False
                        degradation.append(
                            "embedding provider returned no query vector; semantic retrieval was skipped"
                        )
                except Exception as exc:
                    semantic_available = False
                    degradation.append(f"semantic retrieval failed ({type(exc).__name__}); BM25 results are used")
    if effective_mode == "hybrid" and bm25_results and semantic_results:
        merged = weighted_reciprocal_rank_fusion(
            [bm25_results, semantic_results],
            weights=[bm25_weight, semantic_weight],
            k=rrf_k,
        )
        executed_mode = "hybrid"
    elif semantic_results and effective_mode in ("semantic", "hybrid"):
        merged = semantic_results
        executed_mode = "semantic"
    else:
        merged = bm25_results
        executed_mode = "bm25" if effective_mode != "semantic" else "semantic"
    return merged, executed_mode, degradation, semantic_available


def workspace_query(
    ctx: typer.Context,
    question: str,
    ws_name: str,
    top_k: int | None,
    mode: str,
    pdf_kind: str,
    rerank: bool,
    rerank_top_n: int,
) -> None:
    """用自然语言查询 workspace 论文（FTS5 + Qdrant 混合检索）。"""
    json_out = output_json(ctx)
    workspace_directory_or_exit(ctx, ws_name, context="workspace query")
    idx_path = workspace_index_path(ws_name)
    if not idx_path.exists():
        emit_error(
            "not_found",
            f"No index found for workspace '{ws_name}'",
            output_json=json_out,
            hint=f"Run 'zot workspace index {ws_name}' first",
            context="workspace query",
        )

    ss_cfg = load_semantic_search_config()
    idx = RagIndex(idx_path)
    vector_store = open_vector_store_for_read(ws_name)
    try:
        if top_k is None:
            top_k = ss_cfg.top_k
        merged, executed_mode, degradation, semantic_available = _search_run(
            idx,
            vector_store,
            question,
            candidate_k=ss_cfg.candidate_k,
            bm25_weight=ss_cfg.bm25_weight,
            semantic_weight=ss_cfg.semantic_weight,
            rrf_k=ss_cfg.rrf_k,
            mode=mode,
        )
        filtered = filter_ranked_results_by_pdf_kind(merged, pdf_kind)
        if pdf_kind != "any" and merged and not filtered:
            emit_warning(
                f"all {len(merged)} candidate chunk(s) were non-PDF chunks and were excluded by --pdf-kind {pdf_kind}"
            )
        rerank_applied = False
        rerank_requested = bool(rerank and filtered)
        if rerank and filtered:
            rerank_cfg = load_rerank_config(apply_env_overrides=True)
            if not rerank_cfg.is_configured:
                emit_error(
                    "configuration_error",
                    "Reranker provider is not configured",
                    output_json=json_out,
                    hint="Set [rerank.api.gitee] in .zot/config.toml",
                    context="workspace query",
                )
            try:
                reranked = rerank_chunks(question, filtered, rerank_cfg, top_n=rerank_top_n)
            except UnsupportedRerankerProviderError as exc:
                emit_error(
                    "configuration_error",
                    str(exc),
                    output_json=json_out,
                    hint="Set [rerank].provider = 'gitee' in .zot/config.toml",
                    context="workspace query",
                )
            if reranked:
                filtered = reranked
                rerank_applied = True
            else:
                degradation.append("reranker provider returned no ranking; retrieval ordering is used")
        top = filtered[:top_k]
        display_mode = executed_mode + ("+rerank" if rerank_applied else "")
        for note in degradation:
            emit_warning(note)
        results = [
            {
                "rank": i + 1,
                "score": float(score),
                "item_key": chunk["item_key"],
                "source": chunk["source"],
                "content": chunk["content"][:500],
            }
            for i, (_cid, score, chunk) in enumerate(top)
        ]
        meta: dict[str, object] = {
            "count": len(results),
            "mode": display_mode,
            "requested_mode": mode,
            "degraded": bool(degradation),
            "semantic_available": semantic_available,
            "rerank_requested": rerank_requested,
            "rerank_applied": rerank_applied,
        }
        if degradation:
            meta["degradation"] = list(degradation)
        if not top:
            emit_result(
                ctx,
                {"mode": display_mode, "results": []},
                human_text="No results found.",
                meta=meta,
            )
            return
        emit_result(
            ctx,
            {"mode": display_mode, "results": results},
            human_text=format_workspace_query(top, mode=display_mode, output_json=False),
            meta=meta,
        )
    finally:
        if vector_store is not None:
            vector_store.close()
        idx.close()
