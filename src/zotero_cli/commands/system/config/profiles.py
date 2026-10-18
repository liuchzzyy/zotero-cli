"""`zot config profile` - list and select configuration profiles."""

from __future__ import annotations

import json

import click

from zotero_cli.commands.system.config.commands import _emit_config_error, _json_out
from zotero_cli.config import (
    ConfigError,
    get_default_profile,
    list_profiles,
    read_config_data,
    resolve_config_path,
    write_config_data,
)
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error


@click.group("profile", invoke_without_command=True)
@click.pass_context
def profile_group(ctx: click.Context) -> None:
    """Manage configuration profiles."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@profile_group.command("list")
@click.option("--config-path", type=click.Path(), default=None)
@click.pass_context
def profile_list(ctx: click.Context, config_path: str | None) -> None:
    """List all profiles."""
    json_out = _json_out(ctx)
    try:
        path = resolve_config_path(config_path, must_exist=config_path is not None)
        profiles = list_profiles(path)
        default = get_default_profile(path)
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    if json_out:
        click.echo(
            json.dumps(
                envelope_ok({"profiles": profiles, "default": default}, meta={"count": len(profiles)}),
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not profiles:
        click.echo("No profiles configured.")
        return
    for name in profiles:
        marker = " (default)" if name == default else ""
        click.echo(f"  {name}{marker}")


@profile_group.command("set")
@click.argument("name")
@click.option("--config-path", type=click.Path(), default=None)
@click.option("--dry-run", is_flag=True, help="Preview the change without writing")
@click.pass_context
def profile_set(ctx: click.Context, name: str, config_path: str | None, dry_run: bool) -> None:
    """Set the default profile, keeping every other config section intact."""
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
        backup_path = ""
        if default_table.get("profile") != name:
            default_table["profile"] = name
            data["default"] = default_table
            if not dry_run:
                write_config_data(path, data)
                backup_path = str(path.with_name(path.name + ".bak"))
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    payload = {"profile": name, "config_path": str(path), "backup_path": backup_path}
    if json_out:
        extra = {"dry_run": True} if dry_run else None
        click.echo(json.dumps(envelope_ok(payload, extra=extra), indent=2, ensure_ascii=False))
        return
    if dry_run:
        click.echo(f"Would set default profile to '{name}'.")
    else:
        click.echo(f"Default profile set to '{name}'.")
