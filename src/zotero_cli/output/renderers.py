"""Human-readable and JSON payload renderers.

Each `format_*` helper renders one payload either as a Rich table/tree (TTY) or
as a JSON envelope (piped or `--json`). `print_error` routes diagnostics to the
channel the contract requires. Envelope construction lives in
`zotero_cli.output.envelope`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from io import StringIO
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from zotero_cli.models import Collection, DuplicateGroup, ErrorInfo, Item, Note
from zotero_cli.output.envelope import envelope_error, envelope_ok


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def format_items(items: list[Item], output_json: bool = False, detail: str = "standard") -> str:
    if output_json:
        if detail == "minimal":
            minimal_keys = {"key", "item_type", "title", "creators", "date"}
            data = [{k: v for k, v in asdict(i).items() if k in minimal_keys} for i in items]
        else:
            data = [asdict(i) for i in items]
        return _dump(envelope_ok(data, meta={"count": len(items)}))
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    table = Table(show_header=True, header_style="bold")
    table.add_column("Key", style="cyan", width=10)
    table.add_column("Title", width=50)
    table.add_column("Authors", width=25)
    table.add_column("Year", width=6)
    table.add_column("Type", width=15)
    for item in items:
        authors = ", ".join(c.full_name for c in item.creators[:3])
        if len(item.creators) > 3:
            authors += " et al."
        table.add_row(Text(item.key), Text(item.title), Text(authors), Text(item.date or ""), Text(item.item_type))
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
    console.print(Text(item.title, style="bold cyan"))
    console.print(Text(f"Key: {item.key}  |  Type: {item.item_type}  |  Date: {item.date or 'N/A'}"))
    console.print(Text(f"Authors: {', '.join(c.full_name for c in item.creators)}"))
    if item.doi:
        console.print(Text(f"DOI: {item.doi}"))
    if item.url:
        console.print(Text(f"URL: {item.url}"))
    if detail != "minimal":
        if item.tags:
            console.print(Text(f"Tags: {', '.join(item.tags)}"))
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
                    shown.append(f"  {label}: {val}")
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
                    console.print(Text(f"  {rk}: {rv}"))
        if item.abstract:
            console.print("\n[bold]Abstract:[/bold]")
            console.print(Text(item.abstract))
        if notes:
            console.print(f"\n[bold]Notes ({len(notes)}):[/bold]")
            for n in notes:
                console.print(Text(f"  [{n.key}] {n.content[:200]}"))
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
        console.print(f"[bold cyan][{n.key}][/bold cyan]")
        console.print(n.content)
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
        keys = ", ".join(item.key for item in g.items)
        title = g.items[0].title if g.items else ""
        table.add_row(str(i), Text(keys), Text(title), Text(g.match_type), f"{g.score:.2f}")
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
        console.print(content or "")
    elif outline is not None:
        for item in outline:
            indent = "  " * (item["level"] - 1)
            console.print(f"{item['number']}. {indent}{item['text']}")
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
        desc = ws.description[:47] + "..." if len(ws.description) > 50 else ws.description
        created = ws.created[:10] if len(ws.created) >= 10 else ws.created
        table.add_row(Text(ws.name), Text(desc), str(len(ws.items)), Text(created))
    console.print(table)
    return buf.getvalue()


def format_workspace_query(results: list, mode: str, output_json: bool = False) -> str:
    if output_json:
        data = {
            "mode": mode,
            "results": [
                {
                    "rank": i + 1,
                    "score": round(r[1], 4),
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
    for i, (cid, score, chunk) in enumerate(results):
        preview = chunk["content"][:120].replace("\n", " ")
        console.print(f"[{i + 1}] Score: {score:.2f} | {chunk['item_key']} | {chunk['source']}")
        console.print(f"    {preview}...")
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
    lines = [f"Error: {error.message}"]
    if error.hint:
        lines.append(f"Hint: {error.hint}")
    return "\n".join(lines)


def print_error(error: str | ErrorInfo, output_json: bool = False) -> None:
    """Emit a structured error to the correct channel.

    JSON mode: envelope to stdout (parseable result for agents).
    Text mode: human line(s) to stderr (human-facing diagnostic).

    Does not exit. Use zotero_cli.output.exit_codes.emit_error when you want to exit.
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
    """Format a success result. JSON mode emits envelope; text mode emits `human_text`."""
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


def _add_collection_to_tree(parent: Tree, c: Collection) -> None:
    node = parent.add(f"[cyan]{c.name}[/cyan] ({c.key})")
    for ch in c.children:
        _add_collection_to_tree(node, ch)
