"""条目读取、列表与检索命令。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import typer

from zotero_cli.cli.tiered_group import TieredGroup
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_item_detail, format_items, sanitize_text
from zotero_cli.output.streaming import stream_items
from zotero_cli.zotero.reader import ZoteroReader

app = typer.Typer(cls=TieredGroup, help="读取和检索 Zotero 条目。", no_args_is_help=True, rich_markup_mode=None)

_SORT_FIELDS = {"dateAdded", "dateModified", "title", "creator"}
_DIRECTIONS = {"asc", "desc"}


def _create_reader(ctx: typer.Context) -> tuple[ZoteroReader, Any]:
    config = load_config(profile=ctx.obj.get("profile"))
    database_path = get_data_dir(config) / "zotero.sqlite"
    library_id = resolve_library_id(database_path, ctx.obj)
    return ZoteroReader(database_path, library_id=library_id), config


def _result_limit(limit: int | None, ctx: typer.Context, config: Any) -> int:
    return limit if limit is not None else ctx.obj.get("limit", config.default_limit)


def _validate_sort_options(sort: str | None, direction: str) -> None:
    if sort not in _SORT_FIELDS | {None}:
        raise typer.BadParameter("sort 必须是 dateAdded、dateModified、title 或 creator")
    if direction not in _DIRECTIONS:
        raise typer.BadParameter("direction 必须是 asc 或 desc")


def _render_search_result(
    *,
    result: Any,
    ctx: typer.Context,
    stream: bool,
    empty_message: str | None = None,
) -> None:
    json_output = ctx.obj.get("json", False)
    detail = ctx.obj.get("detail", "standard")
    fulltext_limited = getattr(result, "fulltext_limited", None)
    metadata = {"fulltext_limited": bool(fulltext_limited)} if fulltext_limited is not None else None
    if fulltext_limited and not json_output:
        typer.echo(
            "Note: full-text search was bounded; some indexed attachment text may not have been searched.", err=True
        )
    if stream:
        typer.echo(stream_items(result.items, detail=detail, total=result.total, meta=metadata))
        return
    if not result.items and empty_message is not None and not json_output:
        typer.echo(empty_message, err=True)
        return
    typer.echo(format_items(result.items, output_json=json_output, detail=detail, meta=metadata))


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(help="检索词。"),
    collection: str | None = typer.Option(None, help="按 Zotero 集合名称筛选。"),
    item_type: str | None = typer.Option(None, "--type", help="按条目类型筛选。"),
    sort: str | None = typer.Option(None, help="排序字段。"),
    direction: str = typer.Option("desc", help="排序方向：asc 或 desc。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """按标题、作者、标签或全文检索条目。"""
    _validate_sort_options(sort, direction)
    reader, config = _create_reader(ctx)
    try:
        try:
            result = reader.search(
                query,
                collection=collection,
                item_type=item_type,
                sort=sort,
                direction=direction,
                limit=_result_limit(limit, ctx, config),
            )
        except ValueError as error:
            emit_error("validation_error", str(error), output_json=ctx.obj.get("json", False))
        _render_search_result(result=result, ctx=ctx, stream=stream, empty_message="No results found.")
    finally:
        reader.close()


@app.command(name="list")
def list_items(
    ctx: typer.Context,
    collection: str | None = typer.Option(None, help="按 Zotero 集合名称筛选。"),
    item_type: str | None = typer.Option(None, "--type", help="按条目类型筛选。"),
    sort: str | None = typer.Option(None, help="排序字段。"),
    direction: str = typer.Option("desc", help="排序方向：asc 或 desc。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """列出 Zotero 库中的条目。"""
    _validate_sort_options(sort, direction)
    reader, config = _create_reader(ctx)
    try:
        try:
            result = reader.search(
                "",
                collection=collection,
                item_type=item_type,
                sort=sort,
                direction=direction,
                limit=_result_limit(limit, ctx, config),
            )
        except ValueError as error:
            emit_error("validation_error", str(error), output_json=ctx.obj.get("json", False))
        _render_search_result(result=result, ctx=ctx, stream=stream)
    finally:
        reader.close()


@app.command()
def stats(ctx: typer.Context) -> None:
    """显示条目、附件、笔记、集合和标签的统计信息。"""
    reader, _config = _create_reader(ctx)
    try:
        statistics = reader.get_stats()
        if ctx.obj.get("json", False):
            typer.echo(json.dumps(envelope_ok(statistics), indent=2, ensure_ascii=False))
            return
        typer.echo(f"Total items: {statistics['total_items']}")
        typer.echo(f"PDF attachments: {statistics['pdf_attachments']}")
        typer.echo(f"Notes: {statistics['notes']}")
        typer.echo("\nItems by type:")
        for name, count in statistics["by_type"].items():
            typer.echo(f"  {sanitize_text(name)}: {count}")
        typer.echo(f"\nCollections ({len(statistics['collections'])}):")
        for name, count in statistics["collections"].items():
            typer.echo(f"  {sanitize_text(name)}: {count} items")
        typer.echo(f"\nTop tags ({len(statistics['top_tags'])}):")
        for name, count in statistics["top_tags"].items():
            typer.echo(f"  {sanitize_text(name)}: {count}")
    finally:
        reader.close()


@app.command()
def recent(
    ctx: typer.Context,
    days: int = typer.Option(7, help="向前检索的天数。"),
    modified: bool = typer.Option(False, "--modified", help="按修改时间而不是添加时间排序。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """显示最近添加或修改的条目。"""
    reader, config = _create_reader(ctx)
    try:
        result_limit = _result_limit(limit, ctx, config)
        sort_field = "dateModified" if modified else "dateAdded"
        since = datetime.now(UTC) - timedelta(days=days)
        fetch_limit = result_limit + 1 if result_limit >= 0 else result_limit
        items = reader.get_recent_items(
            since=since.strftime("%Y-%m-%d %H:%M:%S"),
            sort=sort_field,
            limit=fetch_limit,
        )
        has_more = len(items) > result_limit
        if has_more:
            items = items[:result_limit]
        if stream:
            total = len(items) + 1 if has_more else len(items)
            typer.echo(stream_items(items, detail=ctx.obj.get("detail", "standard"), total=total))
            return
        json_output = ctx.obj.get("json", False)
        if not items and not json_output:
            typer.echo(f"No items {'modified' if modified else 'added'} in the last {days} day(s).", err=True)
            return
        typer.echo(format_items(items, output_json=json_output, detail=ctx.obj.get("detail", "standard")))
    finally:
        reader.close()


@app.command()
def read(ctx: typer.Context, key: str = typer.Argument(help="Zotero 条目键。")) -> None:
    """查看条目元数据、摘要和笔记。"""
    reader, _config = _create_reader(ctx)
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=ctx.obj.get("json", False),
                hint="Run 'zot zotero search' to find valid item keys",
                context="zotero read",
            )
        notes = reader.get_notes(key)
        typer.echo(
            format_item_detail(
                item,
                notes,
                output_json=ctx.obj.get("json", False),
                detail=ctx.obj.get("detail", "standard"),
            )
        )
    finally:
        reader.close()
