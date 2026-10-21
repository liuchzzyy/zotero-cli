"""zot zotero relate：查找通过显式关联、集合或标签相关的条目。"""

from __future__ import annotations

import json

import typer

from zotero_cli.cli.commands.zotero.reading import app
from zotero_cli.cli.output.envelope import envelope_ok
from zotero_cli.cli.output.exit_codes import emit_error
from zotero_cli.cli.output.renderers import format_items
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.zotero.reader import ZoteroReader


@app.command(name="relate")
def relate(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
) -> None:
    """通过共享标签、集合或显式关联查找相关条目。

    \b
    示例：
      zot zotero relate ABC123
      zot --json zotero relate ABC123
    """
    config = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        if reader.get_item(key) is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="relate",
            )
        limit = limit if limit is not None else ctx.obj.get("limit", 20)
        items = reader.get_related_items(key, limit=limit)
        if not items:
            # 空结果是正常情况而非错误——以退出码 0 结束并给出友好提示。
            if json_out:
                typer.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                typer.echo(
                    f"No related items found for '{key}'. Items need shared tags or collections to find relations."
                )
            return
        detail = ctx.obj.get("detail", "standard")
        typer.echo(format_items(items, output_json=json_out, detail=detail))
    finally:
        reader.close()
