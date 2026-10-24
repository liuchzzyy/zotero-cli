"""命令层共用的上下文读取与 Zotero 读取器构造。"""

from __future__ import annotations

from typing import Any

import typer

from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.zotero.access.reader import ZoteroReader


def load_context_config(ctx: typer.Context | Any) -> Any:
    """按当前 profile 读取配置。"""
    obj = getattr(ctx, "obj", None) or {}
    return load_config(profile=obj.get("profile"))


def database_path(config: Any) -> Any:
    return get_data_dir(config) / "zotero.sqlite"


def open_reader(ctx: typer.Context | Any) -> ZoteroReader:
    """只读打开当前库；调用方负责 close。"""
    config = load_context_config(ctx)
    path = database_path(config)
    obj = getattr(ctx, "obj", None) or {}
    return ZoteroReader(path, library_id=resolve_library_id(path, obj))


def option(ctx: typer.Context | Any, name: str, default: Any = None) -> Any:
    """读取根入口写入 ctx.obj 的全局选项。"""
    obj = getattr(ctx, "obj", None) or {}
    return obj.get(name, default)


def resolve_limit(command_limit: Any, ctx: typer.Context | Any, config: Any) -> int:
    """显式命令参数 > 根入口 --limit > TOML output.limit。"""
    if command_limit is not None:
        return int(command_limit)
    root_limit = option(ctx, "limit")
    if root_limit is not None:
        return int(root_limit)
    return int(config.default_limit)
