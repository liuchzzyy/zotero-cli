"""查看本地回收站快照，并通过 Web API 恢复条目。"""

from __future__ import annotations

import json

import typer

from zotero_cli.cli.app.context import resolve_limit
from zotero_cli.cli.app.group import TieredGroup
from zotero_cli.cli.commands.zotero.items import app
from zotero_cli.cli.renderers import format_items
from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial_data
from zotero_cli.output.exit_codes import emit_error, exit_code_for
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.items import restore_items

RESTORE_SYNC_NOTE = (
    "Restored remotely. The local database only reflects the restore after Zotero Desktop syncs; "
    "verify through the Web API or wait for sync."
)


trash_app = typer.Typer(
    cls=TieredGroup,
    help="查看本地回收站快照并通过 Web API 恢复条目。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(trash_app, name="trash")


@trash_app.command(name="list")
def trash_list_cmd(ctx: typer.Context, limit: int | None = typer.Option(None, help="覆盖全局结果上限。")) -> None:
    """列出已同步到本地数据库的回收站条目。"""
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        limit = resolve_limit(limit, ctx, config)
        items = reader.get_trash_items(limit=limit)
        if not items:
            if ctx.obj.get("json"):
                typer.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                typer.echo("Trash is empty.")
            return
        detail = ctx.obj.get("detail", "standard")
        typer.echo(format_items(items, output_json=ctx.obj.get("json", False), detail=detail))
    finally:
        reader.close()


@trash_app.command(name="restore")
def trash_restore_cmd(
    ctx: typer.Context,
    keys: list[str] = typer.Argument(..., help="要从回收站恢复的条目键。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览，不发起写入。"),
) -> None:
    """通过 Web API 恢复回收站条目；永久删除的条目无法恢复。"""
    config = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        data = {"would_restore": list(keys), "count": len(keys)}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            for key in keys:
                typer.echo(f"[dry-run] Would restore '{key}' from the Zotero trash")
        return
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(config, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="trash restore",
        )

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)

    def report(row: dict[str, object], is_error: bool) -> None:
        if json_out:
            return
        if is_error:
            typer.echo(f"Error: restore failed for '{row['key']}': {row['message']}", err=True)
        else:
            typer.echo(f"Restored: {row['key']}")

    result = restore_items(writer, keys, report=report)
    restored = result.restored
    failed = result.failed

    if not failed:
        if json_out:
            env = envelope_ok({"restored": restored, "sync_required": True})
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(RESTORE_SYNC_NOTE, err=True)
            typer.echo(SYNC_REMINDER, err=True)
        return

    first = failed[0]
    exit_code = exit_code_for(first["code"])
    if restored:
        if json_out:
            env = envelope_partial_data({"restored": restored, "failed": failed, "sync_required": True})
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Restored {len(restored)} item(s); {len(failed)} failed.", err=True)
        raise SystemExit(exit_code)
    if json_out:
        committed_extra = {"committed": True} if first["committed"] else {}
        env = envelope_error(
            first["code"],
            f"Restore failed for {len(failed)} item(s): {first['message']}",
            retryable=bool(first["retryable"]) and not bool(first["committed"]),
            **committed_extra,
            failed=failed,
            hint=(
                "Restore only works for items still in Zotero's trash; 'zot zotero delete' is permanent"
                if first["code"] == "not_found"
                else "Retry later or verify the item is in the Zotero trash"
            ),
            context="trash restore",
        )
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)
