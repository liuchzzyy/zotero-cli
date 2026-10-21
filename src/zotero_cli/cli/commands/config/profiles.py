"""`zot config profile` - 列出并选择配置 profile。"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from zotero_cli.cli.commands.config.commands import _emit_config_error, _json_out
from zotero_cli.config import (
    ConfigError,
    get_default_profile,
    list_profiles,
    read_config_data,
    resolve_active_profile,
    resolve_config_path,
    write_config_data,
)
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error

app = typer.Typer(help="管理配置 profile。", invoke_without_command=True, rich_markup_mode=None)


@app.callback()
def profile_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("list")
def profile_list(
    ctx: typer.Context,
    config_path: str | None = typer.Option(None, "--config-path", help="配置文件路径。"),
) -> None:
    """列出所有 profile。"""
    json_out = _json_out(ctx)
    try:
        path = resolve_config_path(config_path, must_exist=config_path is not None)
        profiles = list_profiles(path)
        default = resolve_active_profile({name: {} for name in profiles}, get_default_profile(path)) or ""
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    if json_out:
        typer.echo(
            json.dumps(
                envelope_ok({"profiles": profiles, "default": default}, meta={"count": len(profiles)}),
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not profiles:
        typer.echo("No profiles configured.")
        return
    for name in profiles:
        marker = " (default)" if name == default else ""
        typer.echo(f"  {name}{marker}")


@app.command("set")
def profile_set(
    ctx: typer.Context,
    name: str = typer.Argument(help="profile 名称。"),
    config_path: str | None = typer.Option(None, "--config-path", help="配置文件路径。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不写入。"),
) -> None:
    """设置默认 profile，保持其他配置段不变。"""
    json_out = _json_out(ctx)
    try:
        path = resolve_config_path(config_path, must_exist=True)
        profiles = list_profiles(path)
        if name not in profiles:
            emit_error(
                "not_found",
                f"Profile '{name}' not found",
                output_json=json_out,
                hint=f"Available profiles: {', '.join(profiles)}",
                context="config",
            )
        data = read_config_data(path)
        default_table = data.get("default")
        if default_table is None:
            default_table = {}
        if not isinstance(default_table, dict):
            raise ConfigError("Invalid config: [default] must be a table")
        backup: Path | None = None
        if default_table.get("profile") != name:
            default_table["profile"] = name
            data["default"] = default_table
            if not dry_run:
                backup = write_config_data(path, data)
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    payload = {"profile": name, "config_path": str(path), "backup_path": str(backup) if backup else ""}
    if json_out:
        extra = {"dry_run": True} if dry_run else None
        typer.echo(json.dumps(envelope_ok(payload, extra=extra), indent=2, ensure_ascii=False))
        return
    if dry_run:
        typer.echo(f"Would set default profile to '{name}'.")
    else:
        typer.echo(f"Default profile set to '{name}'.")
