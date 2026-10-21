"""人类可读与 JSON 载荷渲染器。

每个 format_* 辅助函数都会把同一份载荷渲染为 Rich 表格/树（TTY）或 JSON
envelope（管道或 --json）。print_error 按约定把诊断信息输出到所需通道。
envelope 的构造位于 zotero_cli.output.envelope。

人类可读输出把库数据视为不可信内容：剥离 ANSI 转义序列与控制字符，动态值
通过 Rich Text 对象渲染（禁用标记解析），因此精心构造的标题、笔记或 PDF 正文
无法注入终端转义序列或 Rich 标记。JSON 输出不受影响。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from io import StringIO
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from zotero_cli.output.envelope import envelope_error, envelope_ok
from zotero_cli.output.sanitize import sanitize_text
from zotero_cli.zotero.models import Collection, DuplicateGroup, Item, Note


@dataclass
class ErrorInfo:
    """人类可读错误展示所需的字段。"""

    message: str
    context: str = ""
    hint: str = ""
    code: str = "runtime_error"
    retryable: bool = False


def _text(value: object) -> Text:
    """把动态内容渲染为 Rich Text（绝不按控制台标记解析）。"""
    return Text(sanitize_text(value))


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def format_items(
    items: list[Item],
    output_json: bool = False,
    detail: str = "standard",
    meta: dict | None = None,
) -> str:
    if output_json:
        if detail == "minimal":
            minimal_keys = {"key", "item_type", "title", "creators", "date"}
            data = [{k: v for k, v in asdict(i).items() if k in minimal_keys} for i in items]
        else:
            data = [asdict(i) for i in items]
        return _dump(envelope_ok(data, meta={"count": len(items), **(meta or {})}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Key", style="cyan", width=10)
    table.add_column("Title", width=50)
    table.add_column("Authors", width=25)
    table.add_column("Year", width=6)
    table.add_column("Type", width=15)
    for item in items:
        authors = ", ".join(sanitize_text(c.full_name) for c in item.creators[:3])
        if len(item.creators) > 3:
            authors += " et al."
        table.add_row(_text(item.key), _text(item.title), Text(authors), _text(item.date or ""), _text(item.item_type))
    console.print(table)
    return buf.getvalue()


def format_item_detail(item: Item, notes: list[Note], output_json: bool = False, detail: str = "standard") -> str:
    if output_json:
        if detail == "minimal":
            minimal_keys = {"key", "item_type", "title", "creators", "date", "doi", "url"}
            data = {k: v for k, v in asdict(item).items() if k in minimal_keys}
        else:
            data = asdict(item)
            data["notes"] = [asdict(n) for n in notes]
        return _dump(envelope_ok(data))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    console.print(Text(sanitize_text(item.title), style="bold cyan"))
    console.print(
        Text(
            f"Key: {sanitize_text(item.key)}  |  Type: {sanitize_text(item.item_type)}  |  Date: {sanitize_text(item.date or 'N/A')}"
        )
    )
    console.print(Text(f"Authors: {', '.join(sanitize_text(c.full_name) for c in item.creators)}"))
    if item.doi:
        console.print(Text(f"DOI: {sanitize_text(item.doi)}"))
    if item.url:
        console.print(Text(f"URL: {sanitize_text(item.url)}"))
    if detail != "minimal":
        if item.tags:
            console.print(Text(f"Tags: {', '.join(sanitize_text(t) for t in item.tags)}"))
        if detail == "full" and item.extra:
            display_keys = [
                ("publicationTitle", "Journal"),
                ("journalAbbreviation", "Journal Abbr"),
                ("volume", "Volume"),
                ("issue", "Issue"),
                ("pages", "Pages"),
                ("ISSN", "ISSN"),
                ("publisher", "Publisher"),
                ("language", "Language"),
                ("citationKey", "Citation Key"),
            ]
            shown = []
            for ekey, label in display_keys:
                val = item.extra.get(ekey)
                if val:
                    shown.append(f"  {label}: {sanitize_text(val)}")
            if shown:
                console.print("\n[bold]Metadata:[/bold]")
                for line in shown:
                    console.print(Text(line))
            skip = {k for k, _ in display_keys} | {
                "libraryCatalog",
                "accessDate",
                "rights",
            }
            remaining = {k: v for k, v in item.extra.items() if k not in skip and v}
            if remaining:
                for rk, rv in remaining.items():
                    console.print(Text(f"  {sanitize_text(rk)}: {sanitize_text(rv)}"))
        if item.abstract:
            console.print("\n[bold]Abstract:[/bold]")
            console.print(Text(sanitize_text(item.abstract)))
        if notes:
            console.print(f"\n[bold]Notes ({len(notes)}):[/bold]")
            for n in notes:
                console.print(Text(f"  [{sanitize_text(n.key)}] {sanitize_text(n.content)[:200]}"))
    return buf.getvalue()


def format_collections(collections: list[Collection], output_json: bool = False) -> str:
    if output_json:
        data = [_collection_to_dict(c) for c in collections]
        return _dump(envelope_ok(data, meta={"count": len(collections)}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    tree = Tree("[bold]Collections[/bold]")
    for c in collections:
        _add_collection_to_tree(tree, c)
    console.print(tree)
    return buf.getvalue()


def format_notes(notes: list[Note], output_json: bool = False) -> str:
    if output_json:
        data = [asdict(n) for n in notes]
        return _dump(envelope_ok(data, meta={"count": len(notes)}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    for n in notes:
        header = Text()
        header.append(f"[{sanitize_text(n.key)}]", style="bold cyan")
        console.print(header)
        console.print(Text(sanitize_text(n.content)))
        console.print()
    return buf.getvalue()


def format_duplicates(groups: list[DuplicateGroup], output_json: bool = False) -> str:
    if output_json:
        data = []
        for i, g in enumerate(groups, 1):
            data.append(
                {
                    "group": i,
                    "match_type": g.match_type,
                    "score": g.score,
                    "items": [asdict(item) for item in g.items],
                }
            )
        return _dump(envelope_ok(data, meta={"count": len(groups)}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Group", width=6)
    table.add_column("Keys", width=20)
    table.add_column("Title", width=50)
    table.add_column("Match", width=8)
    table.add_column("Score", width=6)
    for i, g in enumerate(groups, 1):
        keys = ", ".join(sanitize_text(item.key) for item in g.items)
        title = sanitize_text(g.items[0].title) if g.items else ""
        table.add_row(str(i), Text(keys), Text(title), _text(g.match_type), f"{g.score:.2f}")
    console.print(table)
    return buf.getvalue()


def format_pdf_text(
    key: str,
    *,
    outline: list[dict] | None = None,
    section: int | None = None,
    content: str | None = None,
    output_json: bool = False,
) -> str:
    if output_json:
        data: dict[str, Any] = {"key": key}
        if section is not None:
            data["section"] = section
            data["content"] = content or ""
        elif outline is not None:
            data["outline"] = outline
        return _dump(envelope_ok(data))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    if section is not None:
        console.print(Text(sanitize_text(content or "")))
    elif outline is not None:
        for item in outline:
            indent = "  " * (int(item.get("level", 1)) - 1)
            console.print(
                Text(f"{sanitize_text(item.get('number', ''))}. {indent}{sanitize_text(item.get('text', ''))}")
            )
    return buf.getvalue()


def format_workspace_list(workspaces: list, output_json: bool = False) -> str:
    if output_json:
        data = [
            {
                "name": ws.name,
                "description": ws.description,
                "items": len(ws.items),
                "created": ws.created,
            }
            for ws in workspaces
        ]
        return _dump(envelope_ok(data, meta={"count": len(workspaces)}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan", width=20)
    table.add_column("Description", width=50)
    table.add_column("Items", justify="right", width=8)
    table.add_column("Created", width=20)
    for ws in workspaces:
        desc = sanitize_text(ws.description)
        desc = desc[:47] + "..." if len(desc) > 50 else desc
        created = sanitize_text(ws.created)
        created = created[:10] if len(created) >= 10 else created
        table.add_row(_text(ws.name), Text(desc), str(len(ws.items)), Text(created))
    console.print(table)
    return buf.getvalue()


def format_workspace_query(results: list, mode: str, output_json: bool = False) -> str:
    if output_json:
        data = {
            "mode": mode,
            "results": [
                {
                    "rank": i + 1,
                    # BM25 分数常常约为 1e-6；需保留足够精度使其仍可区分，
                    # 而不是把命中全部压成 0.0。
                    "score": round(float(r[1]), 10),
                    "item_key": r[2]["item_key"],
                    "source": r[2]["source"],
                    "content": r[2]["content"][:500],
                }
                for i, r in enumerate(results)
            ],
        }
        return _dump(envelope_ok(data))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    for i, (_cid, score, chunk) in enumerate(results):
        preview = sanitize_text(chunk["content"][:120].replace("\n", " "))
        console.print(
            Text(
                f"[{i + 1}] Score: {float(score):.3g} | "
                f"{sanitize_text(chunk['item_key'])} | {sanitize_text(chunk['source'])}"
            )
        )
        console.print(Text(f"    {preview}..."))
    return buf.getvalue()


def format_error(error: str | ErrorInfo, output_json: bool = False) -> str:
    if isinstance(error, str):
        error = ErrorInfo(message=error)
    if output_json:
        env = envelope_error(
            code=error.code or "runtime_error",
            message=error.message,
            retryable=error.retryable,
            hint=error.hint,
            context=error.context,
        )
        return _dump(env)
    lines = [f"Error: {sanitize_text(error.message)}"]
    if error.hint:
        lines.append(f"Hint: {sanitize_text(error.hint)}")
    return "\n".join(lines)


def print_error(error: str | ErrorInfo, output_json: bool = False) -> None:
    """把结构化错误输出到正确的通道。

    JSON 模式：envelope 写入 stdout（供代理程序解析的结果）。
    文本模式：人类可读行写入 stderr（面向人的诊断信息）。

    不会退出。若需要退出，请使用 zotero_cli.output.exit_codes.emit_error。
    """
    import sys as _sys

    import click

    rendered = format_error(error, output_json=output_json)
    if output_json:
        click.echo(rendered)
    else:
        click.echo(rendered, err=True)
    _sys.stdout.flush() if output_json else _sys.stderr.flush()


def format_success(data: Any, output_json: bool = False, human_text: str = "", meta: dict | None = None) -> str:
    """格式化成功结果。JSON 模式输出 envelope；文本模式输出 human_text。"""
    if output_json:
        return _dump(envelope_ok(data, meta=meta))
    return human_text


def _collection_to_dict(c: Collection) -> dict:
    return {
        "key": c.key,
        "name": c.name,
        "parent_key": c.parent_key,
        "children": [_collection_to_dict(ch) for ch in c.children],
    }


def _collection_label(c: Collection) -> Text:
    label = Text()
    label.append(f"[{sanitize_text(c.name)}]", style="cyan")
    label.append(f" ({sanitize_text(c.key)})")
    return label


def _add_collection_to_tree(parent: Tree, c: Collection) -> None:
    node = parent.add(_collection_label(c))
    for ch in c.children:
        _add_collection_to_tree(node, ch)
