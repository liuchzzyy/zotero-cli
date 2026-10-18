"""`zot config` — inspect configuration, manage profiles, and the MinerU cache.

The command implementations live in sibling modules; this module owns the group
shell and the registration order, which is observable in `zot config --help`.
"""

from __future__ import annotations

import click

from zotero_cli.commands.system.config.cache import cache_group
from zotero_cli.commands.system.config.commands import config_init, config_show
from zotero_cli.commands.system.config.profiles import profile_group


@click.group("config", invoke_without_command=True)
@click.pass_context
def config_group(ctx: click.Context) -> None:
    """Manage zot configuration."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Registration order drives the Click tree; help grouping is handled by TieredGroup.
config_group.add_command(config_init)
config_group.add_command(config_show)
config_group.add_command(profile_group)
config_group.add_command(cache_group)

__all__ = ["config_group"]
