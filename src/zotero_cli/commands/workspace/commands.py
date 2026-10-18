"""Workspace browsing, mutation, import, and search commands."""

from __future__ import annotations

from datetime import datetime, timezone

import click

from zotero_cli.commands.workspace import workspace_group
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.core.workspace_store import (
    Workspace,
    delete_workspace,
    list_workspaces,
    load_workspace,
    save_workspace,
    validate_name,
    workspace_exists,
)
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.models import Collection, Item
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_items, format_workspace_list


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
