"""pdf_rag 的浏览、修改、导入与搜索命令。"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import asdict
from datetime import UTC, datetime

import typer

from zotero_cli.cli.commands.pdf_rag._output import (
    emit_result,
    emit_warning,
    load_pdf_rag_if_readable,
    load_pdf_rag_or_exit,
    output_json,
    pdf_rag_directory_or_exit,
    pdf_rag_exists_or_error,
)
from zotero_cli.cli.commands.pdf_rag.indexing import (
    drop_pdf_rag_vector_collection,
    prune_pdf_rag_items,
)
from zotero_cli.cli.renderers import format_items, format_pdf_rag_list
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.pdf_rag.store import (
    PdfRag,
    cleanup_empty_pdf_rag_root,
    delete_pdf_rag,
    list_pdf_rags,
    pdf_rag_file_path,
    save_pdf_rag,
    validate_name,
)
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.models import Collection, Item

_ADD_HINT = "Use 'zot pdf-rag new' to create it first"


def _open_reader(ctx: typer.Context) -> ZoteroReader:
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    return ZoteroReader(db_path, library_id=library_id)


def _resolve_collection_key(reader: ZoteroReader, name_or_key: str) -> str | None:
    """将 collection 名称或 key 解析为 collection key。"""
    collections = reader.get_collections()

    def _search(colls: list[Collection]) -> str | None:
        for c in colls:
            if c.key == name_or_key or c.name.lower() == name_or_key.lower():
                return c.key
            found = _search(c.children)
            if found:
                return found
        return None

    return _search(collections)


def pdf_rag_new(ctx: typer.Context, name: str, description: str, dry_run: bool) -> None:
    """创建新的 pdf_rag。"""
    json_out = output_json(ctx)
    if not validate_name(name):
        emit_error(
            "validation_error",
            f"Invalid pdf_rag name: '{name}'",
            output_json=json_out,
            hint="Use kebab-case (e.g., llm-safety, protein-folding)",
            context="pdf_rag new",
        )
    if pdf_rag_exists_or_error(ctx, name, context="pdf_rag new"):
        emit_error(
            "conflict",
            f"PdfRag '{name}' already exists",
            output_json=json_out,
            hint=f"Use 'zot pdf-rag show {name}' to view it",
            context="pdf_rag new",
        )
    created = datetime.now(UTC).isoformat()
    data = {"name": name, "description": description, "created": created, "items": 0}
    if dry_run:
        emit_result(
            ctx,
            {**data, "would_create": True},
            human_text=f"[dry-run] Would create pdf_rag '{name}'",
            dry_run=True,
        )
        return
    save_pdf_rag(PdfRag(name=name, created=created, description=description))
    emit_result(ctx, data, human_text=f"PdfRag created: {name}")


def pdf_rag_delete(ctx: typer.Context, name: str, yes: bool, dry_run: bool) -> None:
    """删除 pdf_rag。"""
    json_out = output_json(ctx)
    pdf_rag_directory_or_exit(ctx, name, context="pdf_rag delete")
    try:
        manifest_path = pdf_rag_file_path(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context="pdf_rag delete")
    pdf_rag = load_pdf_rag_if_readable(name) if manifest_path.exists() else None
    item_count = len(pdf_rag.items) if pdf_rag is not None else None
    data: dict[str, object] = {"name": name, "items": item_count, "deleted": True}
    if dry_run:
        human = f"[dry-run] Would delete pdf_rag '{name}'"
        if item_count is not None:
            human += f" ({item_count} item(s))"
        emit_result(ctx, {**data, "would_delete": True}, human_text=human, dry_run=True)
        return
    no_interaction = bool(ctx.obj.get("no_interaction", False))
    if not yes:
        if no_interaction or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Refusing to delete pdf_rag '{name}' without confirmation on non-interactive stdin",
                output_json=json_out,
                hint="Pass --yes to confirm or use --dry-run to preview",
                context="pdf_rag delete",
            )
        if not typer.confirm(f"Delete pdf_rag '{name}'?"):
            emit_result(ctx, {"name": name, "cancelled": True}, human_text="Cancelled.")
            return
    try:
        drop_pdf_rag_vector_collection(name)
    except (OSError, RuntimeError, ValueError) as exc:
        emit_warning(f"could not remove vector data for pdf_rag '{name}': {exc}")
    delete_pdf_rag(name)
    cleanup_empty_pdf_rag_root()
    if pdf_rag is None:
        emit_warning(f"pdf_rag manifest for '{name}' was unreadable; removed the directory anyway")
    emit_result(ctx, data, human_text=f"PdfRag deleted: {name}")


def pdf_rag_add(ctx: typer.Context, name: str, keys: tuple[str, ...], dry_run: bool) -> None:
    """按 Zotero key 将条目添加到 pdf_rag。"""
    pdf_rag = load_pdf_rag_or_exit(ctx, name, context="pdf_rag add", hint=_ADD_HINT)
    reader = _open_reader(ctx)
    added: list[str] = []
    skipped: list[str] = []
    missing: list[str] = []
    try:
        for key in keys:
            item = reader.get_item(key)
            if item is None:
                missing.append(key)
                continue
            if pdf_rag.add_item(key, item.title):
                added.append(key)
            else:
                skipped.append(key)
        data = {
            "name": name,
            "added": added,
            "skipped": skipped,
            "missing": missing,
            "items": len(pdf_rag.items),
        }
        if dry_run:
            emit_result(
                ctx,
                {**data, "would_add": added},
                human_text=f"[dry-run] Would add {len(added)} item(s) to pdf_rag '{name}'",
                dry_run=True,
            )
            return
        save_pdf_rag(pdf_rag)
        for key in missing:
            emit_warning(f"item '{key}' not found in Zotero library, skipped")
        for key in skipped:
            emit_warning(f"'{key}' already in pdf_rag")
        emit_result(ctx, data, human_text=f"Added {len(added)} item(s) to pdf_rag '{name}'", meta={"count": len(added)})
    finally:
        reader.close()


def pdf_rag_remove(ctx: typer.Context, name: str, keys: tuple[str, ...], dry_run: bool) -> None:
    """按键从 pdf_rag 中移除条目。"""
    pdf_rag = load_pdf_rag_or_exit(ctx, name, context="pdf_rag remove")
    removed: list[str] = []
    not_present: list[str] = []
    for key in keys:
        if pdf_rag.remove_item(key):
            removed.append(key)
        else:
            not_present.append(key)
    data = {"name": name, "removed": removed, "not_present": not_present, "items": len(pdf_rag.items)}
    if dry_run:
        emit_result(
            ctx,
            {**data, "would_remove": removed},
            human_text=f"[dry-run] Would remove {len(removed)} item(s) from pdf_rag '{name}'",
            dry_run=True,
        )
        return
    save_pdf_rag(pdf_rag)
    index_refresh_required = False
    pruned_index_items = 0
    if removed:
        try:
            pruned_index_items = prune_pdf_rag_items(name, removed)
        except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
            index_refresh_required = True
            emit_warning(
                f"could not prune the pdf_rag index ({exc}); it may still return removed items until "
                f"you run 'zot pdf-rag index {name}'"
            )
    data["pruned_index_items"] = pruned_index_items
    data["index_refresh_required"] = index_refresh_required
    human = f"Removed {len(removed)} item(s) from pdf_rag '{name}'"
    if pruned_index_items:
        human += f"; pruned {pruned_index_items} item(s) from the index"
    emit_result(ctx, data, human_text=human, meta={"count": len(removed)})


def pdf_rag_list(ctx: typer.Context) -> None:
    """列出所有 pdf_rag。"""
    pdf_rags = list_pdf_rags()
    if not pdf_rags:
        emit_result(
            ctx,
            [],
            human_text="No pdf_rags found. Create one with: zot pdf-rag new <name>",
            meta={"count": 0},
        )
        return
    typer.echo(format_pdf_rag_list(pdf_rags, output_json=output_json(ctx)))


def pdf_rag_show(ctx: typer.Context, name: str) -> None:
    """显示 pdf_rag 中的条目。"""
    json_out = output_json(ctx)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)
    pdf_rag = load_pdf_rag_or_exit(ctx, name, context="pdf_rag show")
    if not pdf_rag.items:
        emit_result(
            ctx,
            [],
            human_text=f"PdfRag '{name}' is empty. Use 'zot pdf-rag add {name} KEY' to add items.",
            meta={"count": 0},
        )
        return
    reader = _open_reader(ctx)
    try:
        items = []
        missing = []
        for pdf_rag_item in pdf_rag.items[:limit]:
            item = reader.get_item(pdf_rag_item.key)
            if item is not None:
                items.append(item)
            else:
                missing.append(pdf_rag_item.key)
        if items:
            typer.echo(format_items(items, output_json=json_out, detail=detail))
        elif json_out:
            emit_result(ctx, [], human_text="", meta={"count": 0, "missing": missing})
        for key in missing:
            emit_warning(f"item '{key}' not found in Zotero library (may have been deleted)")
    finally:
        reader.close()


def _markdown_export(name: str, pdf_rag: PdfRag, items: list[Item]) -> str:
    lines = [f"# PdfRag: {name}"]
    desc_part = f" {pdf_rag.description}" if pdf_rag.description else ""
    lines.append(f"> {desc_part.strip()} ({len(items)} items)")
    lines.append("")
    for i, item in enumerate(items, 1):
        lines.append("---")
        lines.append(f"## {i}. {item.title}")
        authors = ", ".join(c.full_name for c in item.creators[:3])
        if len(item.creators) > 3:
            authors += " et al."
        year = item.date or "N/A"
        lines.append(f"**Authors:** {authors} | **Year:** {year} | **Key:** {item.key}")
        if item.tags:
            lines.append(f"**Tags:** {', '.join(item.tags)}")
        if item.abstract:
            lines.append(f"**Abstract:** {item.abstract}")
        lines.append("")
    return "\n".join(lines)


def pdf_rag_export(ctx: typer.Context, name: str, fmt: str) -> None:
    """导出 pdf_rag 条目以供外部使用。"""
    json_out = output_json(ctx)
    pdf_rag = load_pdf_rag_or_exit(ctx, name, context="pdf_rag export")
    if not pdf_rag.items:
        emit_result(
            ctx,
            {"format": fmt, "items": 0, "content": ""},
            human_text=f"PdfRag '{name}' is empty.",
            meta={"count": 0},
        )
        return
    reader = _open_reader(ctx)
    try:
        items = []
        for pdf_rag_item in pdf_rag.items:
            item = reader.get_item(pdf_rag_item.key)
            if item is not None:
                items.append(item)
        if not items:
            emit_result(
                ctx,
                {"format": fmt, "items": 0, "content": ""},
                human_text="No items could be resolved from Zotero library.",
                meta={"count": 0},
            )
            return
        if fmt == "json":
            payload = [asdict(item) for item in items]
            if json_out:
                emit_result(ctx, {"format": "json", "items": payload}, human_text="", meta={"count": len(items)})
            else:
                typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        elif fmt == "bibtex":
            entries = []
            for item in items:
                bib = reader.export_citation(item.key, fmt="bibtex")
                if bib:
                    entries.append(bib)
            content = "\n\n".join(entries)
            if json_out:
                emit_result(
                    ctx, {"format": "bibtex", "content": content}, human_text=content, meta={"count": len(entries)}
                )
            else:
                typer.echo(content)
        else:
            content = _markdown_export(name, pdf_rag, items)
            if json_out:
                emit_result(
                    ctx,
                    {"format": "markdown", "name": name, "content": content},
                    human_text=content,
                    meta={"count": len(items)},
                )
            else:
                typer.echo(content)
    finally:
        reader.close()


def pdf_rag_import_cmd(
    ctx: typer.Context,
    name: str,
    collection: str | None,
    tag: str | None,
    search_query: str | None,
    dry_run: bool,
) -> None:
    """从 collection、tag 或搜索结果批量导入条目到 pdf_rag。"""
    json_out = output_json(ctx)
    pdf_rag = load_pdf_rag_or_exit(ctx, name, context="pdf_rag import", hint=_ADD_HINT)
    if not collection and not tag and not search_query:
        emit_error(
            "validation_error",
            "Must specify at least one of --collection, --tag, or --search",
            output_json=json_out,
            hint="Example: zot pdf-rag import my-ws --search 'attention'",
            context="pdf_rag import",
        )
    reader = _open_reader(ctx)
    try:
        items_to_import: list[Item] = []
        sources: dict[str, int] = {}
        if collection:
            col_key = _resolve_collection_key(reader, collection)
            if col_key is None:
                emit_error(
                    "not_found",
                    f"Collection '{collection}' not found",
                    output_json=json_out,
                    hint="Use 'zot zotero collection list' to list available collections",
                    context="pdf_rag import",
                )
            collection_items = reader.get_collection_items(col_key)
            sources["collection"] = len(collection_items)
            items_to_import.extend(collection_items)
        if tag:
            result = reader.search(tag, limit=500)
            tagged = [item for item in result.items if tag.lower() in [t.lower() for t in item.tags]]
            sources["tag"] = len(tagged)
            items_to_import.extend(tagged)
        if search_query:
            result = reader.search(search_query, limit=500)
            sources["search"] = len(result.items)
            items_to_import.extend(result.items)
        seen: set[str] = set()
        unique_items: list[Item] = []
        for item in items_to_import:
            if item.key not in seen:
                seen.add(item.key)
                unique_items.append(item)
        added: list[str] = []
        skipped = 0
        for item in unique_items:
            if pdf_rag.add_item(item.key, item.title):
                added.append(item.key)
            else:
                skipped += 1
        data = {
            "name": name,
            "resolved": len(unique_items),
            "imported": len(added),
            "skipped": skipped,
            "sources": sources,
            "items": len(pdf_rag.items),
        }
        if dry_run:
            emit_result(
                ctx,
                {**data, "would_add": added},
                human_text=f"[dry-run] Would import {len(added)} item(s) into pdf_rag '{name}'"
                + (f" ({skipped} already present)" if skipped else ""),
                dry_run=True,
            )
            return
        save_pdf_rag(pdf_rag)
        if not unique_items:
            emit_warning(f"no items matched the requested source(s); nothing imported into '{name}'")
        human = f"Imported {len(added)} item(s) into pdf_rag '{name}'"
        if skipped:
            human += f" ({skipped} skipped, already present)"
        emit_result(ctx, data, human_text=human, meta={"count": len(added)})
    finally:
        reader.close()


def pdf_rag_search(ctx: typer.Context, query: str, pdf_rag_name: str) -> None:
    """在 pdf_rag 内按标题、作者或摘要搜索条目。"""
    json_out = output_json(ctx)
    detail = ctx.obj.get("detail", "standard")
    limit = ctx.obj.get("limit", 50)
    pdf_rag = load_pdf_rag_or_exit(ctx, pdf_rag_name, context="pdf_rag search")
    if not pdf_rag.items:
        emit_result(ctx, [], human_text=f"PdfRag '{pdf_rag_name}' is empty.", meta={"count": 0})
        return
    reader = _open_reader(ctx)
    try:
        query_lower = query.lower()
        matches = []
        for pdf_rag_item in pdf_rag.items:
            item = reader.get_item(pdf_rag_item.key)
            if item is None:
                continue
            searchable = " ".join(
                filter(
                    None,
                    [
                        item.title,
                        " ".join(c.full_name for c in item.creators),
                        item.abstract or "",
                        " ".join(item.tags),
                    ],
                )
            ).lower()
            if query_lower in searchable:
                matches.append(item)
        if not matches:
            emit_result(ctx, [], human_text="No matching items found.", meta={"count": 0})
            return
        typer.echo(format_items(matches[:limit], output_json=json_out, detail=detail))
    finally:
        reader.close()
