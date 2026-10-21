"""集合增删改查与写入结果呈现。"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from zotero_cli.cli.context import open_reader
from zotero_cli.cli.renderers import format_collections, format_items, format_success
from zotero_cli.cli.tiered_group import TieredGroup
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.models import Collection
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError

app = typer.Typer(cls=TieredGroup, help="管理 Zotero 集合。", no_args_is_help=True, rich_markup_mode=None)

_COLLECTION_HINT = "Use 'zot zotero collection list' to list available collections"


def _emit_json_or_text(data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None) -> None:
    typer.echo(format_success(data, output_json=output_json, human_text=human_text, meta=meta))


def _emit_sync_success(data: Any, *, output_json: bool, human_text: str, meta: dict[str, Any] | None = None) -> None:
    if output_json:
        _emit_json_or_text(data, output_json=True, human_text=human_text, meta=meta)
        return
    typer.echo(human_text)
    typer.echo(SYNC_REMINDER)


def _emit_dry_run(ctx: typer.Context, data: Any, *, human_text: str, meta: dict[str, Any] | None = None) -> None:
    if ctx.obj.get("json", False):
        typer.echo(json.dumps(envelope_ok(data, meta=meta, dry_run=True), indent=2, ensure_ascii=False))
    else:
        typer.echo(human_text)


def emit_write_error(
    context: str,
    error: ZoteroWriteError,
    *,
    output_json: bool,
    hint: str = "",
) -> None:
    """保留写入失败的错误码与提交状态。"""
    emit_error(
        error.code,
        str(error),
        output_json=output_json,
        retryable=False if error.committed else error.retryable,
        committed=error.committed,
        hint=hint or _COLLECTION_HINT,
        context=context,
    )


def _flatten_collections(collections: list[Collection]) -> list[Collection]:
    flattened: list[Collection] = []
    for collection in collections:
        flattened.append(collection)
        flattened.extend(_flatten_collections(collection.children))
    return flattened


def _flatten_collection_keys(collections: list[Collection]) -> set[str]:
    return {collection.key for collection in _flatten_collections(collections)}


def _load_collections(ctx: typer.Context) -> list[Collection]:
    reader = open_reader(ctx)
    try:
        return reader.get_collections()
    finally:
        reader.close()


def _known_collection_keys(ctx: typer.Context) -> set[str]:
    return _flatten_collection_keys(_load_collections(ctx))


def _descendant_collections(ctx: typer.Context, key: str) -> list[dict[str, str]]:
    """获取本地已知的子集合，以预览级联删除范围。"""
    descendants: list[dict[str, str]] = []

    def walk(collections: list[Collection], inside: bool) -> None:
        for collection in collections:
            if inside:
                descendants.append({"key": collection.key, "name": collection.name})
            walk(collection.children, inside or collection.key == key)

    try:
        walk(_load_collections(ctx), False)
    except Exception:  # noqa: BLE001 - 本地快照可能不可用或过期
        return []
    return descendants


def _validate_collection_key(ctx: typer.Context, key: str, *, context: str) -> None:
    if key not in _known_collection_keys(ctx):
        emit_error(
            "not_found",
            f"Collection '{key}' not found",
            output_json=ctx.obj.get("json", False),
            hint=_COLLECTION_HINT,
            context=context,
        )


def _write_credentials(ctx: typer.Context, cfg: Any) -> tuple[str, str, str]:
    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=ctx.obj.get("json", False),
            hint="Run 'zot config init' to set up API credentials",
            context="collection",
        )
    return library_id, api_key, library_type


@app.command("list")
def collection_list(ctx: typer.Context) -> None:
    """列出所有集合。"""
    typer.echo(format_collections(_load_collections(ctx), output_json=ctx.obj.get("json", False)))


@app.command("items")
def collection_items(ctx: typer.Context, key: str) -> None:
    """列出集合内的条目。"""
    _validate_collection_key(ctx, key, context="collection items")
    reader = open_reader(ctx)
    try:
        items = reader.get_collection_items(key)
        typer.echo(format_items(items, output_json=ctx.obj.get("json", False)))
    finally:
        reader.close()


@app.command("create")
def collection_create(
    ctx: typer.Context,
    name: str = typer.Argument(help="集合名称。"),
    parent: str | None = typer.Option(None, help="父集合键。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览创建结果。"),
) -> None:
    """创建集合。"""
    json_out = ctx.obj.get("json", False)
    if dry_run:
        _emit_dry_run(
            ctx,
            {"name": name, "parent_key": parent, "would_create": True},
            human_text=f"[dry-run] Would create collection '{name}'" + (f" under parent {parent}" if parent else ""),
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    library_id, api_key, library_type = _write_credentials(ctx, cfg)
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        key = writer.create_collection(name, parent_key=parent)
    except ZoteroWriteError as e:
        emit_write_error("collection create", e, output_json=json_out, hint="Check API credentials and network")
    _emit_sync_success(
        {"key": key, "name": name, "parent_key": parent, "sync_required": True},
        output_json=json_out,
        human_text=f"Collection created: {key}",
    )


@app.command("move")
def collection_move(
    ctx: typer.Context,
    item_key: str = typer.Argument(help="条目键。"),
    collection_key: str = typer.Argument(help="目标集合键。"),
    source_collection: str | None = typer.Option(None, "--from", help="可选的来源集合键。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览移动结果。"),
) -> None:
    """添加条目到集合，或从来源集合移动条目。"""
    json_out = ctx.obj.get("json", False)
    if dry_run:
        action = "moved" if source_collection else "added"
        human = (
            f"[dry-run] Would move item {item_key} from collection {source_collection} to {collection_key}"
            if source_collection
            else f"[dry-run] Would add item {item_key} to collection {collection_key}"
        )
        _emit_dry_run(
            ctx,
            {
                "item_key": item_key,
                "collection_key": collection_key,
                "source_collection_key": source_collection,
                "action": action,
                "would_change": True,
            },
            human_text=human,
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    library_id, api_key, library_type = _write_credentials(ctx, cfg)
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.move_to_collection(item_key, collection_key, source_collection_key=source_collection)
    except ZoteroWriteError as e:
        emit_write_error("collection move", e, output_json=json_out, hint="Check item and collection keys")
    action = "moved" if source_collection else "added"
    human_text = (
        f"Item {item_key} moved from collection {source_collection} to {collection_key}"
        if source_collection
        else f"Item {item_key} added to collection {collection_key}"
    )
    _emit_sync_success(
        {
            "item_key": item_key,
            "collection_key": collection_key,
            "source_collection_key": source_collection,
            "action": action,
            "sync_required": True,
        },
        output_json=json_out,
        human_text=human_text,
    )


@app.command("delete")
def collection_delete(
    ctx: typer.Context,
    key: str = typer.Argument(help="集合键。"),
    yes: bool = typer.Option(False, "--yes", help="跳过交互确认。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览删除及子集合。"),
) -> None:
    """删除集合及其子集合。"""
    json_out = ctx.obj.get("json", False)
    descendants = _descendant_collections(ctx, key)
    descendant_payload = {"descendant_collections": descendants, "descendant_count": len(descendants)}
    if dry_run:
        data = {"key": key, "would_delete": True, **descendant_payload}
        human = f"[dry-run] Would delete collection '{key}'"
        if descendants:
            names = ", ".join(f"{c['key']} ({c['name']})" for c in descendants[:10])
            human += f" and {len(descendants)} nested collection(s): {names}"
        _emit_dry_run(ctx, data, human_text=human)
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    no_interaction = bool(ctx.obj.get("no_interaction", False))
    if not yes:
        if no_interaction or not sys.stdin.isatty():
            nested_note = f" and {len(descendants)} nested collection(s)" if descendants else ""
            emit_error(
                "confirmation_required",
                f"Refusing to delete collection '{key}'{nested_note} without confirmation on non-interactive stdin",
                output_json=json_out,
                hint="Pass --yes to confirm or use --dry-run to preview",
                context="collection delete",
            )
        prompt = f"Delete collection '{key}'?"
        if descendants:
            prompt = f"Delete collection '{key}' and {len(descendants)} nested collection(s)?"
        if not typer.confirm(prompt):
            _emit_json_or_text({"key": key, "cancelled": True}, output_json=json_out, human_text="Cancelled.")
            return
    library_id, api_key, library_type = _write_credentials(ctx, cfg)
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.delete_collection(key)
    except ZoteroWriteError as e:
        emit_write_error("collection delete", e, output_json=json_out, hint="Check collection key")
    human = f"Collection {key} deleted"
    if descendants:
        human += f" (with {len(descendants)} nested collection(s))"
    _emit_sync_success(
        {"key": key, "deleted": True, **descendant_payload, "sync_required": True},
        output_json=json_out,
        human_text=human,
    )


@app.command("rename")
def collection_rename(
    ctx: typer.Context,
    key: str = typer.Argument(help="集合键。"),
    new_name: str = typer.Argument(help="新名称。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览重命名结果。"),
) -> None:
    """重命名集合。"""
    json_out = ctx.obj.get("json", False)
    if dry_run:
        _emit_dry_run(
            ctx,
            {"key": key, "new_name": new_name, "would_rename": True},
            human_text=f"[dry-run] Would rename collection {key} to '{new_name}'",
        )
        return
    cfg = load_config(profile=ctx.obj.get("profile"))
    library_id, api_key, library_type = _write_credentials(ctx, cfg)
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        writer.rename_collection(key, new_name)
    except ZoteroWriteError as e:
        emit_write_error("collection rename", e, output_json=json_out, hint="Check collection key")
    _emit_sync_success(
        {"key": key, "new_name": new_name, "sync_required": True},
        output_json=json_out,
        human_text=f"Collection {key} renamed to '{new_name}'",
    )


from zotero_cli.cli.zotero import collection_reorganize  # noqa: E402, F401
