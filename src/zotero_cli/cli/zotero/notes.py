"""读取及添加 Zotero 条目笔记。"""

from __future__ import annotations

import json
import sqlite3

import typer

from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_notes
from zotero_cli.zotero.reader import ZoteroReader
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError


@app.command(name="note")
def note(
    ctx: typer.Context,
    key: str = typer.Argument(help="父条目的 Zotero 键。"),
    content: str | None = typer.Option(None, "--add", help="新增笔记内容；省略则读取笔记。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览笔记新增，不调用 Web API。"),
    idempotency_key: str | None = typer.Option(None, help="重复请求复用已确认的结果；未知写入需先核对。"),
) -> None:
    """读取或新增条目笔记；新增操作会写入 Zotero 库。"""
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
                typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
            else:
                typer.echo(f"[dry-run] Would add note to '{key}': {content[:80]}...")
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

        from zotero_cli.zotero.idempotency import (
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
                    typer.echo(json.dumps(cached, indent=2, ensure_ascii=False))
                else:
                    typer.echo(f"Note added: {cached.get('data', {}).get('note_key', '?')} (cached).")
                return

        writer = None
        try:
            writer = ZoteroWriter(library_id=str(write_library_id), api_key=api_key, library_type=library_type)
            note_key = writer.add_note(key, content)
        except Exception as exc:
            # pyzotero 解析明确的 HTTP 拒绝时也可能失败；其他解析或本地错误
            # 不能证明远端未提交，因此不暴露任意异常消息。
            write_error = (
                exc
                if isinstance(exc, ZoteroWriteError)
                else ZoteroWriteError("Note creation did not return a usable result")
            )
            # 优先检查客户端保留的响应；无响应时以写入异常的提交状态作依据。
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
                    rejected = False  # 持久化解除失败时保留写入预约。
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
            next=[f"zot zotero note {key}"],
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
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Note added: {note_key}")
            typer.echo(SYNC_REMINDER, err=True)
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
                        hint="Check the item key (zot zotero search / zot zotero recent)",
                        context="note",
                    )
                emit_error(
                    "not_found",
                    f"No notes found for '{key}'",
                    output_json=json_out,
                    hint="Add one with: zot zotero note KEY --add 'content'",
                    context="note",
                )
            typer.echo(format_notes(notes, output_json=json_out))
        finally:
            reader.close()
