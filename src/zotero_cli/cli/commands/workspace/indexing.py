"""workspace 索引构建、MinerU 导入与索引状态辅助函数。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path

import typer

from zotero_cli.ai_pdf import MinerUError, MinerUParseCache, MinerUParseResult
from zotero_cli.cli.commands.workspace._output import (
    emit_progress,
    emit_result,
    emit_warning,
    load_workspace_or_exit,
    output_json,
)
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_config,
    load_embedding_config,
    load_vector_store_config,
    resolve_library_id,
)
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.workspace.rag import (
    build_metadata_chunk,
    chunk_mineru_content,
    embed_texts,
    infer_pdf_kind,
    tokenize,
)
from zotero_cli.workspace.rag.index import RagIndex
from zotero_cli.workspace.rag.vector_store import (
    QdrantVectorStore,
    drop_collection,
    is_local_vector_store,
    prune_empty_store,
    resolve_vector_store_path,
)
from zotero_cli.workspace.store import (
    WorkspaceItem,
    workspace_index_path,
)
from zotero_cli.zotero.models import Item
from zotero_cli.zotero.reader import ZoteroReader

# 为每个条目存进索引 metadata；当 chunk 切分方式或 MinerU 流水线变化时递增它，
# 使增量重建索引能识别该变化。
_RAG_PIPELINE_VERSION = "mineru-json-v1"

_ITEM_HINT = "Use 'zot workspace list' to see available workspaces"


def _collect_pdf_refs(reader: ZoteroReader, item_key: str) -> list[tuple[str, str, Path]]:
    refs: list[tuple[str, str, Path]] = []
    for att in reader.get_pdf_attachments(item_key):
        if att.path is None or not att.path.exists():
            continue
        refs.append((att.key, att.filename or att.key, att.path))
    return refs


def _collect_note_refs(reader: ZoteroReader, item_key: str) -> list[tuple[str, str]]:
    return [(note.key, note.content) for note in reader.get_notes(item_key) if note.content.strip()]


def _compute_pdf_hash(pdf_refs: list[tuple[str, str, Path]]) -> str:
    parts: list[str] = []
    for _key, _name, path in sorted(pdf_refs, key=lambda ref: str(ref[2])):
        try:
            stat = path.stat()
            parts.append(f"{path}:{stat.st_size}:{stat.st_mtime_ns}")
        except OSError:
            parts.append(f"{path}:missing")
    return sha1("|".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()


def _compute_content_hash(item: Item, notes: list[tuple[str, str]]) -> str:
    authors = ", ".join(c.full_name for c in item.creators)
    payload = [build_metadata_chunk(item.title, authors, item.abstract, item.tags), sorted(notes)]
    return sha1(json.dumps(payload, ensure_ascii=False).encode("utf-8"), usedforsecurity=False).hexdigest()


def _vector_store_for(name: str) -> QdrantVectorStore:
    config = load_vector_store_config()
    return QdrantVectorStore(resolve_vector_store_path(config), f"ws_{name}")


def drop_workspace_vector_collection(name: str) -> bool:
    """删除该 workspace 的本地 Qdrant collection，并清理空的 store。

    绝不创建 store 目录；对从未产生过向量的 workspace 调用是安全的。即使指定的 collection 不存在
    也会执行空 store 清理，因此只产生过空 store 的 workspace 也能被清理干净。
    """
    store_path = resolve_vector_store_path(load_vector_store_config())
    if not is_local_vector_store(store_path):
        return False
    removed = drop_collection(store_path, f"ws_{name}")
    prune_empty_store(store_path)
    return removed


def _read_index_stats(index_path: Path) -> dict[str, object]:
    """在不创建或写入索引的情况下读取 chunk 与条目数量。"""
    stats: dict[str, object] = {"index_exists": index_path.exists(), "chunks": 0, "indexed_items": 0}
    if not index_path.exists():
        return stats
    try:
        conn = sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return stats
    try:
        stats["chunks"] = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        stats["indexed_items"] = int(conn.execute("SELECT COUNT(DISTINCT item_key) FROM chunks").fetchone()[0])
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return stats


def open_vector_store_for_read(name: str) -> QdrantVectorStore | None:
    """仅当 workspace 向量 store 已存在时才打开它。

    只读路径（query）绝不能创建本地 store：目录缺失仅表示“没有向量”，而不可读或只读的 store
    会降级为仅用 BM25，而不是让命令失败或写入磁盘。
    """
    store_path = resolve_vector_store_path(load_vector_store_config())
    if not store_path.is_dir():
        return None
    try:
        return QdrantVectorStore(store_path, f"ws_{name}")
    except (OSError, RuntimeError, ValueError):
        return None


def prune_workspace_items(name: str, keys: list[str]) -> int:
    """清理已从 workspace manifest 中移除的条目 key 所对应的 chunk、meta 与向量。"""
    if not keys:
        return 0
    index_path = workspace_index_path(name)
    if not index_path.exists():
        return 0
    store_path = resolve_vector_store_path(load_vector_store_config())
    idx = RagIndex(index_path)
    pruned = 0
    try:
        for key in keys:
            old_ids = idx.delete_chunks_for_item(key)
            if old_ids and store_path.is_dir():
                store = _vector_store_for(name)
                try:
                    store.delete(old_ids)
                finally:
                    store.close()
            idx.delete_meta(f"pdf_hash:{key}")
            idx.delete_meta(f"content_hash:{key}")
            idx.delete_meta(f"pipeline:{key}")
            pruned += 1
        remaining = idx.get_indexed_keys()
        if not remaining:
            idx.set_meta("chunk_count", "0")
            idx.set_meta("indexed_at", datetime.now(UTC).isoformat())
        idx.commit()
    finally:
        idx.close()
    if not remaining:
        drop_collection(store_path, f"ws_{name}")
        prune_empty_store(store_path)
    return pruned


def _clear_workspace_index(name: str, index_path: Path, *, force: bool) -> int:
    """为空 workspace 清理所有已索引条目（使用 --force 时清空全部内容）。"""
    store_path = resolve_vector_store_path(load_vector_store_config())
    pruned = 0
    if index_path.exists():
        idx = RagIndex(index_path)
        try:
            indexed = idx.get_indexed_keys()
            pruned = len(indexed)
            if force:
                idx.clear()
            else:
                for key in indexed:
                    idx.delete_chunks_for_item(key)
                    idx.delete_meta(f"pdf_hash:{key}")
                    idx.delete_meta(f"content_hash:{key}")
                    idx.delete_meta(f"pipeline:{key}")
            idx.set_meta("chunk_count", "0")
            idx.set_meta("indexed_at", datetime.now(UTC).isoformat())
            idx.commit()
        finally:
            idx.close()
    drop_collection(store_path, f"ws_{name}")
    prune_empty_store(store_path)
    return pruned


def _prune_item_from_index(idx: RagIndex, vector_store: QdrantVectorStore, key: str) -> None:
    old_ids = idx.delete_chunks_for_item(key)
    if old_ids:
        vector_store.delete(old_ids)
    idx.delete_meta(f"pdf_hash:{key}")
    idx.delete_meta(f"content_hash:{key}")
    idx.delete_meta(f"pipeline:{key}")


def _index_workspace(
    ctx: typer.Context,
    name: str,
    *,
    force: bool,
    progress_lines: bool,
    item_progress: bool,
    no_embed: bool,
    dry_run: bool,
) -> None:
    json_out = output_json(ctx)
    workspace = load_workspace_or_exit(ctx, name, context="workspace index", hint=_ITEM_HINT)
    index_path = workspace_index_path(name)
    if not workspace.items:
        stats = _read_index_stats(index_path)
        if dry_run:
            emit_result(
                ctx,
                {
                    "name": name,
                    "force": force,
                    "items": 0,
                    "would_index": 0,
                    "would_prune": stats.get("indexed_items", 0),
                    **stats,
                },
                human_text=f"[dry-run] Workspace '{name}' is empty; nothing would be indexed"
                + (
                    f" (would prune {stats.get('indexed_items', 0)} stale item(s))"
                    if stats.get("indexed_items")
                    else ""
                ),
                dry_run=True,
            )
            return
        pruned = _clear_workspace_index(name, index_path, force=force)
        human = f"Workspace '{name}' is empty."
        if pruned:
            human += f" Cleared {pruned} stale item(s) from its index."
        else:
            human += f" Add items first with: zot workspace add {name} KEY"
        emit_result(
            ctx,
            {"name": name, "indexed": 0, "chunks": 0, "pruned": pruned, "items": 0, "force": force},
            human_text=human,
            meta={"count": 0},
        )
        return

    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
    try:
        entries: list[tuple[WorkspaceItem, Item, list[tuple[str, str, Path]], list[tuple[str, str]]]] = []
        missing_keys: list[str] = []
        for ws_item in workspace.items:
            item = reader.get_item(ws_item.key)
            if item is None:
                missing_keys.append(ws_item.key)
                continue
            entries.append(
                (ws_item, item, _collect_pdf_refs(reader, ws_item.key), _collect_note_refs(reader, ws_item.key))
            )

        if dry_run:
            stats = _read_index_stats(index_path)
            unique_paths = {path for _w, _i, refs, _n in entries for _k, _n2, path in refs}
            data = {
                "name": name,
                "force": force,
                "no_embed": no_embed,
                "items": len(workspace.items),
                "would_index": len(entries),
                "missing": missing_keys,
                "pdf_attachments": len(unique_paths),
                **stats,
            }
            emit_result(
                ctx,
                data,
                human_text=(
                    f"[dry-run] Would index {len(entries)} item(s)"
                    f" ({len(unique_paths)} PDF attachment(s)) for workspace '{name}'"
                    + (" after clearing the existing index" if force else "")
                ),
                dry_run=True,
            )
            return

        candidate_index_path = (
            index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.candidate") if force else None
        )
        idx = RagIndex(candidate_index_path or index_path)
        candidate_replaced = False
        vector_store = _vector_store_for(name)

        try:
            already_indexed = idx.get_indexed_keys()
            manifest_keys = {entry[0].key for entry in entries}
            stale_keys = sorted((already_indexed - manifest_keys) | (set(missing_keys) & already_indexed))
            up_to_date: list[str] = []
            to_index: list[tuple[WorkspaceItem, Item, list[tuple[str, str, Path]], list[tuple[str, str]]]] = []
            if force:
                to_index = list(entries)
            else:
                for entry in entries:
                    ws_item, item, pdf_refs, note_refs = entry
                    pdf_hash = _compute_pdf_hash(pdf_refs)
                    content_hash = _compute_content_hash(item, note_refs)
                    stored_hash = idx.get_meta(f"pdf_hash:{ws_item.key}")
                    stored_content = idx.get_meta(f"content_hash:{ws_item.key}")
                    stored_pipeline = idx.get_meta(f"pipeline:{ws_item.key}")
                    if (
                        ws_item.key in already_indexed
                        and stored_hash == pdf_hash
                        and stored_content == content_hash
                        and stored_pipeline == _RAG_PIPELINE_VERSION
                    ):
                        up_to_date.append(ws_item.key)
                        continue
                    to_index.append(entry)

            if not to_index and not stale_keys and not force:
                emit_result(
                    ctx,
                    {
                        "name": name,
                        "indexed": 0,
                        "chunks": 0,
                        "pruned": 0,
                        "up_to_date": len(up_to_date),
                        "up_to_date_keys": up_to_date,
                    },
                    human_text=f"Index for '{name}' is up to date ({len(already_indexed)} item(s) indexed).",
                    meta={"count": 0, "up_to_date": len(up_to_date)},
                )
                return

            t0 = time.monotonic()

            def progress_message(phase: str, current: int, total: int) -> str:
                percent = (current / total * 100) if total else 0.0
                return f"  [{phase}] {current}/{total} ({percent:.1f}%) elapsed={time.monotonic() - t0:.1f}s"

            def emit_step(phase: str, current: int, total: int) -> None:
                message = progress_message(phase, current, total)
                if progress_lines or item_progress:
                    emit_progress(ctx, message)
                else:
                    sys.stderr.write(f"\r{' ' * 100}\r{message}")
                    sys.stderr.flush()

            def clear_progress_status() -> None:
                if not progress_lines and not item_progress:
                    sys.stderr.write(f"\r{' ' * 100}\r")
                    sys.stderr.flush()

            # 阶段 1 - 在任何索引变更之前先获取规范的 MinerU 解析包，这样配置或网络
            # 故障就不会破坏一个可用的索引。
            unique_pdf_paths: list[Path] = list(
                dict.fromkeys(path for _ws, _item, refs, _notes in to_index for _k, _n, path in refs)
            )
            batch_pdf_parses: dict[Path, MinerUParseResult | Exception] = {}
            pdf_errors: list[tuple[str, str, Exception]] = []
            if unique_pdf_paths:
                emit_progress(ctx, f"  Parsing {len(unique_pdf_paths)} PDF attachment(s) with MinerU cloud API...")

                def batch_progress(phase: str, current: int, total: int, pages: int) -> None:
                    _ = pages
                    emit_step(f"extract:{phase}", current, total)

                try:
                    batch_pdf_parses = MinerUParseCache().ensure_many(unique_pdf_paths, batch_progress)
                except MinerUError as exc:
                    error_code = (
                        "configuration_error" if "token not configured" in str(exc).lower() else "network_error"
                    )
                    emit_error(
                        error_code,
                        str(exc),
                        output_json=json_out,
                        retryable=error_code == "network_error",
                        hint=(
                            "Set [pdf].mineru_token in .zot/config.toml"
                            if error_code == "configuration_error"
                            else "Check the MinerU token, API status, and network connection"
                        ),
                        context="workspace index",
                    )
                clear_progress_status()

            # 阶段 2 - 切分 MinerU 结构化 JSON；Markdown 保留给 AI 笔记。
            emit_progress(ctx, f"  Chunking {len(to_index)} item(s)...")
            all_chunks: list[tuple[str, str, str, int]] = []
            item_chunk_counts: list[int] = []
            failed_item_keys: set[str] = set()
            for ws_item, item, pdf_refs, note_refs in to_index:
                authors = ", ".join(c.full_name for c in item.creators)
                meta_text = build_metadata_chunk(item.title, authors, item.abstract, item.tags)
                all_chunks.append((ws_item.key, "metadata", meta_text, len(tokenize(meta_text))))
                for note_key, note_content in note_refs:
                    note_text = f"Title: {item.title}\nNote Key: {note_key}\nContent:\n{note_content}"
                    all_chunks.append((ws_item.key, f"note:{note_key}", note_text, len(tokenize(note_text))))
                for att_key, pdf_name, pdf_path in pdf_refs:
                    parsed = batch_pdf_parses.get(pdf_path)
                    if isinstance(parsed, Exception):
                        pdf_errors.append((ws_item.key, pdf_name, parsed))
                        failed_item_keys.add(ws_item.key)
                        continue
                    if not isinstance(parsed, MinerUParseResult):
                        continue
                    pdf_kind = infer_pdf_kind(parsed.markdown, pdf_name)
                    labeled_title = f"{item.title} | PDF: {pdf_name} | Attachment: {att_key} | Kind: {pdf_kind}"
                    structured_chunks = chunk_mineru_content(parsed.content_list, labeled_title)
                    if not structured_chunks:
                        error = MinerUError("MinerU content_list produced no searchable blocks")
                        pdf_errors.append((ws_item.key, pdf_name, error))
                        failed_item_keys.add(ws_item.key)
                        continue
                    for structured_chunk in structured_chunks:
                        page_label = structured_chunk.page if structured_chunk.page is not None else 0
                        source = f"pdf:{pdf_kind}:{att_key}:{pdf_name}:p{page_label}:{structured_chunk.block_type}"
                        all_chunks.append(
                            (
                                ws_item.key,
                                source,
                                structured_chunk.content,
                                len(tokenize(structured_chunk.content)),
                            )
                        )
                item_chunk_counts.append(len(all_chunks) - sum(item_chunk_counts))

            # 仅在解析完成后修改索引。强制重建使用候选 SQLite 文件，直到候选索引构建完成
            # 才替换可用索引，避免 MinerU 或构建失败时丢失结果。
            if force:
                pass
            else:
                for stale_key in stale_keys:
                    _prune_item_from_index(idx, vector_store, stale_key)
                for ws_item, _item, _pdf_refs, _notes in to_index:
                    if ws_item.key in already_indexed:
                        _prune_item_from_index(idx, vector_store, ws_item.key)
            idx.commit()

            # 阶段 3 - 写入词项索引（SQLite FTS5）。
            emit_progress(ctx, f"  Indexing {len(all_chunks)} chunk(s)...")
            chunk_ids: list[int] = []
            chunk_texts: list[str] = []
            inserted = 0
            if item_progress:
                total_items = len(item_chunk_counts)
                for item_number, chunk_count in enumerate(item_chunk_counts, 1):
                    item_chunks = all_chunks[inserted : inserted + chunk_count]
                    item_key = item_chunks[0][0] if item_chunks else ""
                    for key, source, content, doc_len in item_chunks:
                        chunk_id = idx.insert_chunk_no_commit(key, source, content, doc_len)
                        chunk_ids.append(chunk_id)
                        chunk_texts.append(content)
                    idx.commit()
                    inserted += chunk_count
                    emit_progress(
                        ctx,
                        f"  [item] {item_number}/{total_items} {item_key} ({chunk_count} chunk(s))",
                    )
            else:
                for chunk_i, (key, source, content, doc_len) in enumerate(all_chunks, 1):
                    if chunk_i % 500 == 0 or chunk_i == len(all_chunks):
                        emit_step("index", chunk_i, len(all_chunks))
                    chunk_id = idx.insert_chunk_no_commit(key, source, content, doc_len)
                    chunk_ids.append(chunk_id)
                    chunk_texts.append(content)
                idx.commit()
            clear_progress_status()

            # 阶段 4 - 用配置的 provider 做 embedding 并将向量写入本地 Qdrant。
            mode_label = "BM25 (FTS5)"
            emb_cfg = None if no_embed else load_embedding_config(apply_env_overrides=True)
            if emb_cfg and emb_cfg.is_configured and chunk_texts:
                emit_progress(ctx, "  Generating embeddings...")
                try:
                    vectors = embed_texts(chunk_texts, emb_cfg) or []
                    if vectors:
                        payloads = [{"item_key": key, "source": source} for key, source, _c, _d in all_chunks]
                        valid_ids: list[int] = []
                        valid_vectors: list[list[float]] = []
                        valid_payloads: list[dict] = []
                        for chunk_id, vector, payload in zip(chunk_ids, vectors, payloads):
                            if vector:
                                valid_ids.append(chunk_id)
                                valid_vectors.append(vector)
                                valid_payloads.append(payload)
                        if valid_ids:
                            vector_store.upsert(valid_ids, valid_vectors, valid_payloads)
                        mode_label = "BM25 + embeddings"
                except Exception as exc:
                    emit_warning(f"embedding failed: {exc}; workspace index keeps BM25 term search")
                clear_progress_status()

            total_chunks = len(all_chunks)
            idx.set_meta("chunk_count", str(total_chunks))
            idx.set_meta("indexed_at", datetime.now(UTC).isoformat())
            for ws_item, _item, pdf_refs, _notes in to_index:
                if ws_item.key in failed_item_keys:
                    idx.delete_meta(f"pdf_hash:{ws_item.key}")
                    idx.delete_meta(f"content_hash:{ws_item.key}")
                    idx.delete_meta(f"pipeline:{ws_item.key}")
                else:
                    idx.set_meta(f"pdf_hash:{ws_item.key}", _compute_pdf_hash(pdf_refs))
                    idx.set_meta(f"content_hash:{ws_item.key}", _compute_content_hash(_item, _notes))
                    idx.set_meta(f"pipeline:{ws_item.key}", _RAG_PIPELINE_VERSION)

            for key, pdf_name, pdf_error in pdf_errors:
                emit_warning(f"PDF extraction failed for {key} ({pdf_name}): {pdf_error}")

            elapsed = time.monotonic() - t0
            if candidate_index_path is not None:
                idx.close()
                os.replace(candidate_index_path, index_path)
                candidate_replaced = True
            human = f"Indexed {len(to_index)} item(s) ({total_chunks} chunks) in {elapsed:.1f}s [{mode_label}]"
            if stale_keys:
                human += f"; pruned {len(stale_keys)} stale item(s)"
            data = {
                "name": name,
                "indexed": len(to_index),
                "chunks": total_chunks,
                "pruned": len(stale_keys),
                "up_to_date": len(up_to_date),
                "mode": mode_label,
                "elapsed_s": round(elapsed, 2),
                "pdf_errors": [
                    {"item_key": key, "pdf": pdf_name, "error": str(pdf_error)}
                    for key, pdf_name, pdf_error in pdf_errors
                ],
            }
            emit_result(ctx, data, human_text=human, meta={"count": len(to_index)})
        finally:
            vector_store.close()
            if not candidate_replaced:
                idx.close()
            if candidate_index_path is not None and candidate_index_path.exists():
                candidate_index_path.unlink()
    finally:
        reader.close()


def workspace_index(
    ctx: typer.Context,
    name: str,
    force: bool,
    progress_lines: bool,
    item_progress: bool,
    no_embed: bool,
    dry_run: bool,
) -> None:
    """构建 workspace 语义索引（MinerU PDF -> Gitee embedding + SQLite FTS5）。"""
    _index_workspace(
        ctx,
        name,
        force=force,
        progress_lines=progress_lines,
        item_progress=item_progress,
        no_embed=no_embed,
        dry_run=dry_run,
    )


def workspace_reindex(ctx: typer.Context, name: str, progress_lines: bool, no_embed: bool, dry_run: bool) -> None:
    """强制完整重建 workspace 索引。"""
    _index_workspace(
        ctx,
        name,
        force=True,
        progress_lines=progress_lines,
        item_progress=False,
        no_embed=no_embed,
        dry_run=dry_run,
    )
