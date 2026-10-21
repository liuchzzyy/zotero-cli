"""Ordered registration of the `zot` command tree.

Registration order is stable across releases but is NOT what `zot --help`
renders: Click sorts command names alphabetically within each help tier. Keep
this list in the established order when adding commands (it preserves the
historical command set for tooling that reads the tree), and classify every new
command in `zotero_cli.cli.safety`.
"""

from __future__ import annotations

import click

from zotero_cli.commands.attachments.pdf import pdf_cmd
from zotero_cli.commands.items.summarize import summarize_cmd
from zotero_cli.commands.items.summarize_all import summarize_all_cmd
from zotero_cli.commands.items.update_status import update_status_cmd
from zotero_cli.commands.notes.ai_analyze import ai_analyze_cmd
from zotero_cli.commands.system.completions import completions_cmd
from zotero_cli.commands.system.config import config_group
from zotero_cli.commands.system.schema import schema_cmd
from zotero_cli.commands.workspace import workspace_group

# (command, name) in registration order. This is the observable command order.
COMMANDS: list[tuple[click.Command, str]] = [
    (config_group, "config"),
    (summarize_cmd, "summarize"),
    (summarize_all_cmd, "summarize-all"),
    (pdf_cmd, "pdf"),
    (completions_cmd, "completions"),
    (ai_analyze_cmd, "ai_analyze"),
    (update_status_cmd, "update-status"),
    (workspace_group, "workspace"),
    (schema_cmd, "schema"),
]


def register_commands(group: click.Group) -> None:
    """Attach every command to `group` in the canonical order."""
    for command, name in COMMANDS:
        group.add_command(command, name)
