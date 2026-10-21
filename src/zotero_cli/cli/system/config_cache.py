"""`zot config cache` - inspect and clear the MinerU parse-package cache."""

from __future__ import annotations

import json

import typer

from zotero_cli.cli.system.config_commands import _json_out
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import sanitize_text

app = typer.Typer(help="管理 MinerU 解析缓存。", invoke_without_command=True, rich_markup_mode=None)


@app.callback()
def cache_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command("clear")
def cache_clear(
    ctx: typer.Context,
    yes: bool = typer.Option(False, "--yes", help="确认删除全部缓存。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览，不删除。"),
) -> None:
    """Clear cached MinerU parse packages (requires --yes; --dry-run previews)."""
    from zotero_cli.ai_pdf import MinerUParseCache

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
            typer.echo(json.dumps(envelope_ok(payload, dry_run=True), indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Would remove {entries} cached MinerU package(s) ({total_bytes:,} bytes).")
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
        typer.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    typer.echo(f"Cache cleared. Removed {removed} MinerU parse package(s).")


@app.command("stats")
def cache_stats(ctx: typer.Context) -> None:
    """Show MinerU parse cache statistics."""
    from zotero_cli.ai_pdf import MinerUParseCache

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
        typer.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    typer.echo(f"Cached PDFs: {payload['entries']}")
    typer.echo(f"Total bytes: {payload['total_bytes']:,}")


@app.command("list")
def cache_list(
    ctx: typer.Context,
    limit: int = typer.Option(100, "--limit", min=0, show_default=True, help="显示条数（0 表示全部）。"),
) -> None:
    """List cached MinerU parse packages (bounded by --limit; 0 shows all)."""
    from zotero_cli.ai_pdf import MinerUParseCache

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
        typer.echo(json.dumps(envelope_ok(visible, meta=meta), ensure_ascii=False, indent=2))
        return
    if not visible:
        typer.echo("Cache is empty.")
        return
    if len(visible) < total:
        typer.echo(f"Showing {len(visible)} of {total} cached package(s); use --limit N or --limit 0 to list all.")
    for entry in visible:
        # Cache metadata comes from local PDF filenames: sanitize before echoing.
        typer.echo(
            f"{sanitize_text(entry.get('source_name', '?'))}  "
            f"{sanitize_text(entry.get('source_sha256', ''))[:12]}  "
            f"{sanitize_text(entry.get('mineru_model_version', '?'))}  "
            f"{sanitize_text(entry.get('parsed_at', '?'))}"
        )
