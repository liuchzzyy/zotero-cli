"""Collection CRUD commands and shared write output helpers."""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from zotero_cli.commands.collections import collection_group
from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.models import Collection, ErrorInfo
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import EXIT_RUNTIME, emit_error
from zotero_cli.output.renderers import format_collections, format_items, format_success, print_error

_COLLECTION_HINT = "Use 'zot collection list' to list available collections"


def _emit_json_or_text(data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None) -> None:
    click.echo(format_success(data, output_json=output_json, human_text=human_text, meta=meta))


def _emit_sync_success(data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None) -> None:
    if output_json:
        _emit_json_or_text(data, output_json=True, human_text=human_text, meta=meta)
        return
    click.echo(human_text)
    click.echo(SYNC_REMINDER)


def _emit_dry_run(ctx: click.Context, data: Any, *, human_text: str, meta: dict[str, Any] | None = None) -> None:
    if ctx.obj.get("json", False):
        extra = {"dry_run": True}
        click.echo(json.dumps(envelope_ok(data, meta=meta, extra=extra), indent=2, ensure_ascii=False))
    else:
        click.echo(human_text)


def _flatten_collection_keys(collections: list[Collection]) -> set[str]:
    keys: set[str] = set()
    for collection in collections:
        keys.add(collection.key)
        keys.update(_flatten_collection_keys(collection.children))
    return keys


def _known_collection_keys(ctx: click.Context) -> set[str]:
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        return _flatten_collection_keys(reader.get_collections())
    finally:
        reader.close()


def _validate_collection_key(ctx: click.Context, key: str, *, context: str) -> None:
    if key not in _known_collection_keys(ctx):
        emit_error(
            "not_found",
            f"Collection '{key}' not found",
            output_json=ctx.obj.get("json", False),
            hint=_COLLECTION_HINT,
            context=context,
        )


@collection_group.command("list")
@click.pass_context
def collection_list(ctx: click.Context) -> None:
    """List all collections."""
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        collections = reader.get_collections()
        click.echo(format_collections(collections, output_json=ctx.obj.get("json", False)))
    finally:
        reader.close()


@collection_group.command("items")
@click.argument("key")
@click.pass_context
def collection_items(ctx: click.Context, key: str) -> None:
    """List items in a collection."""
    _validate_collection_key(ctx, key, context="collection items")
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        items = reader.get_collection_items(key)
        click.echo(format_items(items, output_json=ctx.obj.get("json", False)))
    finally:
        reader.close()


@collection_group.command("create")
@click.argument("name")
@click.option("--parent", default=None, help="Parent collection key")
@click.option("--dry-run", is_flag=True, help="Preview the collection without creating it")
@click.pass_context
def collection_create(ctx: click.Context, name: str, parent: str | None, dry_run: bool) -> None:
    """Create a new collection."""
    if dry_run:
        _emit_dry_run(
            ctx,
            {"name": name, "parent_key": parent, "would_create": True},
            human_text=f"[dry-run] Would create collection '{name}'" + (f" under parent {parent}" if parent else ""),
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="collection",
        )
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        key = writer.create_collection(name, parent_key=parent)
        _emit_sync_success(
            {
                "key": key,
                "name": name,
                "parent_key": parent,
                "sync_required": True,
            },
            output_json=json_out,
            human_text=f"Collection created: {key}",
        )
    except ZoteroWriteError as e:
        print_error(
            ErrorInfo(message=str(e), context="collection create", hint="Check API credentials and network"),
            output_json=json_out,
        )
        ctx.exit(EXIT_RUNTIME)


@collection_group.command("move")
@click.argument("item_key")
@click.argument("collection_key")
@click.option(
    "--from",
    "source_collection",
    default=None,
    help="Source collection key to remove. Without this, the item is only added to the target collection.",
)
@click.option("--dry-run", is_flag=True, help="Preview the move without changing any collection")
@click.pass_context
def collection_move(
    ctx: click.Context, item_key: str, collection_key: str, source_collection: str | None, dry_run: bool
) -> None:
    """Add an item to a collection, or move it from a source collection."""
    if dry_run:
        action = "moved" if source_collection else "added"
        human = (
            f"[dry-run] Would move item {item_key} from collection {source_collection} to {collection_key}"
            if source_collection
            else f"[dry-run] Would add item {item_key} to collection {collection_key}"
        )
        _emit_dry_run(
            ctx,
            {
                "item_key": item_key,
                "collection_key": collection_key,
                "source_collection_key": source_collection,
                "action": action,
                "would_change": True,
            },
            human_text=human,
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="collection",
        )
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.move_to_collection(item_key, collection_key, source_collection_key=source_collection)
        action = "moved" if source_collection else "added"
        if source_collection:
            human_text = f"Item {item_key} moved from collection {source_collection} to {collection_key}"
        else:
            human_text = f"Item {item_key} added to collection {collection_key}"
        _emit_sync_success(
            {
                "item_key": item_key,
                "collection_key": collection_key,
                "source_collection_key": source_collection,
                "action": action,
                "sync_required": True,
            },
            output_json=json_out,
            human_text=human_text,
        )
    except ZoteroWriteError as e:
        print_error(
            ErrorInfo(message=str(e), context="collection move", hint="Check item and collection keys"),
            output_json=json_out,
        )
        ctx.exit(EXIT_RUNTIME)


@collection_group.command("delete")
@click.argument("key")
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option("--dry-run", is_flag=True, help="Validate and show what would be deleted without deleting")
@click.pass_context
def collection_delete(ctx: click.Context, key: str, yes: bool, dry_run: bool) -> None:
    """Delete a collection."""
    json_out = ctx.obj.get("json", False)
    if dry_run:
        _validate_collection_key(ctx, key, context="collection delete")
        _emit_dry_run(ctx, {"key": key, "would_delete": True}, human_text=f"[dry-run] Would delete collection '{key}'")
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    no_interaction = bool(ctx.obj.get("no_interaction", False))
    if not yes:
        if no_interaction or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Refusing to delete collection '{key}' without confirmation on non-interactive stdin",
                output_json=json_out,
                hint="Pass --yes to confirm or use --dry-run to preview",
                context="collection delete",
            )
        if not click.confirm(f"Delete collection '{key}'?"):
            _emit_json_or_text({"key": key, "cancelled": True}, output_json=json_out, human_text="Cancelled.")
            return
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="collection",
        )
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.delete_collection(key)
        _emit_sync_success(
            {
                "key": key,
                "deleted": True,
                "sync_required": True,
            },
            output_json=json_out,
            human_text=f"Collection {key} deleted",
        )
    except ZoteroWriteError as e:
        print_error(
            ErrorInfo(message=str(e), context="collection delete", hint="Check collection key"),
            output_json=json_out,
        )
        ctx.exit(EXIT_RUNTIME)


@collection_group.command("rename")
@click.argument("key")
@click.argument("new_name")
@click.option("--dry-run", is_flag=True, help="Preview the rename without renaming the collection")
@click.pass_context
def collection_rename(ctx: click.Context, key: str, new_name: str, dry_run: bool) -> None:
    """Rename a collection."""
    if dry_run:
        _emit_dry_run(
            ctx,
            {"key": key, "new_name": new_name, "would_rename": True},
            human_text=f"[dry-run] Would rename collection {key} to '{new_name}'",
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="collection",
        )
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.rename_collection(key, new_name)
        _emit_sync_success(
            {
                "key": key,
                "new_name": new_name,
                "sync_required": True,
            },
            output_json=json_out,
            human_text=f"Collection {key} renamed to '{new_name}'",
        )
    except ZoteroWriteError as e:
        print_error(
            ErrorInfo(message=str(e), context="collection rename", hint="Check collection key"),
            output_json=json_out,
        )
        ctx.exit(EXIT_RUNTIME)
