from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import click

from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.writer import SYNC_REMINDER, ZoteroWriteError, ZoteroWriter
from zotero_cli.exit_codes import emit_error
from zotero_cli.formatter import emit_progress, envelope_ok, envelope_partial


class BatchUpdateRow(TypedDict):
    line: int
    key: str
    fields: dict[str, str]


def _parse_inline_fields(
    *,
    title: str | None,
    date: str | None,
    field: tuple[str, ...],
    json_out: bool,
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if title:
        fields["title"] = title
    if date:
        fields["date"] = date
    for f in field:
        if "=" not in f:
            emit_error(
                "validation_error",
                f"Invalid field format: '{f}'",
                output_json=json_out,
                hint="Use key=value format",
                context="update",
            )
        k, v = f.split("=", 1)
        fields[k] = v
    return fields


def _load_updates_from_jsonl(file_path: Path, *, json_out: bool) -> list[BatchUpdateRow]:
    try:
        raw_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        emit_error(
            "validation_error",
            f"Could not read JSONL file '{file_path}': {e}",
            output_json=json_out,
            hint="Provide a UTF-8 JSONL file with one update object per line",
            context="update",
        )

    updates: list[BatchUpdateRow] = []
    for line_no, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as e:
            emit_error(
                "validation_error",
                f"Invalid JSON on line {line_no} of '{file_path}': {e.msg}",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )
        if not isinstance(payload, dict):
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' must be a JSON object",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )

        key = payload.get("key")
        fields = payload.get("fields")
        if not isinstance(key, str) or not key.strip():
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' is missing a non-empty string 'key'",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )
        if not isinstance(fields, dict) or not fields:
            emit_error(
                "validation_error",
                f"Line {line_no} of '{file_path}' must contain a non-empty object 'fields'",
                output_json=json_out,
                hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
                context="update",
            )

        clean_fields: dict[str, str] = {}
        for field_name, field_value in fields.items():
            if not isinstance(field_name, str) or not field_name.strip():
                emit_error(
                    "validation_error",
                    f"Line {line_no} of '{file_path}' contains an invalid field name",
                    output_json=json_out,
                    hint="Each field name must be a non-empty string",
                    context="update",
                )
            if not isinstance(field_value, str):
                emit_error(
                    "validation_error",
                    f"Line {line_no} of '{file_path}' field '{field_name}' must be a string in batch mode",
                    output_json=json_out,
                    hint="Batch metadata cleanup currently supports string field values only",
                    context="update",
                )
            clean_fields[field_name] = field_value

        updates.append({"line": line_no, "key": key, "fields": clean_fields})

    if not updates:
        emit_error(
            "validation_error",
            f"JSONL file '{file_path}' is empty or has no valid update rows",
            output_json=json_out,
            hint='Expected JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
            context="update",
        )

    return updates


@click.command("update")
@click.argument("key", required=False)
@click.option("--title", default=None, help="New title")
@click.option("--date", default=None, help="New date (e.g. 2025-01-01)")
@click.option("--field", multiple=True, help="Set field as key=value (repeatable)")
@click.option(
    "--from-jsonl",
    "from_jsonl",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help='Batch update from JSONL rows like {"key":"ABC123","fields":{"title":"New title"}}',
)
@click.option("--add-tag", "add_tags", multiple=True, help="Add tag(s) after a successful update (repeatable)")
@click.option("--dry-run", is_flag=True, help="Preview the update without executing")
@click.option("--idempotency-key", default=None, help="Key so retries are safe; same key returns the original result")
@click.pass_context
def update_cmd(
    ctx: click.Context,
    key: str | None,
    title: str | None,
    date: str | None,
    field: tuple[str, ...],
    from_jsonl: Path | None,
    add_tags: tuple[str, ...],
    dry_run: bool,
    idempotency_key: str | None,
) -> None:
    """Update item metadata fields via the Zotero API. MUTATES LIBRARY.

    \b
    Examples:
      zot update ABC123 --title "New Title"
      zot update ABC123 --date "2025-01-01"
      zot update ABC123 --field volume=42 --field pages=1-10
      zot update ABC123 --title "Title" --field abstractNote="New abstract"
      zot update ABC123 --title "New" --dry-run
      zot update --from-jsonl updates.jsonl --add-tag update/metadata
    """
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
                hint="Use either 'zot update KEY ...' or 'zot update --from-jsonl updates.jsonl'",
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
                click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
            else:
                click.echo(f"[dry-run] Would update {len(updates)} item(s) from {from_jsonl}")
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
                    click.echo(f"  [{index}/{len(updates)}] Updated: {row_key}", err=True)
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
                    click.echo(f"  [{index}/{len(updates)}] Failed: {row_key} ({e})", err=True)

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
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            click.echo(f"\nDone: {len(succeeded)} updated, {len(failed)} failed", err=True)
            if succeeded:
                click.echo(SYNC_REMINDER, err=True)
        return

    if key is None:
        emit_error(
            "validation_error",
            "Missing item key",
            output_json=json_out,
            hint="Use 'zot update KEY --title ...' or 'zot update --from-jsonl updates.jsonl'",
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
            click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
        else:
            click.echo(f"[dry-run] Would update '{key}' with {len(fields)} field(s): {list(fields.keys())}")
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

    # Idempotency cache check
    from zotero_cli.core.idempotency import get_cached, store_cached

    cache_scope = f"update:{key}"
    if idempotency_key:
        cached = get_cached(cache_scope, idempotency_key)
        if cached is not None:
            click.echo(json.dumps(cached, indent=2, ensure_ascii=False) if json_out else f"Updated '{key}' (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.update_item(key, fields)
        if add_tags:
            writer.add_tags(key, list(add_tags))
    except ZoteroWriteError as e:
        emit_error(
            e.code,
            str(e),
            output_json=json_out,
            retryable=e.retryable,
            context="update",
            hint=f"Failed to update '{key}'",
        )

    env = envelope_ok(
        {"key": key, "fields": fields, "tags_added": list(add_tags), "sync_required": True},
        extra={"next": [f"zot read {key}"]},
    )
    if idempotency_key:
        store_cached(cache_scope, idempotency_key, env)
    if json_out:
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        click.echo(f"Updated {len(fields)} field(s) for '{key}'.")
        click.echo(SYNC_REMINDER, err=True)
