"""A Click Group that renders the command list grouped by safety tier.

`zot --help` lists read commands first, then write and destructive commands under
explicit "MUTATES LIBRARY" headers, so an agent scanning help cannot mistake a
library-mutating command for a harmless one. Tier membership comes from
`zotero_cli.cli.safety`.
"""

from __future__ import annotations

import sys
from typing import Any

import click

from zotero_cli.cli.global_flags import _hoist_global_flags
from zotero_cli.cli.safety import DESTRUCTIVE_COMMANDS, READ_COMMANDS, WRITE_COMMANDS


class TieredGroup(click.Group):
    """A Click Group that renders the command list grouped by safety tier."""

    def main(  # type: ignore[override]
        self,
        args: list[str] | None = None,
        **kwargs: Any,
    ) -> Any:
        if args is None:
            args = sys.argv[1:]
        args = _hoist_global_flags(list(args), self)
        return super().main(args=args, **kwargs)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands: list[tuple[str, click.Command]] = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or getattr(cmd, "hidden", False):
                continue
            commands.append((name, cmd))

        def rows_for(names: frozenset[str]) -> list[tuple[str, str]]:
            rows = []
            for name, cmd in commands:
                if name in names:
                    rows.append((name, cmd.get_short_help_str(limit=70)))
            return rows

        sections = [
            ("Read commands", rows_for(READ_COMMANDS)),
            ("Write commands (MUTATES LIBRARY)", rows_for(WRITE_COMMANDS)),
            ("Destructive commands (MUTATES LIBRARY)", rows_for(DESTRUCTIVE_COMMANDS)),
        ]
        other = rows_for(frozenset(n for n, _ in commands) - READ_COMMANDS - WRITE_COMMANDS - DESTRUCTIVE_COMMANDS)
        if other:
            sections.append(("Other", other))

        for title, rows in sections:
            if not rows:
                continue
            with formatter.section(title):
                formatter.write_dl(rows)
