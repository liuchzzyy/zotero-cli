"""Workspace hybrid retrieval command (FTS5 + Qdrant, optional rerank)."""

from __future__ import annotations

import json

import click

from zotero_cli.commands.workspace import workspace_group
from zotero_cli.commands.workspace.indexing import _vector_store_for
from zotero_cli.config import load_embedding_config, load_rerank_config, load_semantic_search_config
from zotero_cli.core.rag import embed_texts, filter_ranked_results_by_pdf_kind, weighted_reciprocal_rank_fusion
from zotero_cli.core.rag.index import RagIndex
from zotero_cli.core.rag.rerank import rerank_chunks
from zotero_cli.core.workspace_store import workspace_exists, workspace_index_path
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_workspace_query


@workspace_group.command("query")
@click.argument("question")
@click.option("--workspace", "ws_name", required=True, help="Workspace to query")
@click.option("--top-k", default=None, type=int, help="Number of results (default: from [semantic_search].top_k).")
@click.option(
    "--mode",
    type=click.Choice(["auto", "bm25", "semantic", "hybrid"]),
    default="auto",
    help="Retrieval mode",
)
@click.option(
    "--pdf-kind",
    type=click.Choice(["any", "main", "supplementary"]),
    default="any",
    help="Restrict PDF results to main paper or supplementary PDF chunks",
)
@click.option("--rerank", is_flag=True, help="Rerank fused retrieval candidates with the configured reranker.")
@click.option(
    "--rerank-top-n",
    type=click.IntRange(min=1),
    default=50,
    show_default=True,
    help="Number of fused candidates to rerank.",
)
@click.pass_context
def workspace_query(
    ctx: click.Context,
    question: str,
    ws_name: str,
    top_k: int | None,
    mode: str,
    pdf_kind: str,
    rerank: bool,
    rerank_top_n: int,
) -> None:
    """Query workspace papers with natural language (FTS5 + Qdrant hybrid retrieval)."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(ws_name):
        emit_error(
            "not_found",
            f"Workspace '{ws_name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace query",
        )
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
    vector_store = _vector_store_for(ws_name)
    try:
        if top_k is None or top_k <= 0:
            top_k = ss_cfg.top_k
        candidate_k = ss_cfg.candidate_k
        rrf_k = ss_cfg.rrf_k

        has_vectors = vector_store.count() > 0
        if mode == "auto":
            effective_mode = "hybrid" if has_vectors else "bm25"
        else:
            effective_mode = mode

        bm25_results: list[tuple[int, float, dict]] = []
        semantic_results: list[tuple[int, float, dict]] = []

        if effective_mode in ("bm25", "hybrid"):
            bm25_results = idx.search_bm25(question, limit=candidate_k)

        if effective_mode in ("semantic", "hybrid") and has_vectors:
            emb_cfg = load_embedding_config(apply_env_overrides=True)
            if emb_cfg.is_configured:
                try:
                    q_vecs = embed_texts([question], emb_cfg, input_type="query")
                    if q_vecs and q_vecs[0]:
                        hits = vector_store.search(q_vecs[0], limit=candidate_k)
                        hit_ids = [cid for cid, _score, _payload in hits]
                        chunks_by_id = idx.get_chunks_by_ids(hit_ids)
                        semantic_results = [
                            (cid, score, chunks_by_id[cid]) for cid, score, _payload in hits if cid in chunks_by_id
                        ]
                except Exception:
                    pass

        if effective_mode == "hybrid" and bm25_results and semantic_results:
            merged = weighted_reciprocal_rank_fusion(
                [bm25_results, semantic_results],
                weights=[ss_cfg.bm25_weight, ss_cfg.semantic_weight],
                k=rrf_k,
            )
        elif semantic_results and effective_mode in ("semantic", "hybrid"):
            merged = semantic_results
        else:
            merged = bm25_results

        filtered = filter_ranked_results_by_pdf_kind(merged, pdf_kind)
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
            reranked = rerank_chunks(question, filtered, rerank_cfg, top_n=rerank_top_n)
            if reranked:
                filtered = reranked
                effective_mode = f"{effective_mode}+rerank"

        top = filtered[:top_k]
        if not top:
            if json_out:
                click.echo(
                    json.dumps(envelope_ok([], meta={"count": 0, "mode": effective_mode}), indent=2, ensure_ascii=False)
                )
            else:
                click.echo("No results found.")
            return
        click.echo(format_workspace_query(top, mode=effective_mode, output_json=json_out))
    finally:
        vector_store.close()
        idx.close()
