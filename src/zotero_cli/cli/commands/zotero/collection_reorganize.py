"""集合重组命令与预览呈现。"""

from __future__ import annotations

import json
from typing import Any

import typer

from zotero_cli.cli.commands.zotero.collection import _emit_dry_run, _emit_json_or_text, _emit_sync_success, app
from zotero_cli.cli.output.envelope import envelope_ok, envelope_partial_data
from zotero_cli.cli.output.exit_codes import emit_error, exit_code_for
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.zotero.collections import execute_reorganization, validate_reorganization_plan
from zotero_cli.zotero.writer import ZoteroWriter


@app.command("reorganize")
def collection_reorganize(
    ctx: typer.Context,
    plan_file: str = typer.Argument(help="JSON 重组计划路径。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览计划，不执行写入。"),
) -> None:
    """根据 JSON 计划批量创建集合并替换条目所属集合。"""
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
        collections = validate_reorganization_plan(plan)
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
            typer.echo(f"[dry-run] Would create collection '{name}'{parent_str}")
            for item_key in items:
                typer.echo(f"[dry-run]   Would move {item_key} -> '{name}' (replacing other collection memberships)")
        if json_out:
            data = {
                "would_create_collections": preview_collections,
                "total_collections": len(preview_collections),
                "total_items": sum(len(coll["items"]) for coll in preview_collections),
                "would_replace_collection_memberships": True,
            }
            typer.echo(
                json.dumps(
                    envelope_ok(data, meta={"count": len(preview_collections)}, dry_run=True),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            typer.echo(f"\n[dry-run] Total: {len(collections)} collections to create")
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

    def report(message: str, error: bool = False) -> None:
        if not json_out:
            typer.echo(message, err=error)

    data, moved_item_count = execute_reorganization(writer, collections, report)
    summary = data["summary"]
    failed_results = data["failed"]
    if failed_results:
        exit_code = exit_code_for(str(failed_results[0].get("code") or "api_error"))
        if json_out:
            env = envelope_partial_data(data, meta=summary)
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Incomplete: {len(failed_results)} operation(s) failed.", err=True)
        raise SystemExit(exit_code)
    _emit_sync_success(
        data,
        output_json=json_out,
        human_text=f"\nDone. Created {summary['collections_created']} collections; moved {moved_item_count} item(s).",
        meta=summary,
    )
