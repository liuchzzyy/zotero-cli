"""zot attach: upload a local file as an attachment on an existing item."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click

from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error


@click.command("attach")
@click.argument("key")
@click.option("--file", "file_path", required=True, type=click.Path(exists=True, dir_okay=False), help="File to upload")
@click.option("--dry-run", is_flag=True, help="Preview the upload without calling the API")
@click.option("--idempotency-key", default=None, help="Key so retries are safe; same key returns the original result")
@click.pass_context
def attach_cmd(
    ctx: click.Context,
    key: str,
    file_path: str,
    dry_run: bool,
    idempotency_key: str | None,
) -> None:
    """Upload a file attachment to an existing Zotero item. MUTATES LIBRARY.

    \b
    Examples:
      zot attach ABC123 --file paper.pdf
      zot attach ABC123 --file ~/Downloads/supplement.pdf
      zot attach ABC123 --file paper.pdf --dry-run
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    fp = Path(file_path)
    size = fp.stat().st_size if fp.exists() else None

    if dry_run:
        data = {"would": {"parent": key, "file": str(fp), "size_bytes": size}}
        if json_out:
            click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
        else:
            click.echo(f"[dry-run] Would upload {fp} ({size} bytes) as attachment to '{key}'")
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
            context="attach",
        )

    from zotero_cli.core.zotero.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    if idempotency_key:
        from zotero_cli.core.mineru import file_sha256

        cache_scope = request_scope(
            "attach",
            library_type,
            str(library_id),
            {"parent": key, "file": str(fp.resolve()), "sha256": file_sha256(fp)},
        )
    else:
        cache_scope = ""
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote item's attachments before retrying; do not bypass this with a new key",
                context="attach",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve the attachment upload; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="attach",
            )
        if cached is not None:
            if json_out:
                click.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                click.echo(f"Attachment uploaded: {cached.get('data', {}).get('attachment_key', '?')} (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        att_key = writer.upload_attachment(key, fp)
    except ZoteroWriteError as e:
        rejected = False
        if idempotency_key:
            rejected = failure_definitely_rejected(response=writer._zot.request, code=e.code, retryable=e.retryable)
            if rejected:
                try:
                    release_rejected_mutation(cache_scope, idempotency_key)
                except (OSError, sqlite3.Error):
                    pass
        emit_error(
            e.code,
            str(e),
            output_json=json_out,
            retryable=False if idempotency_key and not rejected else e.retryable,
            hint=(
                "The upload outcome is unknown. Reconcile the item's attachments before retrying"
                if idempotency_key and not rejected
                else "Check the item key and file path"
            ),
            context="attach",
        )

    env = envelope_ok(
        {"attachment_key": att_key, "parent_key": key, "file": str(fp), "sync_required": True},
        extra={"next": [f"zot read {key}"]},
    )
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, sqlite3.Error) as exc:
            click.echo(f"Warning: attachment uploaded but the retry result could not be cached: {exc}", err=True)
    if json_out:
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        click.echo(f"Attachment uploaded: {att_key}")
        click.echo(SYNC_REMINDER, err=True)
