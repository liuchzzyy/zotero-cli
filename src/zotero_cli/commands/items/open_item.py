"""zot open: open an item's PDF/URL or a web URL in the default application."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import click

from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, resolve_library_id
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import sanitize_text

_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_ANY_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DOI_RE = re.compile(r"^doi:", re.IGNORECASE)


def _open_path(path: str) -> None:
    """Open a file or URL with the system default handler.

    Uses one fixed, argument-only invocation per platform (no shell): on macOS
    "open", on Linux "xdg-open", and on Windows the native os.startfile API
    (which takes a filename, never a command string, so no shell quoting is
    involved).
    """
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows-only API
    else:
        subprocess.run(["xdg-open", path], check=True)


def _emit_and_open(target: str, *, kind: str, output_json: bool) -> None:
    """Report the target (envelope or human line) and launch the default app."""
    if output_json:
        click.echo(json.dumps(envelope_ok({"target": target, "kind": kind, "gui": True}), indent=2, ensure_ascii=False))
    else:
        click.echo(f"Opening {sanitize_text(target)}")
    _open_path(target)


@click.command("open")
@click.argument("key")
@click.option(
    "--url",
    "open_url",
    is_flag=True,
    help="Open the item URL (or a web URL passed as KEY) in the browser instead of the PDF",
)
@click.pass_context
def open_cmd(ctx: click.Context, key: str, open_url: bool) -> None:
    """Open the PDF or URL of a Zotero item in the default app.

    KEY may also be an http(s) URL, which is opened directly
    (e.g. zot open --url https://example.com). This command launches the system
    default application, i.e. it has a GUI side effect; JSON mode returns one
    envelope {target, kind, gui} describing what was opened.

    \b
    Examples:
      zot open ABC123                    Open PDF in default viewer
      zot open ABC123 --url              Open DOI/URL in browser
      zot open --url https://example.com Open a web URL directly
      zot --json open ABC123             Envelope + open (GUI side effect)
    """
    json_out = ctx.obj.get("json", False)

    # Direct web URL support: "zot open --url https://example.com" used to treat
    # the URL as an item key. Only http(s) is accepted here.
    if _HTTP_URL_RE.match(key):
        _emit_and_open(key, kind="url", output_json=json_out)
        return
    if _ANY_SCHEME_RE.match(key):
        emit_error(
            "validation_error",
            f"Unsupported URL scheme in '{key}'; only http(s) URLs are supported",
            output_json=json_out,
            hint="Use 'zot open <item-key>' for library items",
            context="open",
        )
    if _DOI_RE.match(key):
        _emit_and_open(f"https://doi.org/{key[4:]}", kind="url", output_json=json_out)
        return

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot search' to find valid item keys",
                context="open",
            )

        if open_url:
            target = item.url or item.doi
            if item.doi and not item.url:
                target = f"https://doi.org/{item.doi}"
            if not target:
                emit_error(
                    "not_found",
                    f"No URL or DOI for item '{key}'",
                    output_json=json_out,
                    context="open",
                )
            _emit_and_open(target, kind="url", output_json=json_out)
            return

        # Default: open PDF
        att = reader.get_pdf_attachment(key)
        if att is None:
            emit_error(
                "not_found",
                f"No PDF attachment for '{key}'",
                output_json=json_out,
                hint="Use --url to open the item URL instead",
                context="open",
            )
        pdf_path = att.path
        if not pdf_path or not pdf_path.exists():
            emit_error(
                "not_found",
                f"PDF file not found at {pdf_path or att.filename}",
                output_json=json_out,
                context="open",
            )
        _emit_and_open(str(pdf_path), kind="pdf", output_json=json_out)
    finally:
        reader.close()
