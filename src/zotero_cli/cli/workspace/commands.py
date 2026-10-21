"""Workspace browsing, mutation, import, and search commands."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import click

from zotero_cli.cli.workspace._output import (
    emit_result,
    emit_warning,
    load_workspace_if_readable,
    load_workspace_or_exit,
    output_json,
    workspace_directory_or_exit,
    workspace_exists_or_error,
)
from zotero_cli.cli.workspace.indexing import (
    drop_workspace_vector_collection,
    prune_workspace_items,
)
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.models import Collection, Item
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_items, format_workspace_list
from zotero_cli.workspace.store import (
    Workspace,
    cleanup_empty_workspace_root,
    delete_workspace,
    list_workspaces,
    save_workspace,
    validate_name,
    workspace_file_path,
)
from zotero_cli.zotero.reader import ZoteroReader

_ADD_HINT = "Use 'zot workspace new' to create it first"


def _open_reader(ctx: click.Context) -> ZoteroReader:
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    return ZoteroReader(db_path, library_id=library_id)


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


def workspace_new(ctx: click.Context, name: str, description: str, dry_run: bool) -> None:
    """Create a new workspace."""
    json_out = output_json(ctx)
    if not validate_name(name):
        emit_error(
            "validation_error",
            f"Invalid workspace name: '{name}'",
            output_json=json_out,
            hint="Use kebab-case (e.g., llm-safety, protein-folding)",
            context="workspace new",
        )
    if workspace_exists_or_error(ctx, name, context="workspace new"):
        emit_error(
            "conflict",
            f"Workspace '{name}' already exists",
            output_json=json_out,
            hint=f"Use 'zot workspace show {name}' to view it",
            context="workspace new",
        )
    created = datetime.now(UTC).isoformat()
    data = {"name": name, "description": description, "created": created, "items": 0}
    if dry_run:
        emit_result(
            ctx,
            {**data, "would_create": True},
            human_text=f"[dry-run] Would create workspace '{name}'",
            dry_run=True,
        )
        return
    save_workspace(Workspace(name=name, created=created, description=description))
    emit_result(ctx, data, human_text=f"Workspace created: {name}")


def workspace_delete(ctx: click.Context, name: str, yes: bool, dry_run: bool) -> None:
    """Delete a workspace."""
    json_out = output_json(ctx)
    workspace_directory_or_exit(ctx, name, context="workspace delete")
    try:
        manifest_path = workspace_file_path(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context="workspace delete")
    workspace = load_workspace_if_readable(name) if manifest_path.exists() else None
    item_count = len(workspace.items) if workspace is not None else None
    data: dict[str, object] = {"name": name, "items": item_count, "deleted": True}
    if dry_run:
        human = f"[dry-run] Would delete workspace '{name}'"
        if item_count is not None:
            human += f" ({item_count} item(s))"
        emit_result(ctx, {**data, "would_delete": True}, human_text=human, dry_run=True)
        return
    no_interaction = bool(ctx.obj.get("no_interaction", False))
    if not yes:
        if no_interaction or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Refusing to delete workspace '{name}' without confirmation on non-interactive stdin",
                output_json=json_out,
                hint="Pass --yes to confirm or use --dry-run to preview",
                context="workspace delete",
            )
        if not click.confirm(f"Delete workspace '{name}'?"):
            emit_result(ctx, {"name": name, "cancelled": True}, human_text="Cancelled.")
            return
    try:
        drop_workspace_vector_collection(name)
    except (OSError, RuntimeError, ValueError) as exc:
        emit_warning(f"could not remove vector data for workspace '{name}': {exc}")
    delete_workspace(name)
    cleanup_empty_workspace_root()
    if workspace is None:
        emit_warning(f"workspace manifest for '{name}' was unreadable; removed the directory anyway")
    emit_result(ctx, data, human_text=f"Workspace deleted: {name}")


def workspace_add(ctx: click.Context, name: str, keys: tuple[str, ...], dry_run: bool) -> None:
    """Add items to a workspace by Zotero key."""
    workspace = load_workspace_or_exit(ctx, name, context="workspace add", hint=_ADD_HINT)
    reader = _open_reader(ctx)
    added: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    try:
        for key in keys:
            item = reader.get_item(key)
            if item is None:
                missing.append(key)
                continue
            if workspace.add_item(key, item.title):
                added.append(key)
            else:
                skipped.append(key)
        data = {
            "name": name,
            "added": added,
            "skipped": skipped,
            "missing": missing,
            "items": len(workspace.items),
        }
        if dry_run:
            emit_result(
                ctx,
                {**data, "would_add": added},
                human_text=f"[dry-run] Would add {len(added)} item(s) to workspace '{name}'",
                dry_run=True,
            )
            return
        save_workspace(workspace)
        for key in missing:
            emit_warning(f"item '{key}' not found in Zotero library, skipped")
        for key in skipped:
            emit_warning(f"'{key}' already in workspace")
        emit_result(
            ctx, data, human_text=f"Added {len(added)} item(s) to workspace '{name}'", meta={"count": len(added)}
        )
    finally:
        reader.close()


def workspace_remove(ctx: click.Context, name: str, keys: tuple[str, ...], dry_run: bool) -> None:
    """Remove items from a workspace by key."""
    workspace = load_workspace_or_exit(ctx, name, context="workspace remove")
    removed: list[str] = []
    not_present: list[str] = []
    for key in keys:
        if workspace.remove_item(key):
            removed.append(key)
        else:
            not_present.append(key)
    data = {"name": name, "removed": removed, "not_present": not_present, "items": len(workspace.items)}
    if dry_run:
        emit_result(
            ctx,
            {**data, "would_remove": removed},
            human_text=f"[dry-run] Would remove {len(removed)} item(s) from workspace '{name}'",
            dry_run=True,
        )
        return
    save_workspace(workspace)
    index_refresh_required = False
    pruned_index_items = 0
    if removed:
        try:
            pruned_index_items = prune_workspace_items(name, removed)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            index_refresh_required = True
            emit_warning(
                f"could not prune the workspace index ({exc}); it may still return removed items until "
                f"you run 'zot workspace index {name}'"
            )
    data["pruned_index_items"] = pruned_index_items
    data["index_refresh_required"] = index_refresh_required
    human = f"Removed {len(removed)} item(s) from workspace '{name}'"
    if pruned_index_items:
        human += f"; pruned {pruned_index_items} item(s) from the index"
    emit_result(ctx, data, human_text=human, meta={"count": len(removed)})


def workspace_list(ctx: click.Context) -> None:
    """List all workspaces."""
    workspaces = list_workspaces()
    if not workspaces:
        emit_result(
            ctx,
            [],
            human_text="No workspaces found. Create one with: zot workspace new <name>",
            meta={"count": 0},
        )
        return
    click.echo(format_workspace_list(workspaces, output_json=output_json(ctx)))


def workspace_show(ctx: click.Context, name: str) -> None:
    """Show items in a workspace."""
    json_out = output_json(ctx)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)
    workspace = load_workspace_or_exit(ctx, name, context="workspace show")
    if not workspace.items:
        emit_result(
            ctx,
            [],
            human_text=f"Workspace '{name}' is empty. Use 'zot workspace add {name} KEY' to add items.",
            meta={"count": 0},
        )
        return
    reader = _open_reader(ctx)
    try:
        items = []
        missing = []
        for ws_item in workspace.items[:limit]:
            item = reader.get_item(ws_item.key)
            if item is not None:
                items.append(item)
            else:
                missing.append(ws_item.key)
        if items:
            click.echo(format_items(items, output_json=json_out, detail=detail))
        elif json_out:
            emit_result(ctx, [], human_text="", meta={"count": 0, "missing": missing})
        for key in missing:
            emit_warning(f"item '{key}' not found in Zotero library (may have been deleted)")
    finally:
        reader.close()


def _markdown_export(name: str, workspace: Workspace, items: list[Item]) -> str:
    lines = [f"# Workspace: {name}"]
    desc_part = f" {workspace.description}" if workspace.description else ""
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
    return "\n".join(lines)


def workspace_export(ctx: click.Context, name: str, fmt: str) -> None:
    """Export workspace items for external use."""
    json_out = output_json(ctx)
    workspace = load_workspace_or_exit(ctx, name, context="workspace export")
    if not workspace.items:
        emit_result(
            ctx,
            {"format": fmt, "items": 0, "content": ""},
            human_text=f"Workspace '{name}' is empty.",
            meta={"count": 0},
        )
        return
    reader = _open_reader(ctx)
    try:
        items = []
        for ws_item in workspace.items:
            item = reader.get_item(ws_item.key)
            if item is not None:
                items.append(item)
        if not items:
            emit_result(
                ctx,
                {"format": fmt, "items": 0, "content": ""},
                human_text="No items could be resolved from Zotero library.",
                meta={"count": 0},
            )
            return
        if fmt == "json":
            payload = [asdict(item) for item in items]
            if json_out:
                emit_result(ctx, {"format": "json", "items": payload}, human_text="", meta={"count": len(items)})
            else:
                click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        elif fmt == "bibtex":
            entries = []
            for item in items:
                bib = reader.export_citation(item.key, fmt="bibtex")
                if bib:
                    entries.append(bib)
            content = "\n\n".join(entries)
            if json_out:
                emit_result(
                    ctx, {"format": "bibtex", "content": content}, human_text=content, meta={"count": len(entries)}
                )
            else:
                click.echo(content)
        else:
            content = _markdown_export(name, workspace, items)
            if json_out:
                emit_result(
                    ctx,
                    {"format": "markdown", "name": name, "content": content},
                    human_text=content,
                    meta={"count": len(items)},
                )
            else:
                click.echo(content)
    finally:
        reader.close()


def workspace_import_cmd(
    ctx: click.Context,
    name: str,
    collection: str | None,
    tag: str | None,
    search_query: str | None,
    dry_run: bool,
) -> None:
    """Bulk import items into a workspace from collection, tag, or search."""
    json_out = output_json(ctx)
    workspace = load_workspace_or_exit(ctx, name, context="workspace import", hint=_ADD_HINT)
    if not collection and not tag and not search_query:
        emit_error(
            "validation_error",
            "Must specify at least one of --collection, --tag, or --search",
            output_json=json_out,
            hint="Example: zot workspace import my-ws --search 'attention'",
            context="workspace import",
        )
    reader = _open_reader(ctx)
    try:
        items_to_import: list[Item] = []
        sources: dict[str, int] = {}
        if collection:
            col_key = _resolve_collection_key(reader, collection)
            if col_key is None:
                emit_error(
                    "not_found",
                    f"Collection '{collection}' not found",
                    output_json=json_out,
                    hint="Use 'zot zotero collection list' to list available collections",
                    context="workspace import",
                )
            collection_items = reader.get_collection_items(col_key)
            sources["collection"] = len(collection_items)
            items_to_import.extend(collection_items)
        if tag:
            result = reader.search(tag, limit=500)
            tagged = [item for item in result.items if tag.lower() in [t.lower() for t in item.tags]]
            sources["tag"] = len(tagged)
            items_to_import.extend(tagged)
        if search_query:
            result = reader.search(search_query, limit=500)
            sources["search"] = len(result.items)
            items_to_import.extend(result.items)
        seen: set[str] = set()
        unique_items: list[Item] = []
        for item in items_to_import:
            if item.key not in seen:
                seen.add(item.key)
                unique_items.append(item)
        added: list[str] = []
        skipped = 0
        for item in unique_items:
            if workspace.add_item(item.key, item.title):
                added.append(item.key)
            else:
                skipped += 1
        data = {
            "name": name,
            "resolved": len(unique_items),
            "imported": len(added),
            "skipped": skipped,
            "sources": sources,
            "items": len(workspace.items),
        }
        if dry_run:
            emit_result(
                ctx,
                {**data, "would_add": added},
                human_text=f"[dry-run] Would import {len(added)} item(s) into workspace '{name}'"
                + (f" ({skipped} already present)" if skipped else ""),
                dry_run=True,
            )
            return
        save_workspace(workspace)
        if not unique_items:
            emit_warning(f"no items matched the requested source(s); nothing imported into '{name}'")
        human = f"Imported {len(added)} item(s) into workspace '{name}'"
        if skipped:
            human += f" ({skipped} skipped, already present)"
        emit_result(ctx, data, human_text=human, meta={"count": len(added)})
    finally:
        reader.close()


def workspace_search(ctx: click.Context, query: str, ws_name: str) -> None:
    """Search items within a workspace by title, author, or abstract."""
    json_out = output_json(ctx)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)
    workspace = load_workspace_or_exit(ctx, ws_name, context="workspace search")
    if not workspace.items:
        emit_result(ctx, [], human_text=f"Workspace '{ws_name}' is empty.", meta={"count": 0})
        return
    reader = _open_reader(ctx)
    try:
        query_lower = query.lower()
        matches = []
        for ws_item in workspace.items:
            item = reader.get_item(ws_item.key)
            if item is None:
                continue
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
            emit_result(ctx, [], human_text="No matching items found.", meta={"count": 0})
            return
        click.echo(format_items(matches[:limit], output_json=json_out, detail=detail))
    finally:
        reader.close()
