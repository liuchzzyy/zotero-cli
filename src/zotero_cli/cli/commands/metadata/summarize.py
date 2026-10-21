"""汇总单个条目元数据：输出紧凑的结构化摘要。"""

from __future__ import annotations

import json

import click

from zotero_cli.cli.output.envelope import envelope_ok
from zotero_cli.cli.output.exit_codes import emit_error
from zotero_cli.cli.output.sanitize import sanitize_text
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.zotero.reader import ZoteroReader


def summarize(ctx: click.Context, key: str) -> None:
    """输出供 AI 代理程序使用的结构化摘要。

    \b
    示例：
      zot metadata summarize ABC123
      zot --json metadata summarize ABC123
      zot --detail minimal metadata summarize ABC123
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="summarize",
            )
        notes = reader.get_notes(key)
        detail = ctx.obj.get("detail", "standard")
        if json_out:
            data: dict = {
                "title": item.title,
                "authors": [c.full_name for c in item.creators],
                "year": item.date,
                "doi": item.doi,
            }
            if detail != "minimal":
                data["abstract"] = item.abstract
                data["tags"] = item.tags
                data["notes"] = [n.content[:500] for n in notes]
            click.echo(json.dumps(envelope_ok(data, meta={"detail": detail}), indent=2, ensure_ascii=False))
        else:
            click.echo(f"Title: {sanitize_text(item.title)}")
            click.echo(f"Authors: {', '.join(sanitize_text(c.full_name) for c in item.creators)}")
            click.echo(f"Year: {sanitize_text(item.date or 'N/A')}")
            if item.doi:
                click.echo(f"DOI: {sanitize_text(item.doi)}")
            if item.url:
                click.echo(f"URL: {sanitize_text(item.url)}")
            if detail != "minimal":
                if item.tags:
                    click.echo(f"Tags: {', '.join(sanitize_text(t) for t in item.tags)}")
                if item.extra:
                    journal = sanitize_text(item.extra.get("publicationTitle"))
                    volume = sanitize_text(item.extra.get("volume", ""))
                    issue = sanitize_text(item.extra.get("issue", ""))
                    pages = sanitize_text(item.extra.get("pages", ""))
                    parts = [
                        p
                        for p in [
                            journal,
                            f"vol.{volume}" if volume else "",
                            f"({issue})" if issue else "",
                            f"pp.{pages}" if pages else "",
                        ]
                        if p
                    ]
                    if parts:
                        click.echo(f"Source: {' '.join(parts)}")
                if item.abstract:
                    click.echo(f"\nAbstract:\n{sanitize_text(item.abstract)}")
                if notes:
                    click.echo(f"\nNotes ({len(notes)}):")
                    for n in notes:
                        click.echo(f"  {sanitize_text(n.content[:500])}")
    finally:
        reader.close()
