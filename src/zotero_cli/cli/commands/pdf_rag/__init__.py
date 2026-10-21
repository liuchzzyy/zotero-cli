"""PdfRag 的 Typer 命令层。"""

from __future__ import annotations

from typing import Annotated

import typer

from zotero_cli.cli.app.group import TieredGroup
from zotero_cli.cli.commands.pdf_rag.embedding import pdf_rag_embed
from zotero_cli.cli.commands.pdf_rag.indexing import pdf_rag_index, pdf_rag_reindex
from zotero_cli.cli.commands.pdf_rag.operations import (
    pdf_rag_add,
    pdf_rag_delete,
    pdf_rag_export,
    pdf_rag_import_cmd,
    pdf_rag_list,
    pdf_rag_new,
    pdf_rag_remove,
    pdf_rag_search,
    pdf_rag_show,
)
from zotero_cli.cli.commands.pdf_rag.query import pdf_rag_query

pdf_rag_app = typer.Typer(
    cls=TieredGroup,
    help="管理本地工作区及其检索索引。",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@pdf_rag_app.callback()
def pdf_rag_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@pdf_rag_app.command("new")
def new(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    description: Annotated[str, typer.Option("--description", "-d")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """创建新的本地文献 RAG 工作区。"""
    pdf_rag_new(ctx, name, description, dry_run)


@pdf_rag_app.command("delete")
def delete(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """永久删除指定的工作区及其索引。"""
    pdf_rag_delete(ctx, name, yes, dry_run)


@pdf_rag_app.command("add")
def add(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    keys: Annotated[list[str], typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """向工作区添加文献条目。"""
    pdf_rag_add(ctx, name, tuple(keys), dry_run)


@pdf_rag_app.command("remove")
def remove(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    keys: Annotated[list[str], typer.Argument()],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """从工作区移除文献条目。"""
    pdf_rag_remove(ctx, name, tuple(keys), dry_run)


@pdf_rag_app.command("list")
def list_(ctx: typer.Context) -> None:
    """列出全部已创建的 RAG 工作区。"""
    pdf_rag_list(ctx)


@pdf_rag_app.command("show")
def show(ctx: typer.Context, name: Annotated[str, typer.Argument()]) -> None:
    """查看工作区包含的文献条目与元数据。"""
    pdf_rag_show(ctx, name)


@pdf_rag_app.command("export")
def export(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    fmt: Annotated[str, typer.Option("--format")] = "markdown",
) -> None:
    """将工作区清单导出为独立文件。"""
    pdf_rag_export(ctx, name, fmt)


@pdf_rag_app.command("import")
def import_(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    collection: Annotated[str | None, typer.Option("--collection")] = None,
    tag: Annotated[str | None, typer.Option("--tag")] = None,
    search_query: Annotated[str | None, typer.Option("--search")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """从集合、标签或检索词导入条目到工作区。"""
    pdf_rag_import_cmd(ctx, name, collection, tag, search_query, dry_run)


@pdf_rag_app.command("search")
def search(
    ctx: typer.Context, query: Annotated[str, typer.Argument()], pdf_rag_name: Annotated[str, typer.Option("--pdf-rag")]
) -> None:
    """对工作区已索引内容进行快速词条检索。"""
    pdf_rag_search(ctx, query, pdf_rag_name)


@pdf_rag_app.command("index")
def index(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    force: Annotated[bool, typer.Option("--force")] = False,
    progress_lines: Annotated[bool, typer.Option("--progress-lines")] = False,
    item_progress: Annotated[bool, typer.Option("--item-progress")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """为工作区文献建立词条倒排索引。"""
    pdf_rag_index(ctx, name, force, progress_lines, item_progress, no_embed, dry_run)


@pdf_rag_app.command("reindex")
def reindex(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    progress_lines: Annotated[bool, typer.Option("--progress-lines")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """强制重新解析并完全重建工作区索引。"""
    pdf_rag_reindex(ctx, name, progress_lines, no_embed, dry_run)


@pdf_rag_app.command("embed")
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
    """为工作区未嵌入切片批量计算向量。"""
    pdf_rag_embed(
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


@pdf_rag_app.command("query")
def query(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument()],
    pdf_rag_name: Annotated[str, typer.Option("--pdf-rag")],
    top_k: Annotated[int | None, typer.Option("--top-k", min=1)] = None,
    mode: Annotated[str, typer.Option("--mode")] = "auto",
    pdf_kind: Annotated[str, typer.Option("--pdf-kind")] = "any",
    rerank: Annotated[bool, typer.Option("--rerank")] = False,
    rerank_top_n: Annotated[int, typer.Option("--rerank-top-n")] = 50,
) -> None:
    """结合 BM25、语义向量与重排的多路混合检索问答。"""
    pdf_rag_query(ctx, question, pdf_rag_name, top_k, mode, pdf_kind, rerank, rerank_top_n)
