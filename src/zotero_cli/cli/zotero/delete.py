"""通过 Web API 永久删除条目。"""

from __future__ import annotations

import json
import sqlite3
import sys

import typer

from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.zotero.idempotency import (
    PendingMutationError,
    begin_mutation,
    failure_definitely_rejected,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial_data
from zotero_cli.output.exit_codes import emit_error, exit_code_for

PERMANENT_DELETE_WARNING = (
    "Permanently deleted remotely. Zotero's Web API DELETE is irreversible: the item is not moved to "
    "the Zotero trash, and 'zot zotero trash restore' cannot recover it."
)


@app.command(name="delete")
def delete_cmd(
    ctx: typer.Context,
    keys: list[str] = typer.Argument(..., help="要永久删除的条目键。"),
    yes: bool = typer.Option(False, help="明确确认永久删除。"),
    dry_run: bool = typer.Option(False, help="仅预览永久删除，不发起写入。"),
    idempotency_key: str | None = typer.Option(None, help="防止结果未知时重复删除的幂等键。"),
) -> None:
    """通过 Web API 永久删除条目，不会移入回收站，也无法恢复。"""
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        data = {"would_delete": list(keys), "count": len(keys), "permanent": True}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            for key in keys:
                typer.echo(f"[dry-run] Would permanently delete item '{key}' (irreversible)")
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
            context="delete",
        )
    # 必须明确传入 --yes；非交互模式不能代替永久删除确认。
    if not yes:
        if ctx.obj.get("no_interaction", False) or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Permanently deleting {len(keys)} item(s) requires --yes on non-interactive stdin",
                output_json=json_out,
                retryable=False,
                hint="Pass --yes to confirm permanent deletion; --dry-run only previews",
                context="delete",
            )
        label = ", ".join(keys)
        if not typer.confirm(f"Permanently delete {len(keys)} item(s): {label}? This cannot be undone."):
            if json_out:
                typer.echo(json.dumps(envelope_ok({"cancelled": True, "permanent": True}), indent=2))
            else:
                typer.echo("Cancelled.", err=True)
            return

    cache_scope = request_scope("delete", library_type, str(library_id), sorted(keys))
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote items before proceeding; do not bypass this with a new key",
                context="delete",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve the permanent delete; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="delete",
            )
        if cached is not None:
            if json_out:
                typer.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"Deleted {len(keys)} item(s) permanently (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    succeeded: list[str] = []
    failed: list[dict] = []
    rejected_count = 0
    for key in keys:
        try:
            writer.delete_item(key)
            succeeded.append(key)
            if not json_out:
                typer.echo(f"Item '{key}' permanently deleted.")
        except ZoteroWriteError as e:
            rejected = False
            if idempotency_key:
                rejected = failure_definitely_rejected(
                    response=writer._zot.request,
                    code=e.code,
                    retryable=e.retryable,
                    committed=e.committed,
                )
                if rejected:
                    rejected_count += 1
            failed.append(
                {
                    "key": key,
                    "code": e.code,
                    "message": str(e),
                    "retryable": e.retryable,
                    "committed": e.committed,
                    "definitely_rejected": rejected or not e.committed,
                }
            )
            if not json_out:
                typer.echo(f"Error: permanent delete failed for '{key}': {e}", err=True)
    retry_blocked = bool(
        idempotency_key and failed and not (rejected_count == len(failed) and all(not f["committed"] for f in failed))
    )
    if idempotency_key and failed and rejected_count == len(failed) and all(not f["committed"] for f in failed):
        try:
            release_rejected_mutation(cache_scope, idempotency_key)
        except (OSError, sqlite3.Error):
            pass

    if not failed:
        env = envelope_ok(
            {"permanently_deleted": succeeded, "permanent": True, "sync_required": True},
        )
        if idempotency_key:
            try:
                store_cached(cache_scope, idempotency_key, env)
            except (OSError, sqlite3.Error) as exc:
                typer.echo(f"Warning: delete result could not be cached: {exc}", err=True)
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(PERMANENT_DELETE_WARNING, err=True)
            typer.echo(SYNC_REMINDER, err=True)
        return

    first = failed[0]
    exit_code = exit_code_for(first["code"])
    if succeeded:
        env = envelope_partial_data(
            {
                "permanently_deleted": succeeded,
                "failed": failed,
                "permanent": True,
                "sync_required": True,
            },
        )
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Permanently deleted {len(succeeded)} item(s); {len(failed)} failed.", err=True)
        raise SystemExit(exit_code)
    committed_extra = {"committed": True} if first["committed"] else {}
    env = envelope_error(
        first["code"],
        f"Permanent delete failed for {len(failed)} item(s): {first['message']}",
        retryable=False if retry_blocked else bool(first["retryable"]),
        **committed_extra,
        failed=failed,
        hint=(
            "The outcome is unknown for at least one item; reconcile through the Web API before retrying"
            if retry_blocked or any(f["committed"] for f in failed)
            else "Check the item keys and API credentials"
        ),
        context="delete",
    )
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)
