"""查看或管理条目标签。"""

from __future__ import annotations

import json
from typing import Any

import typer

from zotero_cli.cli.commands.zotero.reading import app
from zotero_cli.cli.output.envelope import envelope_error, envelope_ok, envelope_partial
from zotero_cli.cli.output.exit_codes import EXIT_NOT_FOUND, emit_error, exit_code_for
from zotero_cli.cli.output.models import ErrorInfo
from zotero_cli.cli.output.renderers import print_error
from zotero_cli.config import get_data_dir, load_config, resolve_library_id, resolve_write_credentials
from zotero_cli.zotero.reader import ZoteroReader
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError


@app.command("tag")
def tag_cmd(
    ctx: typer.Context,
    keys: list[str] = typer.Argument(help="至少一个条目键。"),
    add_tag: str | None = typer.Option(None, "--add", help="新增标签。"),
    remove_tag: str | None = typer.Option(None, "--remove", help="移除标签。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写入。"),
) -> None:
    """查看或批量管理条目标签。"""
    if not keys:
        raise typer.BadParameter("至少提供一个条目键")
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)

    if dry_run and (add_tag or remove_tag):
        data: dict[str, object] = {"keys": list(keys), "count": len(keys)}
        if add_tag:
            data["would_add"] = add_tag
        if remove_tag:
            data["would_remove"] = remove_tag
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
            return
        for key in keys:
            if add_tag:
                typer.echo(f"[dry-run] Would add tag '{add_tag}' to '{key}'")
            if remove_tag:
                typer.echo(f"[dry-run] Would remove tag '{remove_tag}' from '{key}'")
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
        succeeded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for key in keys:
            try:
                if add_tag:
                    writer.add_tags(key, [add_tag])
                    succeeded.append({"key": key, "tags_added": [add_tag]})
                    if not json_out:
                        typer.echo(f"Tag '{add_tag}' added to '{key}'.")
                if remove_tag:
                    writer.remove_tags(key, [remove_tag])
                    succeeded.append({"key": key, "tags_removed": [remove_tag]})
                    if not json_out:
                        typer.echo(f"Tag '{remove_tag}' removed from '{key}'.")
            except ZoteroWriteError as e:
                failed.append(
                    {
                        "key": key,
                        "error": {
                            "code": e.code,
                            "message": str(e),
                            "retryable": e.retryable,
                            "committed": e.committed,
                        },
                    }
                )
                if not json_out:
                    print_error(
                        ErrorInfo(
                            message=str(e),
                            context="tag",
                            hint=f"Failed for key '{key}'" + (" (write may have committed)" if e.committed else ""),
                        ),
                        output_json=False,
                    )
        first_failure: dict[str, Any] | None = None
        if failed:
            candidate = failed[0].get("error")
            if isinstance(candidate, dict):
                first_failure = candidate
        exit_code = exit_code_for(str(first_failure["code"])) if first_failure else 0
        if json_out:
            if failed:
                assert first_failure is not None
            if failed and succeeded:
                env = envelope_partial(succeeded, failed, meta={"sync_required": bool(succeeded)})
                typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
                raise SystemExit(exit_code)
            if failed:
                assert first_failure is not None
                committed_extra = {"committed": True} if first_failure["committed"] else {}
                env = envelope_error(
                    str(first_failure["code"]),
                    f"{len(failed)} tag update(s) failed",
                    retryable=bool(first_failure["retryable"]) and not first_failure["committed"],
                    **committed_extra,
                    failed=failed,
                    hint=(
                        "The write outcome is unknown for at least one item; reconcile before retrying"
                        if first_failure["committed"]
                        else "Check item keys and API credentials"
                    ),
                )
                typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
                raise SystemExit(exit_code)
            env = envelope_ok(
                {"succeeded": succeeded, "sync_required": True},
                meta={"count": len(succeeded)},
                next=[f"zot zotero read {keys[0]}"] if len(keys) == 1 else None,
            )
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
            return
        if not failed:
            typer.echo(SYNC_REMINDER)
        else:
            # 写入失败时保留类型化退出码。
            raise SystemExit(exit_code)
    else:
        # 读取模式逐条展示标签
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
                                hint="Run 'zot zotero search' to find valid item keys",
                            ),
                            output_json=False,
                        )
                    continue
                results.append({"key": key, "tags": item.tags})
                if not json_out:
                    typer.echo(f"Tags for {key}: {', '.join(item.tags) if item.tags else '(none)'}")
            if json_out:
                if missing and results:
                    typer.echo(
                        json.dumps(
                            envelope_partial(results, missing, meta={"count": len(results)}),
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    return
                if missing:
                    typer.echo(
                        json.dumps(
                            envelope_error(
                                "not_found",
                                f"{len(missing)} item(s) not found",
                                retryable=False,
                                failed=missing,
                                hint="Run 'zot zotero search' to find valid item keys",
                            ),
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                    raise SystemExit(EXIT_NOT_FOUND)
                typer.echo(json.dumps(envelope_ok(results, meta={"count": len(results)}), indent=2, ensure_ascii=False))
        finally:
            reader.close()
