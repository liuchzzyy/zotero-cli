"""Ordered registration of the `zot` command tree.

Registration order is stable across releases but is NOT what `zot --help`
renders: Click sorts command names alphabetically within each help tier. Keep
this list in the established order when adding commands (it preserves the
historical command set for tooling that reads the tree), and classify every new
command in `zotero_cli.cli.safety`.
"""

from __future__ import annotations

import click

from zotero_cli.commands.system.completions import completions_cmd
from zotero_cli.commands.system.config import config_group
from zotero_cli.commands.system.schema import schema_cmd

# (command, name) in registration order. This is the observable command order.
COMMANDS: list[tuple[click.Command, str]] = [
    (config_group, "config"),
    (completions_cmd, "completions"),
    (schema_cmd, "schema"),
]


def register_commands(group: click.Group) -> None:
    """Attach every command to `group` in the canonical order."""
    for command, name in COMMANDS:
        group.add_command(command, name)
