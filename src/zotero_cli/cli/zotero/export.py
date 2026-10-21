"""zot zotero export: export citations as BibTeX, CSL-JSON, RIS, or JSON."""

from __future__ import annotations

import json
from dataclasses import asdict

import typer

from zotero_cli.cli.renderers import sanitize_text
from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.reader import ZoteroReader


@app.command(name="export")
def export(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    fmt: str = typer.Option("bibtex", "--format", help="导出格式。"),
) -> None:
    """Export citation in BibTeX, CSL-JSON, RIS, or raw JSON format.

    \b
    Examples:
      zot zotero export ABC123                    BibTeX (default)
      zot zotero export ABC123 --format csl-json  CSL-JSON
      zot zotero export ABC123 --format ris       RIS
      zot zotero export ABC123 --format json      Raw JSON metadata
    """
    if fmt not in {"bibtex", "csl-json", "ris", "json"}:
        raise typer.BadParameter("format 必须是 bibtex、csl-json、ris 或 json")
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
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
