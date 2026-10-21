"""Shared envelope, dry-run, and workspace-loading helpers for workspace commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.workspace.store import Workspace, load_workspace, workspace_dir, workspace_exists

_LIST_HINT = "Use 'zot workspace list' to see available workspaces"


def output_json(ctx: click.Context) -> bool:
    return bool(ctx.obj.get("json", False))


def emit_result(
    ctx: click.Context,
    data: Any,
    *,
    human_text: str,
    meta: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> None:
    """Emit exactly one output document: a JSON envelope, or human-readable text."""
    if output_json(ctx):
        click.echo(json.dumps(envelope_ok(data, meta=meta, dry_run=dry_run), indent=2, ensure_ascii=False))
    else:
        click.echo(human_text)


def emit_warning(message: str) -> None:
    """Warnings always go to stderr so JSON stdout stays a single envelope."""
    click.echo(f"Warning: {message}", err=True)


def emit_progress(ctx: click.Context, message: str) -> None:
    """Progress is written to stderr in JSON mode so stdout stays a single envelope."""
    if output_json(ctx):
        click.echo(message, err=True)
    else:
        click.echo(message)


def workspace_directory_or_exit(ctx: click.Context, name: str, *, context: str, hint: str = "") -> Path:
    """Resolve a workspace directory, emitting typed errors for bad or missing names."""
    json_out = output_json(ctx)
    try:
        directory = workspace_dir(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context=context)
    except OSError as exc:
        emit_error(
            "validation_error",
            f"Invalid workspace name: {exc}",
            output_json=json_out,
            hint="Workspace names must be a single directory component under .workspace/",
            context=context,
        )
    if not directory.exists():
        emit_error("not_found", f"Workspace '{name}' not found", output_json=json_out, hint=_LIST_HINT, context=context)
    return directory


def load_workspace_or_exit(ctx: click.Context, name: str, *, context: str, hint: str = "") -> Workspace:
    """Load a workspace, converting invalid/missing/corrupt manifests to typed errors."""
    json_out = output_json(ctx)
    workspace_directory_or_exit(ctx, name, context=context, hint=hint or _LIST_HINT)
    try:
        return load_workspace(name)
    except ValueError as exc:
        emit_error(
            "configuration_error",
            f"Workspace manifest is invalid: {exc}",
            output_json=json_out,
            hint=f"Delete it with 'zot workspace delete {name} --yes' and recreate it",
            context=context,
        )
    except OSError as exc:
        emit_error(
            "configuration_error",
            f"Cannot read workspace manifest: {exc}",
            output_json=json_out,
            context=context,
        )


def load_workspace_if_readable(name: str) -> Workspace | None:
    """Best-effort manifest load used by delete previews and corrupt-workspace cleanup."""
    try:
        return load_workspace(name)
    except (OSError, ValueError):
        return None


def workspace_exists_or_error(ctx: click.Context, name: str, *, context: str, hint: str = "") -> bool:
    json_out = output_json(ctx)
    try:
        return workspace_exists(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context=context)
    except OSError as exc:
        emit_error(
            "validation_error",
            f"Invalid workspace name: {exc}",
            output_json=json_out,
            hint="Workspace names must be a single directory component under .workspace/",
            context=context,
        )
