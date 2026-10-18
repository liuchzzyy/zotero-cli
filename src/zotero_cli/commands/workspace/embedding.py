"""Workspace embedding backfill command and batch retry helper."""

from __future__ import annotations

import sys
import time

import click

from zotero_cli.commands.workspace import workspace_group
from zotero_cli.commands.workspace.indexing import _vector_store_for
from zotero_cli.config import EmbeddingConfig, load_embedding_config
from zotero_cli.core.rag import embed_texts
from zotero_cli.core.rag.index import RagIndex
from zotero_cli.core.workspace_store import workspace_exists, workspace_index_path
from zotero_cli.output.exit_codes import emit_error


def _embed_batch_with_retries(
    texts: list[str],
    emb_cfg: EmbeddingConfig,
    max_retries: int,
    retry_sleep: float,
) -> list[list[float]]:
    for _attempt in range(max_retries + 1):
        vectors = embed_texts(texts, emb_cfg)
        if vectors is not None and len(vectors) == len(texts):
            return vectors
        if retry_sleep > 0:
            time.sleep(retry_sleep)

    result: list[list[float]] = []
    for text in texts:
        single: list[list[float]] | None = None
        for _attempt in range(max_retries + 1):
            single = embed_texts([text], emb_cfg)
            if single is not None and single and single[0]:
                break
            if retry_sleep > 0:
                time.sleep(retry_sleep)
        result.append(single[0] if single and single[0] else [])
    return result


@workspace_group.command("embed")
@click.argument("name")
@click.option(
    "--batch-size",
    default=50,
    show_default=True,
    help="Number of missing chunks to send to the provider per request.",
)
@click.option(
    "--limit", default=0, show_default=True, help="Maximum chunks to attempt in this run; 0 means all missing."
)
@click.option("--max-retries", default=5, show_default=True, help="Retry a failed provider batch this many times.")
@click.option("--retry-sleep", default=10.0, show_default=True, help="Seconds to sleep between batch retries.")
@click.option(
    "--heartbeat-seconds",
    default=15.0,
    show_default=True,
    help="Seconds between progress lines when --progress-lines is set.",
)
@click.option(
    "--progress-lines", is_flag=True, help="Write progress as newline records for log-friendly real-time output."
)
@click.pass_context
def workspace_embed(
    ctx: click.Context,
    name: str,
    batch_size: int,
    limit: int,
    max_retries: int,
    retry_sleep: float,
    heartbeat_seconds: float,
    progress_lines: bool,
) -> None:
    """Backfill embeddings for an existing workspace index (writes vectors to Qdrant)."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace embed",
        )
    idx_path = workspace_index_path(name)
    if not idx_path.exists():
        emit_error(
            "not_found",
            f"No index found for workspace '{name}'",
            output_json=json_out,
            hint=f"Run 'zot workspace index {name}' first",
            context="workspace embed",
        )
    if batch_size <= 0:
        emit_error(
            "validation_error", "--batch-size must be greater than 0", output_json=json_out, context="workspace embed"
        )
    if limit < 0:
        emit_error("validation_error", "--limit must be 0 or greater", output_json=json_out, context="workspace embed")
    if max_retries < 0:
        emit_error(
            "validation_error", "--max-retries must be 0 or greater", output_json=json_out, context="workspace embed"
        )
    if retry_sleep < 0:
        emit_error(
            "validation_error", "--retry-sleep must be 0 or greater", output_json=json_out, context="workspace embed"
        )
    if heartbeat_seconds < 0:
        emit_error(
            "validation_error",
            "--heartbeat-seconds must be 0 or greater",
            output_json=json_out,
            context="workspace embed",
        )

    emb_cfg = load_embedding_config(apply_env_overrides=True)
    if not emb_cfg.is_configured:
        emit_error(
            "configuration_error",
            "Embedding provider is not configured",
            output_json=json_out,
            hint="Set [embedding.api.gitee].api_key in .zot/config.toml or ZOT_EMBEDDING_KEY",
            context="workspace embed",
        )

    idx = RagIndex(idx_path)
    vector_store = _vector_store_for(name)
    t0 = time.monotonic()
    try:
        existing = set(vector_store.list_ids())
        all_ids = idx.get_chunk_ids()
        missing = [cid for cid in all_ids if cid not in existing]
        if not missing:
            click.echo(f"Embeddings for '{name}' are already complete.")
            return

        target = min(len(missing), limit) if limit else len(missing)
        missing = missing[:target]
        attempted = 0
        stored = 0
        skipped = 0
        click.echo(
            f"Backfilling embeddings for '{name}': missing={len(missing)} "
            f"provider={emb_cfg.provider} model={emb_cfg.model} batch_size={batch_size}"
        )

        for i in range(0, len(missing), batch_size):
            batch_ids = missing[i : i + batch_size]
            chunks = idx.get_chunks_by_ids(batch_ids)
            texts = [chunks[cid]["content"] for cid in batch_ids if cid in chunks]
            vectors = _embed_batch_with_retries(texts, emb_cfg, max_retries, retry_sleep)

            valid_ids: list[int] = []
            valid_vectors: list[list[float]] = []
            valid_payloads: list[dict] = []
            for cid, vector in zip(batch_ids, vectors):
                chunk = chunks.get(cid)
                if vector and chunk is not None:
                    valid_ids.append(cid)
                    valid_vectors.append(vector)
                    valid_payloads.append({"item_key": chunk["item_key"], "source": chunk["source"]})
                else:
                    skipped += 1
            if valid_ids:
                vector_store.upsert(valid_ids, valid_vectors, valid_payloads)
                stored += len(valid_ids)
            attempted += len(batch_ids)

            status = f"  [embed] attempted={attempted}/{len(missing)} stored={stored} skipped={skipped}"
            if progress_lines:
                click.echo(status)
            else:
                sys.stderr.write(f"\r{' ' * 100}\r{status}")
                sys.stderr.flush()

        if not progress_lines:
            sys.stderr.write(f"\r{' ' * 100}\r")
            sys.stderr.flush()
        remaining = sum(1 for cid in idx.get_chunk_ids() if cid not in set(vector_store.list_ids()))
        click.echo(
            f"Backfilled embeddings for '{name}': attempted={attempted}, stored={stored}, "
            f"skipped={skipped}, remaining_missing={remaining}, elapsed={time.monotonic() - t0:.1f}s"
        )
    finally:
        vector_store.close()
        idx.close()
