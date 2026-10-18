"""zot note: view or add notes on an item."""

from __future__ import annotations

import json
import sqlite3

import click

from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_notes


@click.command("note")
@click.argument("key")
@click.option("--add", "content", default=None, help="Add a new note")
@click.option("--dry-run", is_flag=True, help="Preview the note addition without executing (only with --add)")
@click.option(
    "--idempotency-key",
    default=None,
    help="Replay confirmed success; in-flight or unknown outcomes require reconciliation",
)
@click.pass_context
def note_cmd(
    ctx: click.Context,
    key: str,
    content: str | None,
    dry_run: bool,
    idempotency_key: str | None,
) -> None:
    """View or add notes for an item. `--add` MUTATES LIBRARY.

    \b
    Examples:
      zot note ABC123                            View notes
      zot note ABC123 --add "Key finding: ..."   Add a note
      zot note ABC123 --add "..." --dry-run      Preview addition
      zot --json note ABC123                     JSON output
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    if content is not None:
        if not content.strip():
            emit_error(
                "validation_error",
                "Note content must not be empty",
                output_json=json_out,
                hint="Pass a non-empty string after --add",
                context="note",
            )
        if dry_run:
            data = {"would": {"parent": key, "content_preview": content[:200]}}
            if json_out:
                click.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
            else:
                click.echo(f"[dry-run] Would add note to '{key}': {content[:80]}...")
            return

        library_type = ctx.obj.get("library_type", "user")
        group_id = ctx.obj.get("group_id")
        write_library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
        if not write_library_id or not api_key:
            emit_error(
                "auth_missing",
                "Write credentials not configured",
                output_json=json_out,
                hint="Run 'zot config init' to set up API credentials",
                context="note",
            )

        from zotero_cli.core.zotero.idempotency import (
            PendingMutationError,
            begin_mutation,
            failure_definitely_rejected,
            release_rejected_mutation,
            request_scope,
            store_cached,
        )

        cache_scope = request_scope("note", library_type, str(write_library_id), {"parent": key, "content": content})
        if idempotency_key:
            try:
                cached = begin_mutation(cache_scope, idempotency_key)
            except PendingMutationError as e:
                emit_error(
                    "conflict",
                    str(e),
                    output_json=json_out,
                    retryable=False,
                    hint="Reconcile the remote parent's notes before proceeding; do not bypass this with a new key",
                    context="note",
                )
            except (OSError, sqlite3.Error):
                emit_error(
                    "configuration_error",
                    "Could not durably reserve note creation; no write attempted",
                    output_json=json_out,
                    retryable=False,
                    hint="Check the idempotency state directory and database",
                    context="note",
                )
            if cached is not None:
                if json_out:
                    click.echo(json.dumps(cached, indent=2, ensure_ascii=False))
                else:
                    click.echo(f"Note added: {cached.get('data', {}).get('note_key', '?')} (cached).")
                return

        writer = None
        try:
            writer = ZoteroWriter(library_id=str(write_library_id), api_key=api_key, library_type=library_type)
            note_key = writer.add_note(key, content)
        except Exception as exc:
            # pyzotero may fail while parsing even an explicit HTTP rejection.
            # Other parse/local errors after HTTP are unknown, not proof that
            # nothing committed. Avoid exposing arbitrary exception messages.
            write_error = (
                exc
                if isinstance(exc, ZoteroWriteError)
                else ZoteroWriteError("Note creation did not return a usable result")
            )
            # pyzotero 1.16/httpx2 keeps the last response on the client; the
            # writer's typed error carries committed/code evidence as a fallback.
            response = getattr(getattr(writer, "_zot", None), "request", None) if writer is not None else None
            rejected = failure_definitely_rejected(
                response=response,
                code=write_error.code,
                retryable=write_error.retryable,
                committed=write_error.committed,
            )
            if rejected and idempotency_key:
                try:
                    release_rejected_mutation(cache_scope, idempotency_key)
                except (OSError, sqlite3.Error):
                    rejected = False  # Preserve the reservation if its durable release failed.
            outcome_unknown = not rejected
            emit_error(
                write_error.code,
                str(write_error),
                output_json=json_out,
                retryable=False if outcome_unknown else write_error.retryable,
                hint=(
                    "The write outcome is unknown. Reconcile the remote parent's notes; do not retry with a new key"
                    if outcome_unknown
                    else "Check item key and API credentials"
                ),
                context="note",
            )

        env = envelope_ok(
            {"note_key": note_key, "parent_key": key, "sync_required": True},
            next=[f"zot note {key}"],
        )
        if idempotency_key:
            try:
                store_cached(cache_scope, idempotency_key, env)
            except (OSError, sqlite3.Error):
                emit_error(
                    "configuration_error",
                    f"Note '{note_key}' was created but its retry result could not be saved",
                    output_json=json_out,
                    retryable=False,
                    hint="Reconcile this note remotely; the pending reservation still blocks blind retries",
                    context="note",
                )
        if json_out:
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            click.echo(f"Note added: {note_key}")
            click.echo(SYNC_REMINDER, err=True)
    else:
        data_dir = get_data_dir(cfg)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(db_path, ctx.obj)
        reader = ZoteroReader(db_path, library_id=library_id)
        try:
            notes = reader.get_notes(key)
            if not notes:
                if reader.get_item(key) is None:
                    emit_error(
                        "not_found",
                        f"Item '{key}' not found",
                        output_json=json_out,
                        hint="Check the item key (zot search / zot recent)",
                        context="note",
                    )
                emit_error(
                    "not_found",
                    f"No notes found for '{key}'",
                    output_json=json_out,
                    hint="Add one with: zot note KEY --add 'content'",
                    context="note",
                )
            click.echo(format_notes(notes, output_json=json_out))
        finally:
            reader.close()
