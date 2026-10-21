"""zot zotero cite：格式化条目引文并复制到剪贴板。"""

from __future__ import annotations

import json
import subprocess
import sys

import typer

from zotero_cli.cli.renderers import sanitize_text
from zotero_cli.cli.zotero.reading import app
from zotero_cli.config import get_data_dir, load_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.models import Item
from zotero_cli.zotero.reader import ZoteroReader


def _copy_to_clipboard(text: str) -> bool:
    """将文本复制到系统剪贴板。成功时返回 True。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _get_year(item: Item) -> str:
    """从日期字符串中提取 4 位年份。"""
    if not item.date:
        return "n.d."
    for part in item.date.replace("/", "-").split("-"):
        if len(part) == 4 and part.isdigit():
            return part
    return item.date


def _get_authors_apa(item: Item) -> str:
    """按 APA 样式格式化作者。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        return ""
    if len(authors) == 1:
        a = authors[0]
        first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
        return f"{a.last_name}, {first_initials}" if first_initials else a.last_name
    if len(authors) == 2:
        parts = []
        for a in authors:
            first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
            parts.append(f"{a.last_name}, {first_initials}" if first_initials else a.last_name)
        return f"{parts[0]} & {parts[1]}"
    # APA 第 7 版：最多列出 20 位作者；超过 20 位时列出前 19 位和最后一位。
    parts = []
    display = authors if len(authors) <= 20 else authors[:19]
    for a in display:
        first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
        parts.append(f"{a.last_name}, {first_initials}" if first_initials else a.last_name)
    if len(authors) > 20:
        last = authors[-1]
        first_initials = ". ".join(n[0] for n in last.first_name.split() if n) + "." if last.first_name else ""
        last_str = f"{last.last_name}, {first_initials}" if first_initials else last.last_name
        return ", ".join(parts) + ", ... " + last_str
    return ", ".join(parts[:-1]) + ", & " + parts[-1]


def _get_authors_vancouver(item: Item) -> str:
    """按 Vancouver 样式格式化作者。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        return ""
    parts = []
    display = authors[:6]
    for a in display:
        initials = "".join(n[0].upper() for n in a.first_name.split() if n) if a.first_name else ""
        parts.append(f"{a.last_name} {initials}" if initials else a.last_name)
    result = ", ".join(parts)
    if len(authors) > 6:
        result += ", et al"
    return result


def _format_apa(item: Item) -> str:
    """按 APA 第 7 版样式格式化引文。"""
    authors = _get_authors_apa(item)
    year = _get_year(item)
    title = item.title or ""
    journal = item.extra.get("publicationTitle", "")
    volume = item.extra.get("volume", "")
    issue = item.extra.get("issue", "")
    pages = item.extra.get("pages", "")
    doi = item.doi

    parts = []
    if authors:
        parts.append(f"{authors} ({year}).")
    else:
        parts.append(f"({year}).")
    parts.append(f"{title}.")
    if journal:
        vol_str = f", {volume}" if volume else ""
        issue_str = f"({issue})" if issue else ""
        pages_str = f", {pages}" if pages else ""
        parts.append(f"{journal}{vol_str}{issue_str}{pages_str}.")
    if doi:
        parts.append(f"https://doi.org/{doi}")

    return " ".join(parts)


def _format_nature(item: Item) -> str:
    """按 Nature 样式格式化引文。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        author_str = ""
    else:
        parts = []
        for a in authors:
            initials = " ".join(f"{n[0]}." for n in a.first_name.split() if n) if a.first_name else ""
            parts.append(f"{a.last_name}, {initials}".strip(", ") if initials else a.last_name)
        if len(parts) > 5:
            author_str = ", ".join(parts[:5]) + " et al."
        else:
            author_str = " & ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]

    title = item.title or ""
    journal = item.extra.get("publicationTitle", item.extra.get("journalAbbreviation", ""))
    volume = item.extra.get("volume", "")
    pages = item.extra.get("pages", "")
    year = _get_year(item)
    doi = item.doi

    result = f"{author_str} {title}." if author_str else f"{title}."
    if journal:
        result += f" {journal}"
        if volume:
            result += f" **{volume}**"
        if pages:
            result += f", {pages}"
        result += f" ({year})."
    else:
        result += f" ({year})."
    if doi:
        result += f" https://doi.org/{doi}"

    return result


def _format_vancouver(item: Item) -> str:
    """按 Vancouver 样式格式化引文。"""
    authors = _get_authors_vancouver(item)
    title = item.title or ""
    journal = item.extra.get("journalAbbreviation", item.extra.get("publicationTitle", ""))
    year = _get_year(item)
    volume = item.extra.get("volume", "")
    issue = item.extra.get("issue", "")
    pages = item.extra.get("pages", "")
    doi = item.doi

    result = f"{authors}. {title}." if authors else f"{title}."
    if journal:
        result += f" {journal}. {year}"
        if volume:
            result += f";{volume}"
        if issue:
            result += f"({issue})"
        if pages:
            result += f":{pages}"
        result += "."
    else:
        result += f" {year}."
    if doi:
        result += f" doi:{doi}"

    return result


STYLES = {
    "apa": _format_apa,
    "nature": _format_nature,
    "vancouver": _format_vancouver,
}


@app.command(name="cite")
def cite(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    style: str = typer.Option("apa", help="引文样式。"),
    no_copy: bool = typer.Option(False, "--no-copy", help="仅打印，不复制到剪贴板。"),
) -> None:
    """格式化引文并复制到剪贴板。

    人类模式会打印引文并复制到系统剪贴板，除非传入 --no-copy。JSON 模式返回
    单个 envelope {citation, style, copied}，且绝不写入剪贴板（对代理程序调用方
    不产生隐藏副作用）。

    \b
    示例：
      zot zotero cite ABC123                    APA 样式（默认，会复制）
      zot zotero cite ABC123 --style nature     Nature 样式
      zot zotero cite ABC123 --style vancouver  Vancouver 样式
      zot zotero cite ABC123 --no-copy          仅打印，不复制
      zot --json zotero cite ABC123             仅输出 envelope，不写剪贴板
    """
    if style not in STYLES:
        raise typer.BadParameter("style 必须是 apa、nature 或 vancouver")
    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    json_out = ctx.obj.get("json", False)
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="cite",
            )
        formatter = STYLES[style]
        citation = formatter(item)
        if json_out:
            # 机器模式：只报告引文，不改动剪贴板。
            typer.echo(
                json.dumps(
                    envelope_ok({"citation": citation, "style": style, "copied": False}),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        typer.echo(sanitize_text(citation))
        if not no_copy:
            if _copy_to_clipboard(citation):
                typer.echo("(copied to clipboard)")
            else:
                typer.echo("(clipboard not available)")
    finally:
        reader.close()
