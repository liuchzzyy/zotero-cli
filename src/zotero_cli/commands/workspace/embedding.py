"""Workspace embedding backfill command and batch retry helper."""

from __future__ import annotations

import sys
import threading
import time

import click

from zotero_cli.commands.workspace import workspace_group
from zotero_cli.commands.workspace._output import (
    emit_progress,
    emit_result,
    emit_warning,
    load_workspace_or_exit,
    output_json,
)
from zotero_cli.commands.workspace.indexing import _read_index_stats, _vector_store_for
from zotero_cli.config import EmbeddingConfig, load_embedding_config
from zotero_cli.core.rag import embed_texts
from zotero_cli.core.rag.index import RagIndex
from zotero_cli.core.workspace_store import workspace_index_path
from zotero_cli.output.exit_codes import emit_error

_PER_TEXT_PROBE_LIMIT = 2
_PER_TEXT_RETRY_CAP = 2


def _bounded_sleep(seconds: float, deadline: float | None) -> bool:
    """Sleep up to the deadline; return True when the deadline is exhausted."""
    if seconds <= 0:
        return bool(deadline is not None and time.monotonic() >= deadline)
    if deadline is None:
        time.sleep(seconds)
        return False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return True
    time.sleep(min(seconds, remaining))
    return time.monotonic() >= deadline


def _embed_batch_with_retries(
    texts: list[str],
    emb_cfg: EmbeddingConfig,
    max_retries: int,
    retry_sleep: float,
    deadline: float | None = None,
) -> tuple[list[list[float]], bool]:
    """Embed a batch with bounded retries; return (vectors, deadline_exhausted).

    The batch is retried at most max_retries+1 times. If it keeps failing, only
    the first two chunks are probed individually (capped retries); when both
    probes fail the provider is treated as systemically failing (for example a
    permanent 4xx such as an unsupported model or exhausted account) and the
    remaining chunks are skipped instead of retried one by one. The global
    deadline stops all retrying regardless.
    """
    for attempt in range(max_retries + 1):
        if deadline is not None and time.monotonic() >= deadline:
            return [], True
        vectors = embed_texts(texts, emb_cfg)
        if vectors is not None and len(vectors) == len(texts):
            return vectors, False
        if attempt < max_retries and retry_sleep > 0 and _bounded_sleep(retry_sleep, deadline):
            return [], True

    result: list[list[float]] = []
    failures = 0
    per_text_attempts = min(max_retries, _PER_TEXT_RETRY_CAP)
    for index, text in enumerate(texts):
        if deadline is not None and time.monotonic() >= deadline:
            result.extend([] for _ in texts[index:])
            return result, True
        if index >= _PER_TEXT_PROBE_LIMIT and failures >= _PER_TEXT_PROBE_LIMIT:
            result.extend([] for _ in texts[index:])
            break
        vector: list[float] | None = None
        for attempt in range(per_text_attempts + 1):
            if deadline is not None and time.monotonic() >= deadline:
                result.extend([] for _ in texts[index:])
                return result, True
            single = embed_texts([text], emb_cfg)
            if single and single[0]:
                vector = single[0]
                break
            if attempt < per_text_attempts and retry_sleep > 0 and _bounded_sleep(retry_sleep, deadline):
                result.extend([] for _ in texts[index:])
                return result, True
        if vector is not None:
            result.append(vector)
        else:
            result.append([])
            failures += 1
    return result, False


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
    "--deadline-seconds",
    default=120.0,
    show_default=True,
    help="Global wall-clock budget for retries; 0 disables the deadline.",
)
@click.option(
    "--heartbeat-seconds",
    default=15.0,
    show_default=True,
    help="Seconds between heartbeat progress lines when --progress-lines is set; 0 disables heartbeats.",
)
@click.option(
    "--progress-lines", is_flag=True, help="Write progress as newline records for log-friendly real-time output."
)
@click.option("--dry-run", is_flag=True, help="Show what would be embedded without calling the provider")
@click.pass_context
def workspace_embed(
    ctx: click.Context,
    name: str,
    batch_size: int,
    limit: int,
    max_retries: int,
    retry_sleep: float,
    heartbeat_seconds: float,
    deadline_seconds: float,
    progress_lines: bool,
    dry_run: bool,
) -> None:
    """Backfill embeddings for an existing workspace index (writes vectors to Qdrant)."""
    json_out = output_json(ctx)
    load_workspace_or_exit(ctx, name, context="workspace embed")
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
    if deadline_seconds < 0:
        emit_error(
            "validation_error",
            "--deadline-seconds must be 0 or greater",
            output_json=json_out,
            context="workspace embed",
        )

    emb_cfg = load_embedding_config(apply_env_overrides=True)
    if dry_run:
        stats = _read_index_stats(idx_path)
        emit_result(
            ctx,
            {
                "name": name,
                "provider_configured": emb_cfg.is_configured,
                "provider": emb_cfg.provider,
                "model": emb_cfg.model,
                "batch_size": batch_size,
                "limit": limit,
                "max_retries": max_retries,
                "retry_sleep": retry_sleep,
                "heartbeat_seconds": heartbeat_seconds,
                "deadline_seconds": deadline_seconds,
                **stats,
            },
            human_text=f"[dry-run] Would backfill embeddings for '{name}' from the existing index",
            dry_run=True,
        )
        return
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
    counters = {"attempted": 0, "stored": 0, "skipped": 0}
    counters_lock = threading.Lock()
    stop_heartbeat = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    complete = False
    attempted = 0
    stored = 0
    skipped = 0
    remaining = 0
    elapsed = 0.0
    deadline_hit = False
    deadline = t0 + deadline_seconds if deadline_seconds > 0 else None
    if progress_lines and heartbeat_seconds > 0:

        def heartbeat_writer() -> None:
            while not stop_heartbeat.wait(heartbeat_seconds):
                with counters_lock:
                    counters_snapshot = dict(counters)
                emit_progress(
                    ctx,
                    "  [embed] heartbeat elapsed="
                    f"{time.monotonic() - t0:.0f}s attempted={counters_snapshot['attempted']} "
                    f"stored={counters_snapshot['stored']} skipped={counters_snapshot['skipped']}",
                )

        heartbeat_thread = threading.Thread(target=heartbeat_writer, name="embed-heartbeat", daemon=True)
        heartbeat_thread.start()
    try:
        existing = set(vector_store.list_ids())
        all_ids = idx.get_chunk_ids()
        missing = [cid for cid in all_ids if cid not in existing]
        if not missing:
            complete = True
        else:
            target = min(len(missing), limit) if limit else len(missing)
            missing = missing[:target]
            emit_progress(
                ctx,
                f"  Backfilling embeddings for '{name}': missing={len(missing)} "
                f"provider={emb_cfg.provider} model={emb_cfg.model} batch_size={batch_size}",
            )

            for i in range(0, len(missing), batch_size):
                if deadline is not None and time.monotonic() >= deadline:
                    deadline_hit = True
                    skipped += len(missing) - i
                    emit_warning(
                        f"embedding stopped after reaching the {deadline_seconds:.0f}s deadline; "
                        f"rerun 'zot workspace embed {name}' to continue"
                    )
                    break
                batch_ids = missing[i : i + batch_size]
                chunks = idx.get_chunks_by_ids(batch_ids)
                texts = [chunks[cid]["content"] for cid in batch_ids if cid in chunks]
                vectors, batch_deadline_hit = _embed_batch_with_retries(
                    texts, emb_cfg, max_retries, retry_sleep, deadline
                )
                if batch_deadline_hit:
                    deadline_hit = True

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
                with counters_lock:
                    counters.update({"attempted": attempted, "stored": stored, "skipped": skipped})

                status = f"  [embed] attempted={attempted}/{len(missing)} stored={stored} skipped={skipped}"
                if progress_lines:
                    emit_progress(ctx, status)
                else:
                    sys.stderr.write(f"\r{' ' * 100}\r{status}")
                    sys.stderr.flush()
                if deadline_hit:
                    remaining_count = len(missing) - i - len(batch_ids)
                    if remaining_count > 0:
                        skipped += remaining_count
                    emit_warning(
                        f"embedding stopped after reaching the {deadline_seconds:.0f}s deadline; "
                        f"rerun 'zot workspace embed {name}' to continue"
                    )
                    break

            if not progress_lines:
                sys.stderr.write(f"\r{' ' * 100}\r")
                sys.stderr.flush()
            remaining = sum(1 for cid in idx.get_chunk_ids() if cid not in set(vector_store.list_ids()))
            elapsed = time.monotonic() - t0
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2.0)
        vector_store.close()
        idx.close()

    if complete:
        emit_result(
            ctx,
            {"name": name, "missing": 0, "complete": True},
            human_text=f"Embeddings for '{name}' are already complete.",
            meta={"count": 0},
        )
        return
    failed_chunks = max(len(missing) - stored, skipped)
    if stored == 0 and failed_chunks > 0 and deadline_hit:
        emit_error(
            "network_error",
            f"Embedding stopped at the {deadline_seconds:.0f}s deadline without storing any vectors "
            f"for workspace '{name}' ({failed_chunks} chunk(s) pending)",
            output_json=json_out,
            retryable=True,
            hint="Check the embedding provider configuration/network/model/quota (permanent 4xx errors "
            "are not retryable), reduce --limit/--batch-size, or retry 'zot workspace embed'",
            context="workspace embed",
        )
    if stored == 0 and failed_chunks > 0:
        emit_error(
            "network_error",
            f"Embedding failed for all {failed_chunks} chunk(s) in workspace '{name}'",
            output_json=json_out,
            retryable=True,
            hint="Check the embedding provider configuration/network/model/quota; permanent 4xx "
            "errors (unsupported model, exhausted account) are not retried further",
            context="workspace embed",
        )
    data = {
        "name": name,
        "attempted": attempted,
        "stored": stored,
        "skipped": skipped,
        "remaining_missing": remaining,
        "elapsed_s": round(elapsed, 1),
        "deadline_hit": deadline_hit,
        "deadline_seconds": deadline_seconds,
    }
    human = (
        f"Backfilled embeddings for '{name}': attempted={attempted}, stored={stored}, "
        f"skipped={skipped}, remaining_missing={remaining}, elapsed={elapsed:.1f}s"
    )
    if deadline_hit:
        human += " (stopped at deadline)"
    emit_result(ctx, data, human_text=human, meta={"count": stored})
