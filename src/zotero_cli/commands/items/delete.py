"""zot delete: move items to the trash."""

from __future__ import annotations

import json
import sqlite3
import sys

import click

from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.zotero.idempotency import (
    PendingMutationError,
    begin_mutation,
    release_rejected_mutation,
    request_scope,
    response_definitely_rejected,
    store_cached,
)
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial
from zotero_cli.output.exit_codes import EXIT_RUNTIME, emit_error


@click.command("delete")
@click.argument("keys", nargs=-1, required=True)
@click.option("--yes", is_flag=True, help="Skip confirmation")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without executing")
@click.option("--idempotency-key", default=None, help="Key so retries are safe; same key returns the original result")
@click.pass_context
def delete_cmd(
    ctx: click.Context,
    keys: tuple[str, ...],
    yes: bool,
    dry_run: bool,
    idempotency_key: str | None,
) -> None:
    """Delete one or more items (move to trash). MUTATES LIBRARY.

    Accepts multiple keys: zot delete KEY1 KEY2 KEY3
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        data = {"would_delete": list(keys), "count": len(keys)}
        if json_out:
            click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
        else:
            for key in keys:
                click.echo(f"[dry-run] Would delete item '{key}' (move to trash)")
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
    # H-05: destructive commands require explicit --yes. --no-interaction only
    # suppresses prompts, it never authorizes a destructive write.
    if not yes:
        if ctx.obj.get("no_interaction", False) or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Refusing to delete {len(keys)} item(s) without confirmation on non-interactive stdin",
                output_json=json_out,
                hint="Pass --yes to confirm or use --dry-run to preview",
                context="delete",
            )
        label = ", ".join(keys)
        if not click.confirm(f"Delete {len(keys)} item(s): {label}?"):
            if json_out:
                click.echo(json.dumps(envelope_ok({"cancelled": True}), indent=2, ensure_ascii=False))
            else:
                click.echo("Cancelled.", err=True)
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
                "Could not durably reserve the delete; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="delete",
            )
        if cached is not None:
            if json_out:
                click.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                click.echo(f"Deleted {len(keys)} item(s) (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    succeeded: list[dict] = []
    failed: list[dict] = []
    rejected_count = 0
    unknown_outcome = False
    for key in keys:
        try:
            writer.delete_item(key)
            succeeded.append({"key": key})
            if not json_out:
                click.echo(f"Item '{key}' moved to trash.")
        except ZoteroWriteError as e:
            if idempotency_key:
                if response_definitely_rejected(writer._zot.request):
                    rejected_count += 1
                else:
                    unknown_outcome = True
            failed.append({"key": key, "error": {"code": e.code, "message": str(e), "retryable": e.retryable}})
            if not json_out:
                click.echo(f"Error: delete failed for '{key}': {e}", err=True)
    if idempotency_key and rejected_count == len(failed) and not unknown_outcome:
        try:
            release_rejected_mutation(cache_scope, idempotency_key)
        except (OSError, sqlite3.Error):
            pass
    blocked_by_reservation = bool(idempotency_key and failed and not rejected_count and unknown_outcome)
    if json_out:
        if failed and succeeded:
            env = envelope_partial(succeeded, failed, meta={"sync_required": True})
        elif failed:
            env = envelope_error(
                "api_error",
                f"{len(failed)} delete(s) failed",
                retryable=not blocked_by_reservation,
                failed=failed,
            )
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
            raise SystemExit(EXIT_RUNTIME)
        else:
            env = envelope_ok(
                {"deleted": [s["key"] for s in succeeded], "sync_required": True},
                extra={"next": ["zot trash list", "zot trash empty --yes"]},
            )
        if idempotency_key and not failed:
            try:
                store_cached(cache_scope, idempotency_key, env)
            except (OSError, sqlite3.Error) as exc:
                click.echo(f"Warning: delete result could not be cached: {exc}", err=True)
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        if not failed:
            click.echo(SYNC_REMINDER, err=True)
        if failed:
            raise SystemExit(EXIT_RUNTIME)
