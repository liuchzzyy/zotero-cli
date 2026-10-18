"""A Click Group that renders the command list grouped by safety tier.

zot --help lists read commands first, then write and destructive commands under
explicit "MUTATES LIBRARY" headers, so an agent scanning help cannot mistake a
library-mutating command for a harmless one. Tier membership comes from
zotero_cli.cli.safety.

The group also owns the CLI's top-level error boundary: uncaught exceptions and
Click usage errors are translated into the documented JSON envelope / human
error format and typed exit codes instead of leaking tracebacks.
"""

from __future__ import annotations

import errno
import sys
from typing import Any

import click

from zotero_cli.cli.global_flags import _hoist_global_flags, detect_json_output
from zotero_cli.cli.safety import DESTRUCTIVE_COMMANDS, READ_COMMANDS, WRITE_COMMANDS
from zotero_cli.output.exit_codes import (
    EXIT_RUNTIME,
    EXIT_VALIDATION,
    emit_error,
    emit_exception,
    emit_usage_error,
)


class TieredGroup(click.Group):
    """A Click Group that renders the command list grouped by safety tier."""

    def main(  # type: ignore[override]
        self,
        args: list[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        if args is None:
            args = sys.argv[1:]
        args = _hoist_global_flags(list(args), self)

        if not standalone_mode:
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )

        json_output = detect_json_output(args)
        verbose = "--verbose" in args
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except SystemExit:
            # Explicit exits (help/version/completion, ctx.exit(n)) keep their code.
            raise
        except click.ClickException as exc:
            if isinstance(exc, click.UsageError):
                if json_output:
                    emit_usage_error(exc, output_json=True)
                exc.show()
                raise SystemExit(EXIT_VALIDATION) from None
            if json_output:
                emit_error(
                    "runtime_error",
                    exc.format_message(),
                    output_json=True,
                    hint="Run 'zot --help' for usage",
                    context="cli",
                )
            exc.show()
            raise SystemExit(exc.exit_code) from None
        except click.Abort:
            click.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from None
        except (EOFError, KeyboardInterrupt) as exc:
            click.echo(file=sys.stderr)
            click.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EPIPE:
                raise SystemExit(EXIT_RUNTIME) from None
            emit_exception(exc, output_json=json_output, verbose=verbose)
        except BaseException as exc:  # noqa: BLE001 - the boundary is the point
            emit_exception(exc, output_json=json_output, verbose=verbose)

        if isinstance(result, int):
            raise SystemExit(result)
        return result

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
