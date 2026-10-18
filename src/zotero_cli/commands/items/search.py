"""zot search: search items by title, abstract, creator, tag, or full text."""

from __future__ import annotations

import click

from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_items
from zotero_cli.output.streaming import stream_items


@click.command("search")
@click.argument("query")
@click.option(
    "--collection",
    default=None,
    help="Filter by Zotero collection (folder) name. Use 'zot collection list' to see available names.",
)
@click.option("--type", "item_type", default=None, help="Filter by item type (e.g. journalArticle, book, preprint)")
@click.option(
    "--sort",
    default=None,
    type=click.Choice(["dateAdded", "dateModified", "title", "creator"]),
    help="Sort results by field",
)
@click.option(
    "--direction",
    default="desc",
    type=click.Choice(["asc", "desc"]),
    help="Sort direction (default: desc)",
)
@click.option("--limit", default=None, type=int, help="Limit results (overrides global --limit)")
@click.option("--stream", is_flag=True, help="Emit NDJSON (one item per line) for incremental processing")
@click.pass_context
def search_cmd(
    ctx: click.Context,
    query: str,
    collection: str | None,
    item_type: str | None,
    sort: str | None,
    direction: str,
    limit: int | None,
    stream: bool,
) -> None:
    """Search the Zotero library by title, author, tag, or full text.

    \b
    Examples:
      zot search "transformer attention"
      zot search "GAN" --limit 5
      zot --json search "single cell"

    \b
    Filter by Zotero collection (folder):
      zot collection list                        # show available collections
      zot search "BERT" --collection "NLP"       # search within "NLP" collection
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        limit = limit if limit is not None else ctx.obj.get("limit", cfg.default_limit)
        json_out = ctx.obj.get("json", False)
        try:
            result = reader.search(
                query, collection=collection, item_type=item_type, sort=sort, direction=direction, limit=limit
            )
        except ValueError as e:
            emit_error("validation_error", str(e), output_json=json_out)
        detail = ctx.obj.get("detail", "standard")
        extra_meta: dict = {}
        limited = getattr(result, "fulltext_limited", None)
        if limited is not None:
            extra_meta["fulltext_limited"] = limited
        if limited and not json_out:
            click.echo(
                "Note: full-text search was bounded; some indexed attachment text may not have been searched.",
                err=True,
            )
        if stream:
            click.echo(stream_items(result.items, detail=detail, total=result.total, meta=extra_meta or None))
            return
        if not result.items:
            if json_out:
                click.echo(format_items([], output_json=True, meta=extra_meta or None))
            else:
                click.echo("No results found.", err=True)
            return
        click.echo(format_items(result.items, output_json=json_out, detail=detail, meta=extra_meta or None))
    finally:
        reader.close()
