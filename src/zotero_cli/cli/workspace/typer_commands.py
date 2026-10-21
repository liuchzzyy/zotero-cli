"""Workspace 的 Typer 命令层。"""

from __future__ import annotations

from typing import Annotated

import typer

from zotero_cli.cli.workspace.commands import (
    workspace_add,
    workspace_delete,
    workspace_export,
    workspace_import_cmd,
    workspace_list,
    workspace_new,
    workspace_remove,
    workspace_search,
    workspace_show,
)
from zotero_cli.cli.workspace.embedding import workspace_embed
from zotero_cli.cli.workspace.indexing import workspace_index, workspace_reindex
from zotero_cli.cli.workspace.query import workspace_query

workspace_app = typer.Typer(help="管理本地工作区及其检索索引。", invoke_without_command=True, rich_markup_mode=None)


@workspace_app.callback()
def workspace_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@workspace_app.command("new")
def new(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_new(ctx, name, description, dry_run)


@workspace_app.command("delete")
def delete(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_delete(ctx, name, yes, dry_run)


@workspace_app.command("add")
def add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    keys: Annotated[list[str], typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_add(ctx, name, tuple(keys), dry_run)


@workspace_app.command("remove")
def remove(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    keys: Annotated[list[str], typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_remove(ctx, name, tuple(keys), dry_run)


@workspace_app.command("list")
def list_(ctx: typer.Context) -> None:
    workspace_list(ctx)


@workspace_app.command("show")
def show(ctx: typer.Context, name: Annotated[str, typer.Argument()]) -> None:
    workspace_show(ctx, name)


@workspace_app.command("export")
def export(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    fmt: Annotated[str, typer.Option("--format")] = "markdown",
) -> None:
    workspace_export(ctx, name, fmt)


@workspace_app.command("import")
def import_(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    search_query: Annotated[str | None, typer.Option("--search")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_import_cmd(ctx, name, collection, tag, search_query, dry_run)


@workspace_app.command("search")
def search(
    ctx: typer.Context, query: Annotated[str, typer.Argument()], ws_name: Annotated[str, typer.Option("--workspace")]
) -> None:
    workspace_search(ctx, query, ws_name)


@workspace_app.command("index")
def index(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option("--force")] = False,
    progress_lines: Annotated[bool, typer.Option("--progress-lines")] = False,
    item_progress: Annotated[bool, typer.Option("--item-progress")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_index(ctx, name, force, progress_lines, item_progress, no_embed, dry_run)


@workspace_app.command("reindex")
def reindex(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    progress_lines: Annotated[bool, typer.Option("--progress-lines")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_reindex(ctx, name, progress_lines, no_embed, dry_run)


@workspace_app.command("embed")
def embed(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    batch_size: Annotated[int, typer.Option("--batch-size")] = 50,
    limit: Annotated[int, typer.Option("--limit")] = 0,
    max_retries: Annotated[int, typer.Option("--max-retries")] = 5,
    retry_sleep: Annotated[float, typer.Option("--retry-sleep")] = 10.0,
    deadline_seconds: Annotated[float, typer.Option("--deadline-seconds")] = 120.0,
    heartbeat_seconds: Annotated[float, typer.Option("--heartbeat-seconds")] = 15.0,
    progress_lines: Annotated[bool, typer.Option("--progress-lines")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    workspace_embed(
        ctx,
        name,
        batch_size,
        limit,
        max_retries,
        retry_sleep,
        heartbeat_seconds,
        deadline_seconds,
        progress_lines,
        dry_run,
    )


@workspace_app.command("query")
def query(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument()],
    ws_name: Annotated[str, typer.Option("--workspace")],
    top_k: Annotated[int | None, typer.Option("--top-k", min=1)] = None,
    mode: Annotated[str, typer.Option("--mode")] = "auto",
    pdf_kind: Annotated[str, typer.Option("--pdf-kind")] = "any",
    rerank: Annotated[bool, typer.Option("--rerank")] = False,
    rerank_top_n: Annotated[int, typer.Option("--rerank-top-n")] = 50,
) -> None:
    workspace_query(ctx, question, ws_name, top_k, mode, pdf_kind, rerank, rerank_top_n)
