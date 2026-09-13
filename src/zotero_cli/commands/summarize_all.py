"""Batch export all item summaries for AI consumption."""

from __future__ import annotations

import json
from dataclasses import asdict

import click

from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.core.reader import ZoteroReader
from zotero_cli.formatter import emit_progress, envelope_ok
from zotero_cli.models import Item


def _writable_fields_for_item(item: Item) -> dict[str, str]:
    """AI-safe subset of writable fields for metadata cleanup workflows."""

    fields: dict[str, str] = {"title": item.title}
    if item.abstract is not None:
        fields["abstractNote"] = item.abstract
    for field_name in ("publicationTitle", "journalAbbreviation", "language", "publisher"):
        value = item.extra.get(field_name)
        if value:
            fields[field_name] = value
    return fields


def _summarize_item(item: Item, detail: str) -> dict:
    if detail == "minimal":
        return {
            "key": item.key,
            "title": item.title,
            "authors": [c.full_name for c in item.creators],
            "date": item.date,
        }
    if detail == "full":
        data = asdict(item)
        data["writable_fields"] = _writable_fields_for_item(item)
        return data
    return {
        "key": item.key,
        "title": item.title,
        "authors": [c.full_name for c in item.creators],
        "abstract": item.abstract,
        "tags": item.tags,
        "date": item.date,
    }


@click.command("summarize-all")
@click.option("--offset", default=0, help="Skip first N items (for pagination)")
@click.option("--limit", default=None, type=int, help="Limit results (overrides global --limit)")
@click.option(
    "--exclude-tag",
    "exclude_tags",
    multiple=True,
    help="Exclude items carrying this tag (repeatable). Useful for skipping already-processed metadata batches.",
)
@click.option(
    "--exclude-collection-key",
    "exclude_collection_keys",
    multiple=True,
    help="Exclude items in this collection key (repeatable). Useful for skipping archive/trash holding collections.",
)
@click.pass_context
def summarize_all_cmd(
    ctx: click.Context,
    offset: int,
    limit: int | None,
    exclude_tags: tuple[str, ...],
    exclude_collection_keys: tuple[str, ...],
) -> None:
    """Export item metadata in bulk for AI classification or cleanup workflows.

    \b
    Examples:
      zot summarize-all                   Export all items
      zot summarize-all --limit 100       First 100 items
      zot summarize-all --offset 100      Skip first 100 (pagination)
      zot summarize-all --exclude-tag update/metadata
      zot summarize-all --exclude-collection-key RHBNIDLJ
      zot --detail full summarize-all     Include full item metadata + writable field map
    """
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    limit = limit if limit is not None else ctx.obj.get("limit", 10000)
    detail = ctx.obj.get("detail", "standard")
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        emit_progress(
            "start",
            phase="summarize_all",
            offset=offset,
            limit=limit,
            detail=detail,
            exclude_tags=list(exclude_tags) if exclude_tags else None,
            exclude_collection_keys=list(exclude_collection_keys) if exclude_collection_keys else None,
        )
        result = reader.search("", limit=1_000_000, offset=0)
        excluded_collection_key_set = set(exclude_collection_keys)
        filtered_items = [
            item
            for item in result.items
            if (not exclude_tags or not any(tag in item.tags for tag in exclude_tags))
            and (
                not excluded_collection_key_set
                or not any(collection_key in excluded_collection_key_set for collection_key in item.collections)
            )
        ]
        total = len(filtered_items)
        paged_items = filtered_items[offset : offset + limit]
        page_total = len(paged_items)
        items = []
        for i, item in enumerate(paged_items, 1):
            if page_total >= 100 and i % max(1, page_total // 20) == 0:
                emit_progress("progress", phase="summarize_all", done=i, total=page_total, filtered_total=total)
            items.append(_summarize_item(item, detail))
        emit_progress("complete", phase="summarize_all", done=page_total, total=page_total, filtered_total=total)
        json_out = ctx.obj.get("json", False)
        if json_out:
            click.echo(
                json.dumps(
                    envelope_ok(
                        items,
                        meta={
                            "count": len(items),
                            "detail": detail,
                            "filtered_total": total,
                            "excluded_tags": list(exclude_tags),
                            "excluded_collection_keys": list(exclude_collection_keys),
                        },
                    ),
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            click.echo(json.dumps(items, indent=2, ensure_ascii=False))
    finally:
        reader.close()
