"""`zot metadata` 的 Typer 命令层。"""

from __future__ import annotations

from typing import Annotated

import typer

from zotero_cli.metadata.summarize import summarize
from zotero_cli.metadata.summarize_all import summarize_all
from zotero_cli.metadata.update_status import update_status

metadata_app = typer.Typer(help="读取和更新 Zotero 元数据。", invoke_without_command=True, rich_markup_mode=None)


@metadata_app.callback()
def metadata_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@metadata_app.command("summarize")
def summarize_command(ctx: typer.Context, key: Annotated[str, typer.Argument()]) -> None:
    summarize(ctx, key)


@metadata_app.command("summarize-all")
def summarize_all_command(
    ctx: typer.Context,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    exclude_tags: Annotated[list[str], typer.Option("--exclude-tag")] = [],
    exclude_collection_keys: Annotated[list[str], typer.Option("--exclude-collection-key")] = [],
) -> None:
    summarize_all(ctx, offset, limit, tuple(exclude_tags), tuple(exclude_collection_keys))


@metadata_app.command("update-status")
def update_status_command(
    ctx: typer.Context,
    key: Annotated[str | None, typer.Argument()] = None,
    apply: Annotated[bool, typer.Option("--apply")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    ss_api_key: Annotated[str | None, typer.Option("--api-key")] = None,
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    update_status(ctx, key, apply, dry_run, ss_api_key, collection, limit)
