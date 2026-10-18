"""Ordered registration of the `zot` command tree.

Registration order is observable: it drives the order of `zot --help`, and the
command list is part of the agent contract. Keep this list in the established
order when adding commands, and classify every new command in
`zotero_cli.cli.safety`.
"""

from __future__ import annotations

import click

from zotero_cli.commands.attachments.attach import attach_cmd
from zotero_cli.commands.attachments.pdf import pdf_cmd
from zotero_cli.commands.collections import collection_group
from zotero_cli.commands.items.add import add_cmd
from zotero_cli.commands.items.cite import cite_cmd
from zotero_cli.commands.items.delete import delete_cmd
from zotero_cli.commands.items.duplicates import duplicates_cmd
from zotero_cli.commands.items.export import export_cmd
from zotero_cli.commands.items.listing import list_cmd
from zotero_cli.commands.items.open_item import open_cmd
from zotero_cli.commands.items.read import read_cmd
from zotero_cli.commands.items.recent import recent_cmd
from zotero_cli.commands.items.relate import relate_cmd
from zotero_cli.commands.items.search import search_cmd
from zotero_cli.commands.items.stats import stats_cmd
from zotero_cli.commands.items.summarize import summarize_cmd
from zotero_cli.commands.items.summarize_all import summarize_all_cmd
from zotero_cli.commands.items.tag import tag_cmd
from zotero_cli.commands.items.trash import trash_group
from zotero_cli.commands.items.update import update_cmd
from zotero_cli.commands.items.update_status import update_status_cmd
from zotero_cli.commands.notes.ai_analyze import ai_analyze_cmd
from zotero_cli.commands.notes.note import note_cmd
from zotero_cli.commands.system.completions import completions_cmd
from zotero_cli.commands.system.config import config_group
from zotero_cli.commands.system.schema import schema_cmd
from zotero_cli.commands.workspace import workspace_group

# (command, name) in registration order. This is the observable command order.
COMMANDS: list[tuple[click.Command, str]] = [
    (config_group, "config"),
    (search_cmd, "search"),
    (list_cmd, "list"),
    (read_cmd, "read"),
    (export_cmd, "export"),
    (note_cmd, "note"),
    (add_cmd, "add"),
    (delete_cmd, "delete"),
    (tag_cmd, "tag"),
    (collection_group, "collection"),
    (summarize_cmd, "summarize"),
    (summarize_all_cmd, "summarize-all"),
    (pdf_cmd, "pdf"),
    (relate_cmd, "relate"),
    (stats_cmd, "stats"),
    (open_cmd, "open"),
    (cite_cmd, "cite"),
    (completions_cmd, "completions"),
    (recent_cmd, "recent"),
    (update_cmd, "update"),
    (trash_group, "trash"),
    (duplicates_cmd, "duplicates"),
    (attach_cmd, "attach"),
    (ai_analyze_cmd, "ai_analyze"),
    (update_status_cmd, "update-status"),
    (workspace_group, "workspace"),
    (schema_cmd, "schema"),
]


def register_commands(group: click.Group) -> None:
    """Attach every command to `group` in the canonical order."""
    for command, name in COMMANDS:
        group.add_command(command, name)
