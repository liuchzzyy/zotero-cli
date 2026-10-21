"""zot zotero export：将引文导出为 BibTeX、CSL-JSON、RIS 或 JSON。"""

from __future__ import annotations

import json
from dataclasses import asdict

import typer

from zotero_cli.cli.commands.zotero.reading import app
from zotero_cli.cli.output.envelope import envelope_ok
from zotero_cli.cli.output.exit_codes import emit_error
from zotero_cli.cli.output.sanitize import sanitize_text
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.zotero.reader import ZoteroReader


@app.command(name="export")
def export(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    fmt: str = typer.Option("bibtex", "--format", help="导出格式。"),
) -> None:
    """以 BibTeX、CSL-JSON、RIS 或原始 JSON 格式导出引文。

    \b
    示例：
      zot zotero export ABC123                    BibTeX（默认）
      zot zotero export ABC123 --format csl-json  CSL-JSON
      zot zotero export ABC123 --format ris       RIS
      zot zotero export ABC123 --format json      原始 JSON 元数据
    """
    if fmt not in {"bibtex", "csl-json", "ris", "json"}:
        raise typer.BadParameter("format 必须是 bibtex、csl-json、ris 或 json")
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    json_out = ctx.obj.get("json", False)
    try:
        if fmt == "json":
            item = reader.get_item(key)
            if item is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint="Run 'zot zotero search' to find valid item keys",
                    context="export",
                )
            data = asdict(item)
            if json_out:
                typer.echo(json.dumps(envelope_ok(data, meta={"format": "json"}), indent=2, ensure_ascii=False))
            else:
                # 文本模式保留原始 JSON 负载，以便直接传递给其他程序。
                typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            result = reader.export_citation(key, fmt=fmt)
            if result is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint="Run 'zot zotero search' to find valid item keys",
                    context="export",
                )
            if json_out:
                typer.echo(
                    json.dumps(
                        envelope_ok({"format": fmt, "citation": result}, meta={"format": fmt}),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                typer.echo(sanitize_text(result))
    finally:
        reader.close()
