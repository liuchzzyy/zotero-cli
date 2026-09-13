from __future__ import annotations

import json
from typing import Any

import click

from zotero_cli_agent.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli_agent.core.reader import ZoteroReader
from zotero_cli_agent.core.writer import SYNC_REMINDER, ZoteroWriteError, ZoteroWriter
from zotero_cli_agent.exit_codes import EXIT_RUNTIME, emit_error
from zotero_cli_agent.formatter import envelope_ok, format_collections, format_items, format_success, print_error
from zotero_cli_agent.models import ErrorInfo


@click.group("collection")
def collection_group() -> None:
    """Manage Zotero collections."""
    pass


def _emit_json_or_text(
    data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None
) -> None:
    click.echo(format_success(data, output_json=output_json, human_text=human_text, meta=meta))


def _emit_sync_success(data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None) -> None:
    if output_json:
        _emit_json_or_text(data, output_json=True, human_text=human_text, meta=meta)
        return
    click.echo(human_text)
    click.echo(SYNC_REMINDER)


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
@click.pass_context
def collection_create(ctx: click.Context, name: str, parent: str | None) -> None:
    """Create a new collection."""
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
@click.pass_context
def collection_move(ctx: click.Context, item_key: str, collection_key: str, source_collection: str | None) -> None:
    """Add an item to a collection, or move it from a source collection."""
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
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without executing")
@click.pass_context
def collection_delete(ctx: click.Context, key: str, dry_run: bool) -> None:
    """Delete a collection."""
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        if json_out:
            data = {"key": key, "would_delete": True}
            click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
        else:
            click.echo(f"[dry-run] Would delete collection '{key}'")
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


@collection_group.command("reorganize")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview the plan without executing")
@click.pass_context
def collection_reorganize(ctx: click.Context, plan_file: str, dry_run: bool) -> None:
    """Batch create collections and move items based on a JSON plan file.

    The plan file should be a JSON file with this structure:

    {"collections": [{"name": "Topic A", "items": ["KEY1", "KEY2"]}, ...]}

    Optional "parent" field creates subcollections.
    """
    from pathlib import Path

    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    plan_path = Path(plan_file)
    plan = json.loads(plan_path.read_text())
    collections = plan.get("collections", [])
    if not collections:
        _emit_json_or_text(
            {"collections": [], "message": "No collections in plan."},
            output_json=json_out,
            human_text="No collections in plan.",
            meta={"count": 0},
        )
        return

    if dry_run:
        preview_collections: list[dict[str, Any]] = []
        for coll in collections:
            name = coll["name"]
            parent_name = coll.get("parent")
            items = coll.get("items", [])
            preview_collections.append(
                {
                    "name": name,
                    "parent_name": parent_name,
                    "items": items,
                    "item_count": len(items),
                }
            )
            if json_out:
                continue
            parent_str = f" (under '{parent_name}')" if parent_name else ""
            click.echo(f"[dry-run] Would create collection '{name}'{parent_str}")
            for item_key in items:
                click.echo(f"[dry-run]   Would move {item_key} -> '{name}'")
        if json_out:
            data = {
                "would_create_collections": preview_collections,
                "total_collections": len(preview_collections),
                "total_items": sum(len(coll["items"]) for coll in preview_collections),
            }
            click.echo(
                json.dumps(
                    envelope_ok(data, meta={"count": len(preview_collections)}, extra={"dry_run": True}),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(f"\n[dry-run] Total: {len(collections)} collections to create")
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
            context="collection reorganize",
        )

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    created_collections: dict[str, str] = {}  # name -> key mapping for parent lookups
    created_results: list[dict[str, Any]] = []
    moved_results: list[dict[str, Any]] = []
    failed_results: list[dict[str, Any]] = []

    for coll in collections:
        name = coll["name"]
        parent_name = coll.get("parent")
        parent_key = created_collections.get(parent_name) if parent_name else None
        items = coll.get("items", [])

        try:
            col_key = writer.create_collection(name, parent_key=parent_key)
            created_collections[name] = col_key
            created_results.append(
                {
                    "name": name,
                    "key": col_key,
                    "parent_name": parent_name,
                    "parent_key": parent_key,
                }
            )
            if not json_out:
                click.echo(f"Created collection '{name}' ({col_key})")

            for item_key in items:
                try:
                    writer.move_to_collection(item_key, col_key)
                    moved_results.append(
                        {
                            "item_key": item_key,
                            "collection_name": name,
                            "collection_key": col_key,
                        }
                    )
                    if not json_out:
                        click.echo(f"  Moved {item_key} -> '{name}'")
                except ZoteroWriteError as e:
                    failed_results.append(
                        {
                            "operation": "move_item",
                            "item_key": item_key,
                            "collection_name": name,
                            "collection_key": col_key,
                            "message": str(e),
                        }
                    )
                    if not json_out:
                        click.echo(f"  Failed to move {item_key}: {e}")
        except ZoteroWriteError as e:
            failed_results.append(
                {
                    "operation": "create_collection",
                    "name": name,
                    "parent_name": parent_name,
                    "parent_key": parent_key,
                    "message": str(e),
                }
            )
            if not json_out:
                click.echo(f"Failed to create collection '{name}': {e}")

    _emit_sync_success(
        {
            "created_collections": created_results,
            "items_moved": moved_results,
            "failed": failed_results,
            "summary": {
                "collections_requested": len(collections),
                "collections_created": len(created_collections),
                "items_moved": len(moved_results),
                "failures": len(failed_results),
            },
            "sync_required": True,
        },
        output_json=json_out,
        human_text=f"\nDone. Created {len(created_collections)} collections.",
        meta={
            "collections_requested": len(collections),
            "collections_created": len(created_collections),
            "items_moved": len(moved_results),
            "failures": len(failed_results),
        },
    )


@collection_group.command("rename")
@click.argument("key")
@click.argument("new_name")
@click.pass_context
def collection_rename(ctx: click.Context, key: str, new_name: str) -> None:
    """Rename a collection."""
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
