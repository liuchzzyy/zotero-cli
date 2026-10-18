"""`zot config cache` - inspect and clear the MinerU parse-package cache."""

from __future__ import annotations

import json

import click

from zotero_cli.commands.system.config.commands import _json_out
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import sanitize_text


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
            click.echo(json.dumps(envelope_ok(payload, dry_run=True), indent=2, ensure_ascii=False))
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
    payload = {"entries": entries, "total_bytes": total_bytes, "removed": removed}
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
@click.option("--limit", type=click.IntRange(min=0), default=100, show_default=True, help="Entries to show (0 = all)")
@click.pass_context
def cache_list(ctx: click.Context, limit: int) -> None:
    """List cached MinerU parse packages (bounded by --limit; 0 shows all)."""
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
    total = len(entries)
    visible = entries if limit == 0 else entries[:limit]
    if json_out:
        meta = {"count": len(visible), "total": total, "limit": limit}
        click.echo(json.dumps(envelope_ok(visible, meta=meta), ensure_ascii=False, indent=2))
        return
    if not visible:
        click.echo("Cache is empty.")
        return
    if len(visible) < total:
        click.echo(f"Showing {len(visible)} of {total} cached package(s); use --limit N or --limit 0 to list all.")
    for entry in visible:
        # Cache metadata comes from local PDF filenames: sanitize before echoing.
        click.echo(
            f"{sanitize_text(entry.get('source_name', '?'))}  "
            f"{sanitize_text(entry.get('source_sha256', ''))[:12]}  "
            f"{sanitize_text(entry.get('mineru_model_version', '?'))}  "
            f"{sanitize_text(entry.get('parsed_at', '?'))}"
        )
