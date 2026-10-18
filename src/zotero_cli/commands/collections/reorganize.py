"""Collection reorganization plan validation and command."""

from __future__ import annotations

import json
from typing import Any

import click

from zotero_cli.commands.collections import collection_group
from zotero_cli.commands.collections.commands import _emit_dry_run, _emit_json_or_text, _emit_sync_success
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_ok, envelope_partial_data
from zotero_cli.output.exit_codes import emit_error, exit_code_for


def _validate_reorganization_plan(plan: Any) -> list[dict[str, Any]]:
    """Validate the whole plan and order parent collections before their children."""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    collections = plan.get("collections", [])
    if not isinstance(collections, list):
        raise ValueError("collections must be a list")

    parents: dict[str, str | None] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for row, coll in enumerate(collections, 1):
        if not isinstance(coll, dict):
            raise ValueError(f"collections[{row}] must be an object")
        name = coll.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"collections[{row}] requires a non-empty string name")
        if name in parents:
            raise ValueError(f"Duplicate collection name '{name}' in plan")
        parent_name = coll.get("parent")
        if parent_name is not None and (not isinstance(parent_name, str) or not parent_name.strip()):
            raise ValueError(f"collections[{row}].parent must be a non-empty collection name or null")
        parents[name] = parent_name
        by_name[name] = coll
        items = coll.get("items", [])
        if not isinstance(items, list) or any(
            not isinstance(key, str) or not key.isascii() or not key.isalnum() for key in items
        ):
            raise ValueError(f"collections[{row}].items must be a list of non-empty alphanumeric item keys")
    for name, parent_name in parents.items():
        if parent_name is not None and parent_name not in parents:
            raise ValueError(f"Unknown parent '{parent_name}' for collection '{name}'")

    ordered: list[dict[str, Any]] = []
    scheduled: set[str] = set()
    for name in parents:
        pending: list[str] = []
        visiting: set[str] = set()
        current: str | None = name
        while current is not None and current not in scheduled:
            if current in visiting:
                raise ValueError(f"Collection parent cycle involving '{current}'")
            visiting.add(current)
            pending.append(current)
            current = parents[current]
        for current in reversed(pending):
            ordered.append(by_name[current])
            scheduled.add(current)
    return ordered


@collection_group.command("reorganize")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True, help="Preview the plan without executing")
@click.pass_context
def collection_reorganize(ctx: click.Context, plan_file: str, dry_run: bool) -> None:
    """Batch create collections and move items based on a JSON plan file.

    The plan file should be a JSON file with this structure:

    {"collections": [{"name": "Topic A", "items": ["KEY1", "KEY2"]}, ...]}

    Optional "parent" field creates subcollections. This is a true move: after
    the plan runs, each listed item's collection memberships are replaced with
    the plan's target collections (other memberships are removed). Failures are
    reported with typed error codes and a non-zero exit.
    """
    from pathlib import Path

    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    plan_path = Path(plan_file)
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        emit_error(
            "validation_error",
            f"Cannot read JSON plan: {exc}",
            output_json=json_out,
            context="collection reorganize",
        )
    try:
        collections = _validate_reorganization_plan(plan)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context="collection reorganize")
    if not collections:
        if dry_run:
            _emit_dry_run(
                ctx,
                {"collections": [], "message": "No collections in plan."},
                human_text="No collections in plan.",
                meta={"count": 0},
            )
            return
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
                click.echo(f"[dry-run]   Would move {item_key} -> '{name}' (replacing other collection memberships)")
        if json_out:
            data = {
                "would_create_collections": preview_collections,
                "total_collections": len(preview_collections),
                "total_items": sum(len(coll["items"]) for coll in preview_collections),
                "would_replace_collection_memberships": True,
            }
            click.echo(
                json.dumps(
                    envelope_ok(data, meta={"count": len(preview_collections)}, dry_run=True),
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
        try:
            if parent_name is not None and parent_key is None:
                raise ZoteroWriteError(
                    f"Parent collection '{parent_name}' was not created; skipping '{name}'",
                    code="validation_error",
                    retryable=False,
                    committed=False,
                )
            col_key = writer.create_collection(name, parent_key=parent_key)
        except ZoteroWriteError as e:
            failed_results.append(
                {
                    "operation": "create_collection",
                    "name": name,
                    "parent_name": parent_name,
                    "parent_key": parent_key,
                    "code": e.code,
                    "message": str(e),
                    "committed": e.committed,
                }
            )
            if not json_out:
                click.echo(f"Failed to create collection '{name}': {e}", err=True)
            continue
        created_collections[name] = col_key
        created_results.append({"name": name, "key": col_key, "parent_name": parent_name, "parent_key": parent_key})
        if not json_out:
            click.echo(f"Created collection '{name}' ({col_key})")

    # True move: collect every planned target per item, then replace the item's
    # collection membership with that target set in one write.
    targets: dict[str, list[tuple[str, str]]] = {}
    for coll in collections:
        name = coll["name"]
        created_key = created_collections.get(name)
        if not created_key:
            continue
        for item_key in coll.get("items", []):
            targets.setdefault(item_key, []).append((name, created_key))

    for item_key in sorted(targets):
        entries = targets[item_key]
        target_keys = [col_key for _name, col_key in entries]
        try:
            writer.set_item_collections(item_key, target_keys)
        except ZoteroWriteError as e:
            failed_results.append(
                {
                    "operation": "move_item",
                    "item_key": item_key,
                    "collection_names": [name for name, _key in entries],
                    "collection_keys": target_keys,
                    "code": e.code,
                    "message": str(e),
                    "committed": e.committed,
                }
            )
            if not json_out:
                click.echo(f"Failed to move {item_key}: {e}", err=True)
            continue
        for name, col_key in entries:
            moved_results.append(
                {
                    "item_key": item_key,
                    "collection_name": name,
                    "collection_key": col_key,
                    "replaced_existing_memberships": True,
                }
            )
        if not json_out:
            names = ", ".join(f"'{name}'" for name, _key in entries)
            click.echo(f"  Moved {item_key} -> {names} (replaced other memberships)")

    summary = {
        "collections_requested": len(collections),
        "collections_created": len(created_collections),
        "items_moved": len(moved_results),
        "failures": len(failed_results),
    }
    data = {
        "created_collections": created_results,
        "items_moved": moved_results,
        "failed": failed_results,
        "summary": summary,
        "replaced_existing_memberships": True,
        "sync_required": True,
    }
    if failed_results:
        exit_code = exit_code_for(str(failed_results[0].get("code") or "api_error"))
        if json_out:
            env = envelope_partial_data(data, meta=summary)
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            click.echo(f"Incomplete: {len(failed_results)} operation(s) failed.", err=True)
        raise SystemExit(exit_code)
    _emit_sync_success(
        data,
        output_json=json_out,
        human_text=f"\nDone. Created {len(created_collections)} collections; moved {len(targets)} item(s).",
        meta=summary,
    )
