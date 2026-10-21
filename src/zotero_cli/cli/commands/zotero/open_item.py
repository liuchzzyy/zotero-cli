"""zot zotero open：用默认应用打开条目的 PDF/URL 或某个网页 URL。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import typer

from zotero_cli.cli.commands.zotero.reading import app
from zotero_cli.cli.output.envelope import envelope_ok
from zotero_cli.cli.output.exit_codes import emit_error
from zotero_cli.cli.output.sanitize import sanitize_text
from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, resolve_library_id
from zotero_cli.zotero.reader import ZoteroReader

_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_ANY_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_DOI_RE = re.compile(r"^doi:", re.IGNORECASE)


def _open_path(path: str) -> None:
    """用系统默认程序打开文件或 URL。

    每个平台只使用一条固定、纯参数的调用（不经 shell）：macOS 用 "open"，
    Linux 用 "xdg-open"，Windows 用原生 os.startfile API（其参数是文件名而非
    命令字符串，因此不涉及 shell 引号处理）。
    """
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows-only API
    else:
        subprocess.run(["xdg-open", path], check=True)


def _emit_and_open(target: str, *, kind: str, output_json: bool) -> None:
    """报告目标（envelope 或人类可读行）并启动默认应用。"""
    if output_json:
        typer.echo(json.dumps(envelope_ok({"target": target, "kind": kind, "gui": True}), indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Opening {sanitize_text(target)}")
    _open_path(target)


@app.command(name="open")
def open_item(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键或 http(s) URL。"),
    open_url: bool = typer.Option(False, "--url", help="在浏览器中打开条目或指定的 URL。"),
) -> None:
    """用默认应用打开 Zotero 条目的 PDF 或 URL。

    KEY 也可以是 http(s) URL，此时直接打开该地址
    （例如 zot zotero open --url https://example.com）。此命令会启动系统默认
    应用，即带有 GUI 副作用；JSON 模式返回单个 envelope {target, kind, gui}
    描述所打开的目标。

    \b
    示例：
      zot zotero open ABC123                    用默认阅读器打开 PDF
      zot zotero open ABC123 --url              在浏览器中打开 DOI/URL
      zot zotero open --url https://example.com 直接打开网页 URL
      zot --json zotero open ABC123             envelope + 打开（GUI 副作用）
    """
    json_out = ctx.obj.get("json", False)

    # 直接支持网页 URL：过去 "zot zotero open --url https://example.com" 会把
    # URL 当作条目键。此处仅接受 http(s)。
    if _HTTP_URL_RE.match(key):
        _emit_and_open(key, kind="url", output_json=json_out)
        return
    if _ANY_SCHEME_RE.match(key):
        emit_error(
            "validation_error",
            f"Unsupported URL scheme in '{key}'; only http(s) URLs are supported",
            output_json=json_out,
            hint="Use 'zot zotero open <item-key>' for library items",
            context="open",
        )
    if _DOI_RE.match(key):
        _emit_and_open(f"https://doi.org/{key[4:]}", kind="url", output_json=json_out)
        return

    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
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

        # 默认：打开 PDF
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
