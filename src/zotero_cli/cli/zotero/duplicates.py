"""zot duplicates: find likely duplicate items by DOI or title similarity."""

from __future__ import annotations

import json

import typer

from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import EXIT_CONFLICT
from zotero_cli.output.renderers import format_duplicates
from zotero_cli.zotero.reader import ZoteroReader


@app.command(name="duplicates")
def duplicates(
    ctx: typer.Context,
    strategy: str = typer.Option("both", "--by", help="检测策略：doi、title 或 both。"),
    threshold: float = typer.Option(0.85, help="标题相似度阈值。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
) -> None:
    """Find potential duplicate items in the library.

    \b
    Examples:
      zot duplicates
      zot duplicates --by doi
      zot duplicates --by title --threshold 0.9
      zot --json duplicates
    """
    if strategy not in {"doi", "title", "both"}:
        raise typer.BadParameter("by 必须是 doi、title 或 both")
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        limit = limit if limit is not None else ctx.obj.get("limit", cfg.default_limit)
        groups = reader.find_duplicates(strategy=strategy, threshold=threshold, limit=limit)
        if not groups:
            if ctx.obj.get("json"):
                typer.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                typer.echo("No duplicates found.")
            return
        typer.echo(format_duplicates(groups, output_json=ctx.obj.get("json", False)))
    finally:
        reader.close()
    # Signal "duplicates detected" via typed exit code so agents/scripts can
    # branch on `if zot duplicates ...; then ...; else act_on_duplicates; fi`.
    ctx.exit(EXIT_CONFLICT)
