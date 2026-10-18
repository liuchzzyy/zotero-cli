"""`zot config profile` — list and select configuration profiles."""

from __future__ import annotations

import re
from pathlib import Path

import click

from zotero_cli.config import config_file_path, get_default_profile, list_profiles


@click.group("profile", invoke_without_command=True)
@click.pass_context
def profile_group(ctx: click.Context) -> None:
    """Manage configuration profiles."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@profile_group.command("list")
@click.option("--config-path", type=click.Path(), default=None)
def profile_list(config_path: str | None) -> None:
    """List all profiles."""
    path = Path(config_path) if config_path else config_file_path()
    profiles = list_profiles(path)
    default = get_default_profile(path)
    if not profiles:
        click.echo("No profiles configured.")
        return
    for p in profiles:
        marker = " (default)" if p == default else ""
        click.echo(f"  {p}{marker}")


@profile_group.command("set")
@click.argument("name")
@click.option("--config-path", type=click.Path(), default=None)
def profile_set(name: str, config_path: str | None) -> None:
    """Set the default profile."""
    path = Path(config_path) if config_path else config_file_path()
    from zotero_cli.output.exit_codes import emit_error

    profiles = list_profiles(path)
    if name not in profiles:
        emit_error(
            "not_found",
            f"Profile '{name}' not found",
            output_json=False,
            hint=f"Available profiles: {', '.join(profiles)}",
            context="config",
        )
    content = path.read_text()
    if "[default]" in content:
        content = re.sub(r'(profile\s*=\s*)"[^"]*"', f'\\1"{name}"', content)
    else:
        content = f'[default]\nprofile = "{name}"\n\n' + content
    path.write_text(content)
    click.echo(f"Default profile set to '{name}'.")
