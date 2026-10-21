"""上传 Zotero 条目的本地附件。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer

from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.attachment_upload import (
    AttachmentUploadFailure,
    attachment_upload_scope,
    cache_attachment_upload,
    upload_attachment_protected,
)
from zotero_cli.zotero.idempotency import PendingMutationError
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter


@app.command(name="attach")
def attach(
    ctx: typer.Context,
    key: str = typer.Argument(help="父条目的 Zotero 键。"),
    file_path: Path = typer.Option(..., "--file", exists=True, dir_okay=False, help="上传的本地文件。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不调用 Web API。"),
    idempotency_key: str | None = typer.Option(None, help="重复请求复用已确认的结果；未知写入需先核对。"),
) -> None:
    """将本地文件上传为 Zotero 条目附件；非预览模式会写入库。"""
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    fp = Path(file_path)
    size = fp.stat().st_size if fp.exists() else None

    if dry_run:
        data = {"would": {"parent": key, "file": str(fp), "size_bytes": size}}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            typer.echo(f"[dry-run] Would upload {fp} ({size} bytes) as attachment to '{key}'")
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

    if idempotency_key:
        cache_scope = attachment_upload_scope(
            command="attach",
            library_type=library_type,
            library_id=str(library_id),
            parent_key=key,
            file_path=fp,
        )
    else:
        cache_scope = ""

    try:
        result = upload_attachment_protected(
            writer_factory=lambda: ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type),
            parent_key=key,
            file_path=fp,
            idempotency_key=idempotency_key,
            cache_scope=cache_scope,
        )
    except PendingMutationError as error:
        emit_error(
            "conflict",
            str(error),
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
    except AttachmentUploadFailure as failure:
        error = failure.error
        emit_error(
            error.code,
            str(error),
            output_json=json_out,
            retryable=failure.retryable,
            committed=error.committed,
            hint=(
                "The upload outcome is unknown. Reconcile the item's attachments before retrying"
                if error.committed or (idempotency_key and not failure.retryable)
                else "Check the item key and file path"
            ),
            context="attach",
        )
    if result.cached_envelope is not None:
        if json_out:
            typer.echo(json.dumps(result.cached_envelope, indent=2, ensure_ascii=False))
        else:
            typer.echo(
                f"Attachment uploaded: {result.cached_envelope.get('data', {}).get('attachment_key', '?')} (cached)."
            )
        return

    env = envelope_ok(
        {"attachment_key": result.attachment_key, "parent_key": key, "file": str(fp), "sync_required": True},
        next=[f"zot zotero read {key}"],
    )
    cache_error = cache_attachment_upload(cache_scope=cache_scope, idempotency_key=idempotency_key, envelope=env)
    if cache_error is not None:
        typer.echo(f"Warning: attachment uploaded but the retry result could not be cached: {cache_error}", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Attachment uploaded: {result.attachment_key}")
        typer.echo(SYNC_REMINDER, err=True)
