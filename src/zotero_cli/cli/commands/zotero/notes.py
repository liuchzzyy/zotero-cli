"""读取及添加 Zotero 条目笔记。"""

from __future__ import annotations

import json

import typer

from zotero_cli.cli.commands.zotero.items import app
from zotero_cli.cli.renderers import format_notes
from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.notes import NoteCreationError, create_note


@app.command(name="note")
def note(
    ctx: typer.Context,
    key: str = typer.Argument(help="父条目的 Zotero 键。"),
    content: str | None = typer.Option(None, "--add", help="新增笔记内容；省略则读取笔记。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览笔记新增，不调用 Web API。"),
    idempotency_key: str | None = typer.Option(None, help="重复请求复用已确认的结果；未知写入需先核对。"),
) -> None:
    """读取或新增条目笔记；新增操作会写入 Zotero 库。"""
    config = load_config(profile=ctx.obj.get("profile"))
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
        write_library_id, api_key = resolve_write_credentials(config, library_type=library_type, group_id=group_id)
        if not write_library_id or not api_key:
            emit_error(
                "auth_missing",
                "Write credentials not configured",
                output_json=json_out,
                hint="Run 'zot config init' to set up API credentials",
                context="note",
            )

        try:
            env, cached = create_note(
                key,
                content,
                library_id=str(write_library_id),
                api_key=api_key,
                library_type=library_type,
                idempotency_key=idempotency_key,
                writer_factory=ZoteroWriter,
            )
        except NoteCreationError as error:
            emit_error(
                error.code, str(error), output_json=json_out, retryable=error.retryable, hint=error.hint, context="note"
            )
        if cached:
            if json_out:
                typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"Note added: {env.get('data', {}).get('note_key', '?')} (cached).")
            return
        note_key = env["data"]["note_key"]
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Note added: {note_key}")
            typer.echo(SYNC_REMINDER, err=True)
    else:
        data_dir = get_data_dir(config)
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
