"""基础 PDF 附件列表与本地路径命令。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer

from zotero_cli.cli.app.group import TieredGroup
from zotero_cli.cli.commands.zotero.items import app as zotero_app
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.sanitize import sanitize_text
from zotero_cli.zotero.access.idempotency import PendingMutationError
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.pdf import (
    AttachmentUploadFailure,
    attachment_upload_scope,
    cache_attachment_upload,
    export_pdf,
    select_pdf_attachment,
    upload_attachment_protected,
)

app = typer.Typer(
    cls=TieredGroup,
    help="列出并定位本地 PDF 附件。",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
zotero_app.add_typer(app, name="pdf")


def _open_pdf_reader(ctx: typer.Context) -> ZoteroReader:
    config = load_config(profile=ctx.obj.get("profile"))
    database_path = get_data_dir(config) / "zotero.sqlite"
    library_id = resolve_library_id(database_path, ctx.obj)
    return ZoteroReader(database_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))


@app.command(name="list")
def list_pdfs(ctx: typer.Context, key: str = typer.Argument(help="Zotero 父条目键。")) -> None:
    """列出条目下的 PDF 附件，不调用 MinerU。"""
    reader = _open_pdf_reader(ctx)
    try:
        if reader.get_item(key) is None:
            emit_error(
                "not_found", f"Item '{key}' not found", output_json=ctx.obj.get("json", False), context="pdf list"
            )
        attachments = reader.get_pdf_attachments(key)
        data = {
            "key": key,
            "attachments": [
                {
                    "key": attachment.key,
                    "filename": attachment.filename,
                    "available": bool(attachment.path and attachment.path.is_file()),
                }
                for attachment in attachments
            ],
        }
        if ctx.obj.get("json", False):
            typer.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
        elif attachments:
            for attachment in attachments:
                typer.echo(f"{sanitize_text(attachment.key)}  {sanitize_text(attachment.filename)}")
        else:
            typer.echo(f"No PDF attachment found for '{sanitize_text(key)}'.")
    finally:
        reader.close()


@app.command(name="path")
def pdf_path(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键。"),
    attachment_key: str | None = typer.Option(None, "--attachment", help="指定 PDF 附件键。"),
) -> None:
    """返回可用 PDF 的本地路径，不解析或复制文件。"""
    reader = _open_pdf_reader(ctx)
    try:
        if reader.get_item(key) is None:
            emit_error(
                "not_found", f"Item '{key}' not found", output_json=ctx.obj.get("json", False), context="pdf path"
            )
        attachments = reader.get_pdf_attachments(key)
        try:
            attachment = select_pdf_attachment(attachments, attachment_key)
        except LookupError as error:
            emit_error("not_found", str(error), output_json=ctx.obj.get("json", False), context="pdf path")
        except ValueError as error:
            emit_error(
                "validation_error",
                str(error),
                output_json=ctx.obj.get("json", False),
                hint=f"Run 'zot zotero pdf list {key}' to select an attachment",
                context="pdf path",
            )
        if attachment.path is None or not attachment.path.is_file():
            emit_error(
                "not_found",
                f"PDF file not found for attachment '{attachment.key}'",
                output_json=ctx.obj.get("json", False),
                context="pdf path",
            )
        if ctx.obj.get("json", False):
            data = {"key": key, "attachment_key": attachment.key, "path": str(attachment.path)}
            typer.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
        else:
            typer.echo(sanitize_text(attachment.path))
    finally:
        reader.close()


def _selected_pdf_path(ctx: typer.Context, key: str, attachment_key: str | None, context: str) -> tuple[str, Path]:
    reader = _open_pdf_reader(ctx)
    try:
        if reader.get_item(key) is None:
            emit_error("not_found", f"Item '{key}' not found", output_json=ctx.obj.get("json", False), context=context)
        try:
            attachment = select_pdf_attachment(reader.get_pdf_attachments(key), attachment_key)
        except LookupError as error:
            emit_error("not_found", str(error), output_json=ctx.obj.get("json", False), context=context)
        except ValueError as error:
            emit_error(
                "validation_error",
                str(error),
                output_json=ctx.obj.get("json", False),
                hint=f"Run 'zot zotero pdf list {key}' to select an attachment",
                context=context,
            )
        if attachment.path is None or not attachment.path.is_file():
            emit_error(
                "not_found",
                f"PDF file not found for attachment '{attachment.key}'",
                output_json=ctx.obj.get("json", False),
                context=context,
            )
        return attachment.key, attachment.path
    finally:
        reader.close()


@app.command(name="export")
def export(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键。"),
    destination: Path = typer.Argument(help="导出的新 PDF 路径。"),
    attachment_key: str | None = typer.Option(None, "--attachment", help="指定 PDF 附件键。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不创建文件。"),
) -> None:
    """导出一个本地 PDF 附件；目标路径不能已经存在。"""
    json_out = ctx.obj.get("json", False)
    selected_key, source_path = _selected_pdf_path(ctx, key, attachment_key, "pdf export")
    if destination.exists() or destination.is_symlink():
        emit_error("conflict", f"Destination already exists: {destination}", output_json=json_out, context="pdf export")
    if not destination.parent.is_dir():
        emit_error(
            "validation_error",
            f"Destination directory does not exist: {destination.parent}",
            output_json=json_out,
            context="pdf export",
        )
    data = {"key": key, "attachment_key": selected_key, "source": str(source_path), "destination": str(destination)}
    if dry_run:
        if json_out:
            typer.echo(json.dumps(envelope_ok({"would": data}, dry_run=True), ensure_ascii=False, indent=2))
        else:
            typer.echo(f"[dry-run] Would export {source_path} to {destination}")
        return
    try:
        export_pdf(source_path, destination)
    except FileExistsError:
        emit_error("conflict", f"Destination already exists: {destination}", output_json=json_out, context="pdf export")
    except OSError as error:
        emit_error("runtime_error", f"Could not export PDF: {error}", output_json=json_out, context="pdf export")
    if json_out:
        typer.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"Exported PDF: {destination}")


@app.command(name="upload")
def upload(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键。"),
    file_path: Path = typer.Option(..., "--file", exists=True, dir_okay=False, help="上传的本地 PDF。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不调用 Web API。"),
    idempotency_key: str | None = typer.Option(None, help="重复请求复用已确认的结果；未知写入需先核对。"),
) -> None:
    """将本地 PDF 通过 Zotero Web API 上传到父条目。"""
    json_out = ctx.obj.get("json", False)
    file_path = Path(file_path)
    try:
        with file_path.open("rb") as handle:
            is_pdf = handle.read(5) == b"%PDF-"
    except OSError:
        is_pdf = False
    if file_path.suffix.lower() != ".pdf" or not is_pdf:
        emit_error("validation_error", f"File is not a PDF: {file_path}", output_json=json_out, context="pdf upload")
    size = file_path.stat().st_size
    if dry_run:
        data = {"would": {"parent": key, "file": str(file_path), "size_bytes": size}}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), ensure_ascii=False, indent=2))
        else:
            typer.echo(f"[dry-run] Would upload {file_path} ({size} bytes) as PDF attachment to '{key}'")
        return
    config = load_config(profile=ctx.obj.get("profile"))
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(config, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error("auth_missing", "Write credentials not configured", output_json=json_out, context="pdf upload")
    cache_scope = (
        attachment_upload_scope(
            command="pdf-upload",
            library_type=library_type,
            library_id=str(library_id),
            parent_key=key,
            file_path=file_path,
        )
        if idempotency_key
        else ""
    )
    try:
        result = upload_attachment_protected(
            writer_factory=lambda: ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type),
            parent_key=key,
            file_path=file_path,
            idempotency_key=idempotency_key,
            cache_scope=cache_scope,
        )
    except PendingMutationError as error:
        emit_error("conflict", str(error), output_json=json_out, retryable=False, context="pdf upload")
    except (OSError, sqlite3.Error):
        emit_error(
            "configuration_error",
            "Could not durably reserve the PDF upload; no write attempted",
            output_json=json_out,
            retryable=False,
            context="pdf upload",
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
                else "Check the item key and PDF file"
            ),
            context="pdf upload",
        )
    if result.cached_envelope is not None:
        if json_out:
            typer.echo(json.dumps(result.cached_envelope, ensure_ascii=False, indent=2))
        else:
            typer.echo(f"PDF uploaded: {result.cached_envelope.get('data', {}).get('attachment_key', '?')} (cached).")
        return
    env = envelope_ok(
        {"attachment_key": result.attachment_key, "parent_key": key, "file": str(file_path), "sync_required": True},
        next=[f"zot zotero read {key}"],
    )
    cache_error = cache_attachment_upload(cache_scope=cache_scope, idempotency_key=idempotency_key, envelope=env)
    if cache_error is not None:
        typer.echo(f"Warning: PDF uploaded but the retry result could not be cached: {cache_error}", err=True)
    if json_out:
        typer.echo(json.dumps(env, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"PDF uploaded: {result.attachment_key}")
        typer.echo(SYNC_REMINDER, err=True)
