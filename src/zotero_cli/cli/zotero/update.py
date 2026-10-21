"""通过 Web API 更新条目元数据。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer

from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError
from zotero_cli.output.envelope import envelope_ok, envelope_partial
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.streaming import emit_progress
from zotero_cli.zotero.item_updates import _load_updates_from_jsonl, _parse_inline_fields


@app.command(name="update")
def update_cmd(
    ctx: typer.Context,
    key: str | None = typer.Argument(None, help="条目键；批量更新时不传。"),
    title: str | None = typer.Option(None, help="新标题。"),
    date: str | None = typer.Option(None, help="新日期。"),
    field: list[str] = typer.Option([], help="以 key=value 格式设置字段，可重复。"),
    from_jsonl: Path | None = typer.Option(
        None, "--from-jsonl", exists=True, dir_okay=False, help="从 JSONL 文件批量更新。"
    ),
    add_tags: list[str] = typer.Option([], "--add-tag", help="成功更新后添加标签，可重复。"),
    dry_run: bool = typer.Option(False, help="只预览，不执行更新。"),
    idempotency_key: str | None = typer.Option(None, help="单条写入的幂等键。"),
) -> None:
    """通过 Zotero Web API 更新条目元数据。"""
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    fields = _parse_inline_fields(title=title, date=date, field=field, json_out=json_out)
    has_inline_fields = bool(fields)

    if from_jsonl is not None:
        if key is not None:
            emit_error(
                "validation_error",
                "Do not provide ITEMKEY when using --from-jsonl",
                output_json=json_out,
                hint="Use either 'zot zotero update KEY ...' or 'zot zotero update --from-jsonl updates.jsonl'",
                context="update",
            )
        if has_inline_fields:
            emit_error(
                "validation_error",
                "Do not mix --from-jsonl with --title, --date, or --field",
                output_json=json_out,
                hint="Put all field updates inside the JSONL file",
                context="update",
            )
        if idempotency_key:
            emit_error(
                "validation_error",
                "--idempotency-key is not supported with --from-jsonl",
                output_json=json_out,
                hint="Retry batch updates by rerunning the validated JSONL file or use single-item update mode",
                context="update",
            )

        updates = _load_updates_from_jsonl(from_jsonl, json_out=json_out)
        if dry_run:
            data = {
                "would": {
                    "source": "jsonl",
                    "path": str(from_jsonl),
                    "count": len(updates),
                    "updates": updates,
                    "tags_to_add": list(add_tags),
                }
            }
            if json_out:
                typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
            else:
                typer.echo(f"[dry-run] Would update {len(updates)} item(s) from {from_jsonl}")
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
                context="update",
            )

        emit_progress("start", phase="batch_update", total=len(updates), source=str(from_jsonl))
        writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
        succeeded: list[dict[str, object]] = []
        failed: list[dict[str, object]] = []
        for index, row in enumerate(updates, 1):
            emit_progress("progress", phase="batch_update", done=index - 1, total=len(updates))
            row_key = row["key"]
            row_fields = row["fields"]
            row_line = row["line"]
            try:
                writer.update_item(row_key, row_fields)
                if add_tags:
                    writer.add_tags(row_key, list(add_tags))
                succeeded.append({"line": row_line, "key": row_key, "fields": row_fields, "tags_added": list(add_tags)})
                if not json_out:
                    typer.echo(f"  [{index}/{len(updates)}] Updated: {row_key}", err=True)
            except ZoteroWriteError as e:
                failed.append(
                    {
                        "line": row_line,
                        "key": row_key,
                        "fields": row_fields,
                        "error": {"code": e.code, "message": str(e), "retryable": e.retryable},
                    }
                )
                if not json_out:
                    typer.echo(f"  [{index}/{len(updates)}] Failed: {row_key} ({e})", err=True)

        emit_progress(
            "complete",
            phase="batch_update",
            done=len(updates),
            total=len(updates),
            succeeded=len(succeeded),
            failed=len(failed),
        )
        if json_out:
            env = envelope_partial(succeeded, failed, meta={"total": len(updates), "sync_required": bool(succeeded)})
            if not failed:
                env["ok"] = True
                env["data"] = {"succeeded": succeeded, "failed": [], "tags_added": list(add_tags)}
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"\nDone: {len(succeeded)} updated, {len(failed)} failed", err=True)
            if succeeded:
                typer.echo(SYNC_REMINDER, err=True)
        return

    if key is None:
        emit_error(
            "validation_error",
            "Missing item key",
            output_json=json_out,
            hint="Use 'zot zotero update KEY --title ...' or 'zot zotero update --from-jsonl updates.jsonl'",
            context="update",
        )

    if not fields:
        emit_error(
            "validation_error",
            "No fields to update",
            output_json=json_out,
            hint="Use --title, --date, --field key=value, or --from-jsonl",
            context="update",
        )

    if dry_run:
        data = {"would": {"key": key, "fields": fields, "field_count": len(fields), "tags_to_add": list(add_tags)}}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            typer.echo(f"[dry-run] Would update '{key}' with {len(fields)} field(s): {list(fields.keys())}")
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
            context="update",
        )

    # 写入前预留幂等键，防止响应丢失后盲目重试。
    from zotero_cli.core.zotero.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    cache_scope = request_scope(
        "update", library_type, str(library_id), {"key": key, "fields": fields, "add_tags": sorted(add_tags)}
    )
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote item before retrying; do not bypass this with a new key",
                context="update",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve the update; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="update",
            )
        if cached is not None:
            typer.echo(json.dumps(cached, indent=2, ensure_ascii=False) if json_out else f"Updated '{key}' (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.update_item(key, fields)
        if add_tags:
            writer.add_tags(key, list(add_tags))
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
                try:
                    release_rejected_mutation(cache_scope, idempotency_key)
                except (OSError, sqlite3.Error):
                    pass
        emit_error(
            e.code,
            str(e),
            output_json=json_out,
            retryable=False if (e.committed or (idempotency_key and not rejected)) else e.retryable,
            committed=e.committed,
            context="update",
            hint=(
                "The write outcome is unknown. Reconcile the item before retrying; do not reuse this key blindly"
                if e.committed or (idempotency_key and not rejected)
                else f"Failed to update '{key}'"
            ),
        )

    env = envelope_ok(
        {"key": key, "fields": fields, "tags_added": list(add_tags), "sync_required": True},
        next=[f"zot zotero read {key}"],
    )
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"Warning: item was updated but the retry result could not be cached: {exc}", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Updated {len(fields)} field(s) for '{key}'.")
        typer.echo(SYNC_REMINDER, err=True)
