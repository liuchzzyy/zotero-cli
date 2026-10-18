"""Workspace command group and subcommand registration."""

from __future__ import annotations

import click


@click.group("workspace", invoke_without_command=True)
@click.pass_context
def workspace_group(ctx: click.Context) -> None:
    """Manage local workspaces for organizing papers by topic."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Import the command modules for their registration side effects; each command
# module imports workspace_group back from this package.
from zotero_cli.commands.workspace import commands, embedding, indexing, query  # noqa: E402, F401

__all__ = ["workspace_group"]
