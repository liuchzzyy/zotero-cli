"""zot trash: list locally-known trashed items and restore them via the Web API."""

from __future__ import annotations

import json

import click

from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial_data
from zotero_cli.output.exit_codes import emit_error, exit_code_for
from zotero_cli.output.renderers import format_items

RESTORE_SYNC_NOTE = (
    "Restored remotely. The local database only reflects the restore after Zotero Desktop syncs; "
    "verify through the Web API or wait for sync."
)


@click.group("trash", invoke_without_command=True)
@click.pass_context
def trash_group(ctx: click.Context) -> None:
    """Manage trashed items (list locally, restore through the Web API)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@trash_group.command("list")
@click.option("--limit", default=None, type=int, help="Limit results (overrides global --limit)")
@click.pass_context
def trash_list_cmd(ctx: click.Context, limit: int | None) -> None:
    """List items in the local trash snapshot.

    The local database reflects what Zotero Desktop has synced; a remote
    deletion or restore may not be visible here until Desktop syncs.

    \b
    Examples:
      zot trash list
      zot trash list --limit 10
      zot --json trash list
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        limit = limit if limit is not None else ctx.obj.get("limit", cfg.default_limit)
        items = reader.get_trash_items(limit=limit)
        if not items:
            if ctx.obj.get("json"):
                click.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                click.echo("Trash is empty.")
            return
        detail = ctx.obj.get("detail", "standard")
        click.echo(format_items(items, output_json=ctx.obj.get("json", False), detail=detail))
    finally:
        reader.close()


@trash_group.command("restore")
@click.argument("keys", nargs=-1, required=True)
@click.option("--dry-run", is_flag=True, help="Show what would be restored without executing")
@click.pass_context
def trash_restore_cmd(ctx: click.Context, keys: tuple[str, ...], dry_run: bool) -> None:
    """Restore item(s) already in Zotero's trash (raw PATCH deleted=0). MUTATES LIBRARY.

    This cannot restore items removed with 'zot delete': that command permanently
    deletes through the Web API. Restore is a remote mutation; local reads stay
    stale until Zotero Desktop syncs.

    \b
    Examples:
      zot trash restore ABC123
      zot trash restore KEY1 KEY2 KEY3
      zot trash restore ABC123 --dry-run
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        data = {"would_restore": list(keys), "count": len(keys)}
        if json_out:
            click.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            for key in keys:
                click.echo(f"[dry-run] Would restore '{key}' from the Zotero trash")
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
            context="trash restore",
        )

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    restored: list[str] = []
    failed: list[dict] = []
    for key in keys:
        try:
            writer.restore_from_trash(key)
            restored.append(key)
            if not json_out:
                click.echo(f"Restored: {key}")
        except ZoteroWriteError as e:
            failed.append(
                {
                    "key": key,
                    "code": e.code,
                    "message": str(e),
                    "retryable": e.retryable,
                    "committed": e.committed,
                }
            )
            if not json_out:
                click.echo(f"Error: restore failed for '{key}': {e}", err=True)

    if not failed:
        if json_out:
            env = envelope_ok({"restored": restored, "sync_required": True})
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            click.echo(RESTORE_SYNC_NOTE, err=True)
            click.echo(SYNC_REMINDER, err=True)
        return

    first = failed[0]
    exit_code = exit_code_for(first["code"])
    if restored:
        if json_out:
            env = envelope_partial_data({"restored": restored, "failed": failed, "sync_required": True})
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            click.echo(f"Restored {len(restored)} item(s); {len(failed)} failed.", err=True)
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
                "Restore only works for items still in Zotero's trash; 'zot delete' is permanent"
                if first["code"] == "not_found"
                else "Retry later or verify the item is in the Zotero trash"
            ),
            context="trash restore",
        )
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)
