from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

import click

from zotero_cli.config import (
    EmbeddingConfig,
    get_data_dir,
    get_prefs_js_path,
    load_config,
    load_embedding_config,
    load_rerank_config,
    load_semantic_search_config,
    load_vector_store_config,
    resolve_library_id,
)
from zotero_cli.core.rag import (
    build_metadata_chunk,
    chunk_text,
    convert_pdf_to_text,
    convert_pdfs_to_text,
    embed_texts,
    filter_ranked_results_by_pdf_kind,
    infer_pdf_kind,
    tokenize,
    weighted_reciprocal_rank_fusion,
)
from zotero_cli.core.rag_index import RagIndex
from zotero_cli.core.reader import ZoteroReader
from zotero_cli.core.rerank import rerank_chunks
from zotero_cli.core.semantic_search import QdrantVectorStore, resolve_vector_store_path
from zotero_cli.core.workspace import (
    Workspace,
    WorkspaceItem,
    delete_workspace,
    list_workspaces,
    load_workspace,
    save_workspace,
    validate_name,
    workspace_exists,
    workspace_index_path,
    workspaces_dir,
)
from zotero_cli.exit_codes import emit_error
from zotero_cli.formatter import envelope_ok, format_items, format_workspace_list, format_workspace_query
from zotero_cli.models import Collection, Item

_TEST_PATCH_SEAMS = (convert_pdf_to_text, workspaces_dir)


@click.group("workspace", invoke_without_command=True)
@click.pass_context
def workspace_group(ctx: click.Context) -> None:
    """Manage local workspaces for organizing papers by topic."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@workspace_group.command("new")
@click.argument("name")
@click.option("--description", "-d", default="", help="Workspace description (topic context)")
@click.pass_context
def workspace_new(ctx: click.Context, name: str, description: str) -> None:
    """Create a new workspace."""
    json_out = ctx.obj.get("json", False)
    if not validate_name(name):
        emit_error(
            "validation_error",
            f"Invalid workspace name: '{name}'",
            output_json=json_out,
            hint="Use kebab-case (e.g., llm-safety, protein-folding)",
            context="workspace new",
        )
    if workspace_exists(name):
        emit_error(
            "conflict",
            f"Workspace '{name}' already exists",
            output_json=json_out,
            hint=f"Use 'zot workspace show {name}' to view it",
            context="workspace new",
        )
    ws = Workspace(
        name=name,
        created=datetime.now(timezone.utc).isoformat(),
        description=description,
    )
    save_workspace(ws)
    click.echo(f"Workspace created: {name}")


@workspace_group.command("delete")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def workspace_delete(ctx: click.Context, name: str, yes: bool) -> None:
    """Delete a workspace."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace delete",
        )
    no_interaction = ctx.obj.get("no_interaction", False)
    if not yes and not no_interaction:
        if not click.confirm(f"Delete workspace '{name}'?"):
            click.echo("Cancelled.")
            return
    delete_workspace(name)
    click.echo(f"Workspace deleted: {name}")


@workspace_group.command("add")
@click.argument("name")
@click.argument("keys", nargs=-1, required=True)
@click.pass_context
def workspace_add(ctx: click.Context, name: str, keys: tuple[str, ...]) -> None:
    """Add items to a workspace by Zotero key."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace new' to create it first",
            context="workspace add",
        )

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        ws = load_workspace(name)
        added = 0
        for key in keys:
            item = reader.get_item(key)
            if item is None:
                click.echo(f"Warning: item '{key}' not found in Zotero library, skipped")
                continue
            if ws.add_item(key, item.title):
                added += 1
            else:
                click.echo(f"Skipped: '{key}' already in workspace")
        save_workspace(ws)
        click.echo(f"Added {added} item(s) to workspace '{name}'")
    finally:
        reader.close()


@workspace_group.command("remove")
@click.argument("name")
@click.argument("keys", nargs=-1, required=True)
@click.pass_context
def workspace_remove(ctx: click.Context, name: str, keys: tuple[str, ...]) -> None:
    """Remove items from a workspace by key."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace remove",
        )
    ws = load_workspace(name)
    removed = 0
    for key in keys:
        if ws.remove_item(key):
            removed += 1
    save_workspace(ws)
    click.echo(f"Removed {removed} item(s) from workspace '{name}'")


@workspace_group.command("list")
@click.pass_context
def workspace_list(ctx: click.Context) -> None:
    """List all workspaces."""
    json_out = ctx.obj.get("json", False)
    workspaces = list_workspaces()
    if not workspaces:
        click.echo("No workspaces found. Create one with: zot workspace new <name>")
        return
    click.echo(format_workspace_list(workspaces, output_json=json_out))


@workspace_group.command("show")
@click.argument("name")
@click.pass_context
def workspace_show(ctx: click.Context, name: str) -> None:
    """Show items in a workspace."""
    json_out = ctx.obj.get("json", False)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)

    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace show",
        )

    ws = load_workspace(name)
    if not ws.items:
        click.echo(f"Workspace '{name}' is empty. Use 'zot workspace add {name} KEY' to add items.")
        return

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        items = []
        missing = []
        for ws_item in ws.items[:limit]:
            item = reader.get_item(ws_item.key)
            if item is not None:
                items.append(item)
            else:
                missing.append(ws_item.key)
        if items:
            click.echo(format_items(items, output_json=json_out, detail=detail))
        for key in missing:
            click.echo(f"Warning: item '{key}' not found in Zotero library (may have been deleted)")
    finally:
        reader.close()


@workspace_group.command("export")
@click.argument("name")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "markdown", "bibtex"]),
    default="markdown",
    help="Export format (default: markdown)",
)
@click.pass_context
def workspace_export(ctx: click.Context, name: str, fmt: str) -> None:
    """Export workspace items for external use."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace export",
        )

    ws = load_workspace(name)
    if not ws.items:
        click.echo(f"Workspace '{name}' is empty.")
        return

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        items = []
        for ws_item in ws.items:
            item = reader.get_item(ws_item.key)
            if item is not None:
                items.append(item)

        if not items:
            click.echo("No items could be resolved from Zotero library.")
            return

        if fmt == "json":
            click.echo(format_items(items, output_json=True))
        elif fmt == "bibtex":
            entries = []
            for item in items:
                bib = reader.export_citation(item.key, fmt="bibtex")
                if bib:
                    entries.append(bib)
            click.echo("\n\n".join(entries))
        else:
            # markdown (default)
            lines = [f"# Workspace: {name}"]
            desc_part = f" {ws.description}" if ws.description else ""
            lines.append(f"> {desc_part.strip()} ({len(items)} items)")
            lines.append("")
            for i, item in enumerate(items, 1):
                lines.append("---")
                lines.append(f"## {i}. {item.title}")
                authors = ", ".join(c.full_name for c in item.creators[:3])
                if len(item.creators) > 3:
                    authors += " et al."
                year = item.date or "N/A"
                lines.append(f"**Authors:** {authors} | **Year:** {year} | **Key:** {item.key}")
                if item.tags:
                    lines.append(f"**Tags:** {', '.join(item.tags)}")
                if item.abstract:
                    lines.append(f"**Abstract:** {item.abstract}")
                lines.append("")
            click.echo("\n".join(lines))
    finally:
        reader.close()


@workspace_group.command("import")
@click.argument("name")
@click.option("--collection", default=None, help="Import all items from a Zotero collection (name or key)")
@click.option("--tag", default=None, help="Import all items with this tag")
@click.option("--search", "search_query", default=None, help="Import items matching a search query")
@click.pass_context
def workspace_import_cmd(
    ctx: click.Context, name: str, collection: str | None, tag: str | None, search_query: str | None
) -> None:
    """Bulk import items into a workspace from collection, tag, or search."""
    json_out = ctx.obj.get("json", False)
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace new' to create it first",
            context="workspace import",
        )

    if not collection and not tag and not search_query:
        emit_error(
            "validation_error",
            "Must specify at least one of --collection, --tag, or --search",
            output_json=json_out,
            hint="Example: zot workspace import my-ws --search 'attention'",
            context="workspace import",
        )

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        ws = load_workspace(name)
        items_to_import: list[Item] = []

        if collection:
            # Resolve collection name to key
            col_key = _resolve_collection_key(reader, collection)
            if col_key is None:
                emit_error(
                    "not_found",
                    f"Collection '{collection}' not found",
                    output_json=json_out,
                    hint="Use 'zot collections' to list available collections",
                    context="workspace import",
                )
            items_to_import.extend(reader.get_collection_items(col_key))

        if tag:
            # Search specifically for items with this tag
            result = reader.search(tag, limit=500)
            for item in result.items:
                if tag.lower() in [t.lower() for t in item.tags]:
                    items_to_import.append(item)

        if search_query:
            result = reader.search(search_query, limit=500)
            items_to_import.extend(result.items)

        # Dedup by key
        seen: set[str] = set()
        unique_items: list[Item] = []
        for item in items_to_import:
            if item.key not in seen:
                seen.add(item.key)
                unique_items.append(item)

        added = 0
        skipped = 0
        for item in unique_items:
            if ws.add_item(item.key, item.title):
                added += 1
            else:
                skipped += 1

        save_workspace(ws)
        click.echo(
            f"Imported {added} item(s) into workspace '{name}'"
            + (f" ({skipped} skipped, already present)" if skipped else "")
        )
    finally:
        reader.close()


@workspace_group.command("search")
@click.argument("query")
@click.option("--workspace", "ws_name", required=True, help="Workspace to search")
@click.pass_context
def workspace_search(ctx: click.Context, query: str, ws_name: str) -> None:
    """Search items within a workspace by title, author, or abstract."""
    json_out = ctx.obj.get("json", False)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)

    if not workspace_exists(ws_name):
        emit_error(
            "not_found",
            f"Workspace '{ws_name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace search",
        )

    ws = load_workspace(ws_name)
    if not ws.items:
        click.echo(f"Workspace '{ws_name}' is empty.")
        return

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        query_lower = query.lower()
        matches = []
        for ws_item in ws.items:
            item = reader.get_item(ws_item.key)
            if item is None:
                continue
            # Case-insensitive substring match across title, authors, abstract, tags
            searchable = " ".join(
                filter(
                    None,
                    [
                        item.title,
                        " ".join(c.full_name for c in item.creators),
                        item.abstract or "",
                        " ".join(item.tags),
                    ],
                )
            ).lower()
            if query_lower in searchable:
                matches.append(item)

        if not matches:
            click.echo("No matching items found.")
            return

        click.echo(format_items(matches[:limit], output_json=json_out, detail=detail))
    finally:
        reader.close()


def _resolve_collection_key(reader: ZoteroReader, name_or_key: str) -> str | None:
    """Resolve a collection name or key to a collection key."""
    collections = reader.get_collections()

    def _search(colls: list[Collection]) -> str | None:
        for c in colls:
            if c.key == name_or_key or c.name.lower() == name_or_key.lower():
                return c.key
            found = _search(c.children)
            if found:
                return found
        return None

    return _search(collections)


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
    return sha1("|".join(parts).encode("utf-8")).hexdigest()


def _vector_store_for(name: str) -> QdrantVectorStore:
    cfg = load_vector_store_config()
    return QdrantVectorStore(resolve_vector_store_path(cfg), f"ws_{name}")


def _index_workspace(
    ctx: click.Context,
    name: str,
    *,
    force: bool,
    extractor: str | None,
    progress_lines: bool,
    item_progress: bool,
    no_embed: bool,
) -> None:
    json_out = ctx.obj.get("json", False)
    if extractor is None:
        from zotero_cli.config import load_pdf_config

        extractor = load_pdf_config().extractor
    if not workspace_exists(name):
        emit_error(
            "not_found",
            f"Workspace '{name}' not found",
            output_json=json_out,
            hint="Use 'zot workspace list' to see available workspaces",
            context="workspace index",
        )

    ws = load_workspace(name)
    if not ws.items:
        click.echo(f"Workspace '{name}' is empty. Add items first with: zot workspace add {name} KEY")
        return

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))

    idx = RagIndex(workspace_index_path(name))
    vector_store = _vector_store_for(name)

    try:
        if force:
            idx.clear()
            vector_store.delete_all()

        already_indexed = idx.get_indexed_keys()
        emb_cfg = None if no_embed else load_embedding_config(apply_env_overrides=True)

        to_index: list[tuple[WorkspaceItem, Item, list[tuple[str, str, Path]], list[tuple[str, str]]]] = []
        for ws_item in ws.items:
            item = reader.get_item(ws_item.key)
            if item is None:
                click.echo(f"Warning: item '{ws_item.key}' not found in Zotero, skipped")
                continue
            pdf_refs = _collect_pdf_refs(reader, ws_item.key)
            note_refs = _collect_note_refs(reader, ws_item.key)
            pdf_hash = _compute_pdf_hash(pdf_refs)
            stored_hash = idx.get_meta(f"pdf_hash:{ws_item.key}")
            if ws_item.key in already_indexed and stored_hash == pdf_hash:
                continue
            if ws_item.key in already_indexed:
                old_ids = idx.delete_chunks_for_item(ws_item.key)
                if old_ids:
                    vector_store.delete(old_ids)
            to_index.append((ws_item, item, pdf_refs, note_refs))

        if not to_index:
            click.echo(f"Index for '{name}' is up to date ({len(already_indexed)} item(s) indexed).")
            return

        t0 = time.monotonic()

        def progress_message(phase: str, current: int, total: int) -> str:
            percent = (current / total * 100) if total else 0.0
            return f"  [{phase}] {current}/{total} ({percent:.1f}%) elapsed={time.monotonic() - t0:.1f}s"

        def emit_progress(phase: str, current: int, total: int) -> None:
            if progress_lines or item_progress:
                click.echo(progress_message(phase, current, total))
            else:
                sys.stderr.write(f"\r{' ' * 100}\r{progress_message(phase, current, total)}")
                sys.stderr.flush()

        def clear_progress_status() -> None:
            if not progress_lines and not item_progress:
                sys.stderr.write(f"\r{' ' * 100}\r")
                sys.stderr.flush()

        # Phase 1 — extract PDFs with the configured extractor (MinerU by default).
        unique_pdf_paths: list[Path] = list(
            dict.fromkeys(path for _ws, _item, refs, _notes in to_index for _k, _n, path in refs)
        )
        batch_pdf_texts: dict[Path, str | Exception] = {}
        pdf_errors: list[tuple[str, str, Exception]] = []
        if unique_pdf_paths:
            click.echo(f"  Extracting {len(unique_pdf_paths)} PDF attachment(s) with {extractor}...")

            def batch_progress(phase: str, current: int, total: int, pages: int) -> None:
                _ = pages
                emit_progress(f"extract:{phase}", current, total)

            batch_pdf_texts = convert_pdfs_to_text(unique_pdf_paths, extractor, batch_progress)
            clear_progress_status()

        # Phase 2 — chunk all texts (markdown-structured; main/supplementary preserved).
        click.echo(f"  Chunking {len(to_index)} item(s)...")
        all_chunks: list[tuple[str, str, str, int]] = []
        for ws_item, item, pdf_refs, note_refs in to_index:
            authors = ", ".join(c.full_name for c in item.creators)
            meta_text = build_metadata_chunk(item.title, authors, item.abstract, item.tags)
            all_chunks.append((ws_item.key, "metadata", meta_text, len(tokenize(meta_text))))

            for note_key, note_content in note_refs:
                note_text = f"Title: {item.title}\nNote Key: {note_key}\nContent:\n{note_content}"
                all_chunks.append((ws_item.key, f"note:{note_key}", note_text, len(tokenize(note_text))))

            for att_key, pdf_name, pdf_path in pdf_refs:
                pdf_text_or_err = batch_pdf_texts.get(pdf_path)
                if isinstance(pdf_text_or_err, Exception):
                    pdf_errors.append((ws_item.key, pdf_name, pdf_text_or_err))
                    continue
                if not isinstance(pdf_text_or_err, str) or not pdf_text_or_err.strip():
                    continue
                pdf_kind = infer_pdf_kind(pdf_text_or_err, pdf_name)
                labeled_title = f"{item.title} | PDF: {pdf_name} | Attachment: {att_key} | Kind: {pdf_kind}"
                for chunk_content in chunk_text(pdf_text_or_err, labeled_title):
                    all_chunks.append(
                        (
                            ws_item.key,
                            f"pdf:{pdf_kind}:{att_key}:{pdf_name}",
                            chunk_content,
                            len(tokenize(chunk_content)),
                        )
                    )

        # Phase 3 — write term index (SQLite FTS5).
        click.echo(f"  Indexing {len(all_chunks)} chunk(s)...")
        chunk_ids: list[int] = []
        chunk_texts: list[str] = []
        for chunk_i, (key, source, content, doc_len) in enumerate(all_chunks, 1):
            if chunk_i % 500 == 0 or chunk_i == len(all_chunks):
                emit_progress("index", chunk_i, len(all_chunks))
            chunk_id = idx.insert_chunk_no_commit(key, source, content, doc_len)
            chunk_ids.append(chunk_id)
            chunk_texts.append(content)
        idx.commit()
        clear_progress_status()

        # Phase 4 — embed with Gitee and write vectors to Qdrant local.
        mode_label = "BM25 (FTS5)"
        if emb_cfg and emb_cfg.is_configured and chunk_texts:
            click.echo("  Generating embeddings...")
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
            except Exception as e:
                click.echo(f"  [WARN] Embedding failed: {e}", err=True)
            clear_progress_status()

        total_chunks = len(all_chunks)
        idx.set_meta("chunk_count", str(total_chunks))
        idx.set_meta("indexed_at", datetime.now(timezone.utc).isoformat())
        for ws_item, _item, pdf_refs, _notes in to_index:
            idx.set_meta(f"pdf_hash:{ws_item.key}", _compute_pdf_hash(pdf_refs))

        if pdf_errors:
            click.echo(f"\nWarning: {len(pdf_errors)} PDF extraction(s) failed:")
            for key, pdf_name, exc in pdf_errors:
                click.echo(f"  - {key} ({pdf_name}): {exc}")

        elapsed = time.monotonic() - t0
        click.echo(f"Indexed {len(to_index)} item(s) ({total_chunks} chunks) in {elapsed:.1f}s [{mode_label}]")
    finally:
        vector_store.close()
        idx.close()
        reader.close()


@workspace_group.command("index")
@click.argument("name")
@click.option("--force", is_flag=True, help="Rebuild index from scratch")
@click.option("--extractor", default=None, help="PDF text extractor to use. Defaults to the configured MinerU extractor.")
@click.option("--progress-lines", is_flag=True, help="Write progress as newline records for log-friendly real-time output.")
@click.option("--item-progress", is_flag=True, help="Index and commit one workspace item at a time with per-item progress.")
@click.option("--no-embed", is_flag=True, help="Skip embedding generation during indexing; use workspace embed later.")
@click.pass_context
def workspace_index(
    ctx: click.Context,
    name: str,
    force: bool,
    extractor: str | None,
    progress_lines: bool,
    item_progress: bool,
    no_embed: bool,
) -> None:
    """Build the workspace semantic index (MinerU PDF -> Gitee embeddings + SQLite FTS5)."""
    _index_workspace(
        ctx,
        name,
        force=force,
        extractor=extractor,
        progress_lines=progress_lines,
        item_progress=item_progress,
        no_embed=no_embed,
    )


@workspace_group.command("reindex")
@click.argument("name")
@click.option("--extractor", default=None, help="PDF text extractor to use. Defaults to the configured MinerU extractor.")
@click.option("--progress-lines", is_flag=True, help="Write progress as newline records for log-friendly real-time output.")
@click.pass_context
def workspace_reindex(ctx: click.Context, name: str, extractor: str | None, progress_lines: bool) -> None:
    """Force a full rebuild of a workspace index."""
    _index_workspace(
        ctx,
        name,
        force=True,
        extractor=extractor,
        progress_lines=progress_lines,
        item_progress=False,
        no_embed=False,
    )


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
@click.option("--limit", default=0, show_default=True, help="Maximum chunks to attempt in this run; 0 means all missing.")
@click.option("--max-retries", default=5, show_default=True, help="Retry a failed provider batch this many times.")
@click.option("--retry-sleep", default=10.0, show_default=True, help="Seconds to sleep between batch retries.")
@click.option("--heartbeat-seconds", default=15.0, show_default=True, help="Seconds between progress lines when --progress-lines is set.")
@click.option("--progress-lines", is_flag=True, help="Write progress as newline records for log-friendly real-time output.")
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
        emit_error("validation_error", "--batch-size must be greater than 0", output_json=json_out, context="workspace embed")
    if limit < 0:
        emit_error("validation_error", "--limit must be 0 or greater", output_json=json_out, context="workspace embed")
    if max_retries < 0:
        emit_error("validation_error", "--max-retries must be 0 or greater", output_json=json_out, context="workspace embed")
    if retry_sleep < 0:
        emit_error("validation_error", "--retry-sleep must be 0 or greater", output_json=json_out, context="workspace embed")
    if heartbeat_seconds < 0:
        emit_error("validation_error", "--heartbeat-seconds must be 0 or greater", output_json=json_out, context="workspace embed")

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
                click.echo(json.dumps(envelope_ok([], meta={"count": 0, "mode": effective_mode}), indent=2, ensure_ascii=False))
            else:
                click.echo("No results found.")
            return
        click.echo(format_workspace_query(top, mode=effective_mode, output_json=json_out))
    finally:
        vector_store.close()
        idx.close()
