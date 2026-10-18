"""Collection command group and subcommand registration."""

from __future__ import annotations

import click


@click.group("collection", invoke_without_command=True)
@click.pass_context
def collection_group(ctx: click.Context) -> None:
    """Manage Zotero collections."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Import the command modules for their registration side effects; each command
# module imports collection_group back from this package.
from zotero_cli.commands.collections import commands, reorganize  # noqa: E402, F401

__all__ = ["collection_group"]
