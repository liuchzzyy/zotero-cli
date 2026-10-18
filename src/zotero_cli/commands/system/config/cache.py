"""`zot config cache` — inspect and clear the MinerU parse-package cache."""

from __future__ import annotations

import json

import click

from zotero_cli.output.envelope import envelope_ok


@click.group("cache", invoke_without_command=True)
@click.pass_context
def cache_group(ctx: click.Context) -> None:
    """Manage canonical MinerU parse packages."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cache_group.command("clear")
def cache_clear() -> None:
    """Clear cached MinerU parse packages."""
    from zotero_cli.core.mineru import MinerUParseCache

    try:
        removed = MinerUParseCache().clear()
    except OSError as exc:
        raise click.ClickException(f"Could not clear MinerU cache: {exc}") from exc
    click.echo(f"Cache cleared. Removed {removed} MinerU parse package(s).")


@cache_group.command("stats")
def cache_stats() -> None:
    """Show MinerU parse cache statistics."""
    from zotero_cli.core.mineru import MinerUParseCache

    try:
        stats = MinerUParseCache().stats()
    except OSError as exc:
        raise click.ClickException(f"Could not inspect MinerU cache: {exc}") from exc
    click.echo(f"Cached PDFs: {stats['entries']}")
    click.echo(f"Total bytes: {stats['total_bytes']:,}")


@cache_group.command("list")
@click.pass_context
def cache_list(ctx: click.Context) -> None:
    """List cached MinerU parse packages."""
    from zotero_cli.core.mineru import MinerUParseCache

    json_out = ctx.obj.get("json", False) if ctx.obj else False
    try:
        entries = MinerUParseCache().entries()
    except OSError as exc:
        raise click.ClickException(f"Could not inspect MinerU cache: {exc}") from exc
    if json_out:
        click.echo(json.dumps(envelope_ok(entries, meta={"count": len(entries)}), ensure_ascii=False, indent=2))
        return
    if not entries:
        click.echo("Cache is empty.")
        return
    for entry in entries:
        click.echo(
            f"{entry.get('source_name', '?')}  {entry.get('source_sha256', '')[:12]}  "
            f"{entry.get('mineru_model_version', '?')}  {entry.get('parsed_at', '?')}"
        )
