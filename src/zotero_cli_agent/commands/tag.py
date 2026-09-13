from __future__ import annotations

import json

import click

from zotero_cli_agent.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli_agent.core.reader import ZoteroReader
from zotero_cli_agent.core.writer import SYNC_REMINDER, ZoteroWriteError, ZoteroWriter
from zotero_cli_agent.exit_codes import EXIT_NOT_FOUND, EXIT_RUNTIME, emit_error
from zotero_cli_agent.formatter import envelope_error, envelope_ok, envelope_partial, print_error
from zotero_cli_agent.models import ErrorInfo


@click.command("tag")
@click.argument("keys", nargs=-1, required=True)
@click.option("--add", "add_tag", default=None, help="Add a tag")
@click.option("--remove", "remove_tag", default=None, help="Remove a tag")
@click.option("--dry-run", is_flag=True, help="Show what would change without executing")
@click.pass_context
def tag_cmd(
    ctx: click.Context, keys: tuple[str, ...], add_tag: str | None, remove_tag: str | None, dry_run: bool
) -> None:
    """View or manage tags for one or more items.

    View tags: zot tag KEY
    Batch add: zot tag KEY1 KEY2 KEY3 --add "newtag"
    Batch remove: zot tag KEY1 KEY2 --remove "oldtag"
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    if dry_run and (add_tag or remove_tag):
        data: dict[str, object] = {"keys": list(keys), "count": len(keys)}
        if add_tag:
            data["would_add"] = add_tag
        if remove_tag:
            data["would_remove"] = remove_tag
        if json_out:
            click.echo(json.dumps(envelope_ok(data, extra={"dry_run": True}), indent=2, ensure_ascii=False))
            return
        for key in keys:
            if add_tag:
                click.echo(f"[dry-run] Would add tag '{add_tag}' to '{key}'")
            if remove_tag:
                click.echo(f"[dry-run] Would remove tag '{remove_tag}' from '{key}'")
        return

    if add_tag or remove_tag:
        library_type = ctx.obj.get("library_type", "user")
        group_id = ctx.obj.get("group_id")
        write_library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
        if not write_library_id or not api_key:
            emit_error(
                "auth_missing",
                "Write credentials not configured",
                output_json=json_out,
                hint="Run 'zot config init' to set up API credentials",
                context="tag",
            )
        writer = ZoteroWriter(library_id=str(write_library_id), api_key=api_key, library_type=library_type)
        succeeded: list[dict[str, object]] = []
        failed: list[dict[str, object]] = []
        for key in keys:
            try:
                if add_tag:
                    writer.add_tags(key, [add_tag])
                    succeeded.append({"key": key, "tags_added": [add_tag]})
                    if not json_out:
                        click.echo(f"Tag '{add_tag}' added to '{key}'.")
                if remove_tag:
                    writer.remove_tags(key, [remove_tag])
                    succeeded.append({"key": key, "tags_removed": [remove_tag]})
                    if not json_out:
                        click.echo(f"Tag '{remove_tag}' removed from '{key}'.")
            except ZoteroWriteError as e:
                failed.append({"key": key, "error": {"code": e.code, "message": str(e), "retryable": e.retryable}})
                if not json_out:
                    print_error(
                        ErrorInfo(message=str(e), context="tag", hint=f"Failed for key '{key}'"), output_json=False
                    )
        if json_out:
            if failed and succeeded:
                env = envelope_partial(succeeded, failed, meta={"sync_required": bool(succeeded)})
                click.echo(json.dumps(env, indent=2, ensure_ascii=False))
                ctx.exit(EXIT_RUNTIME)
            if failed:
                env = envelope_error(
                    "api_error",
                    f"{len(failed)} tag update(s) failed",
                    retryable=True,
                    failed=failed,
                )
                click.echo(json.dumps(env, indent=2, ensure_ascii=False))
                ctx.exit(EXIT_RUNTIME)
            env = envelope_ok(
                {"succeeded": succeeded, "sync_required": True},
                meta={"count": len(succeeded)},
                extra={"next": [f"zot read {keys[0]}"] if len(keys) == 1 else []},
            )
            click.echo(json.dumps(env, indent=2, ensure_ascii=False))
            return
        if not failed:
            click.echo(SYNC_REMINDER)
        else:
            # Surface failures via typed exit so agents/scripts detect them.
            ctx.exit(EXIT_RUNTIME)
    else:
        # View mode — show tags for each key
        data_dir = get_data_dir(cfg)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(db_path, ctx.obj)
        reader = ZoteroReader(db_path, library_id=library_id)
        try:
            results: list[dict[str, object]] = []
            missing: list[dict[str, object]] = []
            for key in keys:
                item = reader.get_item(key)
                if item is None:
                    err: dict[str, object] = {
                        "key": key,
                        "error": {
                            "code": "not_found",
                            "message": f"Item '{key}' not found",
                            "retryable": False,
                        },
                    }
                    missing.append(err)
                    if not json_out:
                        print_error(
                            ErrorInfo(
                                message=f"Item '{key}' not found",
                                context="tag",
                                hint="Run 'zot search' to find valid item keys",
                            ),
                            output_json=False,
                        )
                    continue
                results.append({"key": key, "tags": item.tags})
                if not json_out:
                    click.echo(f"Tags for {key}: {', '.join(item.tags) if item.tags else '(none)'}")
            if json_out:
                if missing and results:
                    click.echo(
                        json.dumps(
                            envelope_partial(results, missing, meta={"count": len(results)}),
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    return
                if missing:
                    click.echo(
                        json.dumps(
                            envelope_error(
                                "not_found",
                                f"{len(missing)} item(s) not found",
                                retryable=False,
                                failed=missing,
                                hint="Run 'zot search' to find valid item keys",
                            ),
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    raise SystemExit(EXIT_NOT_FOUND)
                click.echo(json.dumps(envelope_ok(results, meta={"count": len(results)}), indent=2, ensure_ascii=False))
        finally:
            reader.close()
