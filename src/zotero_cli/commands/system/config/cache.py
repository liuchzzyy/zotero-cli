"""`zot config cache` - inspect and clear the MinerU parse-package cache."""

from __future__ import annotations

import json

import click

from zotero_cli.commands.system.config.commands import _json_out
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error


@click.group("cache", invoke_without_command=True)
@click.pass_context
def cache_group(ctx: click.Context) -> None:
    """Manage canonical MinerU parse packages."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cache_group.command("clear")
@click.option("--yes", is_flag=True, help="Confirm deleting every cached MinerU parse package")
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting")
@click.pass_context
def cache_clear(ctx: click.Context, yes: bool, dry_run: bool) -> None:
    """Clear cached MinerU parse packages (requires --yes; --dry-run previews)."""
    from zotero_cli.core.mineru import MinerUParseCache

    json_out = _json_out(ctx)
    try:
        cache = MinerUParseCache()
        stats = cache.stats()
    except OSError as exc:
        emit_error(
            "internal_error",
            f"Could not inspect MinerU cache: {exc}",
            output_json=json_out,
            context="config",
        )
    entries = int(stats["entries"])
    total_bytes = int(stats["total_bytes"])

    if dry_run:
        payload = {"entries": entries, "total_bytes": total_bytes, "would_remove": entries}
        if json_out:
            click.echo(json.dumps(envelope_ok(payload, extra={"dry_run": True}), indent=2, ensure_ascii=False))
        else:
            click.echo(f"Would remove {entries} cached MinerU package(s) ({total_bytes:,} bytes).")
        return

    if not yes:
        emit_error(
            "confirmation_required",
            f"Refusing to clear {entries} cached MinerU package(s) without --yes",
            output_json=json_out,
            hint="Re-run with --yes to delete them, or use --dry-run to preview.",
            context="config",
        )

    try:
        removed = cache.clear()
    except OSError as exc:
        emit_error(
            "internal_error",
            f"Could not clear MinerU cache: {exc}",
            output_json=json_out,
            context="config",
        )
    payload = {"entries": entries, "total_bytes": total_bytes, "would_remove": entries, "removed": removed}
    if json_out:
        click.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    click.echo(f"Cache cleared. Removed {removed} MinerU parse package(s).")


@cache_group.command("stats")
@click.pass_context
def cache_stats(ctx: click.Context) -> None:
    """Show MinerU parse cache statistics."""
    from zotero_cli.core.mineru import MinerUParseCache

    json_out = _json_out(ctx)
    try:
        stats = MinerUParseCache().stats()
    except OSError as exc:
        emit_error(
            "internal_error",
            f"Could not inspect MinerU cache: {exc}",
            output_json=json_out,
            context="config",
        )
    payload = {"entries": int(stats["entries"]), "total_bytes": int(stats["total_bytes"])}
    if json_out:
        click.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    click.echo(f"Cached PDFs: {payload['entries']}")
    click.echo(f"Total bytes: {payload['total_bytes']:,}")


@cache_group.command("list")
@click.pass_context
def cache_list(ctx: click.Context) -> None:
    """List cached MinerU parse packages."""
    from zotero_cli.core.mineru import MinerUParseCache

    json_out = _json_out(ctx)
    try:
        entries = MinerUParseCache().entries()
    except OSError as exc:
        emit_error(
            "internal_error",
            f"Could not inspect MinerU cache: {exc}",
            output_json=json_out,
            context="config",
        )
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
