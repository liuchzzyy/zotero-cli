"""The `zot` Click root group: global options, request scope, update notice.

The console-script entry point is `zotero_cli.cli:main`; `main` is the group
defined here after the command registry has populated it.
"""

from __future__ import annotations

import os
import sys

import click

from zotero_cli import __version__
from zotero_cli.cli.registry import register_commands
from zotero_cli.cli.tiered_group import TieredGroup
from zotero_cli.output.envelope import request_scope


@click.group(cls=TieredGroup, invoke_without_command=True, no_args_is_help=False)
@click.version_option(version=__version__, prog_name="zot")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=None,
    help="Output as JSON (auto-enabled when stdout is not a TTY)",
)
@click.option(
    "--no-json",
    "output_no_json",
    is_flag=True,
    default=False,
    help="Force human-readable output even when stdout is not a TTY",
)
@click.option("--limit", default=50, help="Limit results")
@click.option(
    "--detail", type=click.Choice(["minimal", "standard", "full"]), default="standard", help="Output detail level"
)
@click.option("--no-interaction", is_flag=True, help="Suppress interactive prompts for automation")
@click.option("--verbose", is_flag=True, help="Verbose output")
@click.option("--profile", default=None, help="Config profile name")
@click.option("--library", default="user", help="Library: 'user' (default) or 'group:<id>'")
@click.pass_context
def main(
    ctx: click.Context,
    output_json: bool | None,
    output_no_json: bool,
    limit: int,
    detail: str,
    no_interaction: bool,
    verbose: bool,
    profile: str | None,
    library: str,
) -> None:
    """zot — Zotero CLI for AI agents.

    
    Quick start:
      zot search "attention mechanism"    Search papers
      zot read ABC123                     View paper details
      zot --json search "BERT"            JSON output for AI
    """
    # Bind a request_id + start time for the duration of this invocation so
    # every envelope (success, error, partial) carries them automatically.
    scope = request_scope()
    rid = scope.__enter__()
    ctx.call_on_close(lambda: scope.__exit__(None, None, None))
    ctx.ensure_object(dict)
    ctx.obj["request_id"] = rid
    # Mutual exclusion check: --json and --no-json cannot be used together
    if output_json is not None and output_no_json:
        raise click.BadParameter("Cannot use both --json and --no-json")
    # TTY auto-detect: when stdout is redirected/piped, default to JSON so agents
    # never have to remember --json. Explicit --json, --no-json, or ZOT_FORMAT env var override.
    if output_json is None:
        env_fmt = os.environ.get("ZOT_FORMAT", "").lower()
        if env_fmt == "json":
            output_json = True
        elif env_fmt in ("table", "text"):
            output_json = False
        else:
            output_json = not sys.stdout.isatty()
    # --no-json forces human-readable output regardless of auto-detect
    if output_no_json:
        output_json = False
    ctx.obj["json"] = output_json
    ctx.obj["limit"] = limit
    ctx.obj["detail"] = detail
    ctx.obj["no_interaction"] = no_interaction
    ctx.obj["verbose"] = verbose
    ctx.obj["profile"] = profile or os.environ.get("ZOT_PROFILE")

    # Parse --library option
    if library == "user":
        ctx.obj["library_type"] = "user"
        ctx.obj["group_id"] = None
    elif library.startswith("group:"):
        group_part = library[6:]
        if not group_part.isdigit():
            raise click.BadParameter(f"Invalid --library format: '{library}'. Use 'user' or 'group:<id>'")
        ctx.obj["library_type"] = "group"
        ctx.obj["group_id"] = group_part
    else:
        raise click.BadParameter(f"Invalid --library format: '{library}'. Use 'user' or 'group:<id>'")

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.result_callback()
@click.pass_context
def _after_command(ctx: click.Context, *_args: object, **_kwargs: object) -> None:
    """Show update notice after command completes (interactive mode only)."""
    obj = ctx.obj or {}
    if obj.get("json") or obj.get("no_interaction") or not sys.stderr.isatty():
        return
    from zotero_cli.core.version_check import check_for_update, update_check_disabled, upgrade_command

    if update_check_disabled():
        return
    latest = check_for_update(__version__)
    if latest:
        click.echo(
            click.style(
                f"\n Update available: v{__version__} → v{latest}. Run: {upgrade_command()}",
                fg="yellow",
            ),
            err=True,
        )


register_commands(main)
