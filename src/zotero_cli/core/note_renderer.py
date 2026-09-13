from __future__ import annotations

import html as _html

_PRIMARY = "#EF7060"
_BACKGROUND = "#fff9f9"
_CITE = "#2b6cb0"
_BORDER = "#eeeeee"


def _escape(text: object) -> str:
    return _html.escape(str(text), quote=False)


def render_note(sections: list[dict], *, title: str) -> str:
    """Render a sections-JSON payload to an inline-styled HTML note."""
    parts = [
        f"<h1 style='color:{_PRIMARY};border-bottom:2px solid {_PRIMARY};padding-bottom:6px;'>"
        f"{_escape(title)}</h1>"
    ]
    for section in sections or []:
        rendered = _render_block(section)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def _render_block(block: dict) -> str:
    btype = block.get("type")
    if btype == "heading":
        return _heading(int(block.get("level", 3)), str(block.get("text", "")))
    if btype == "paragraph":
        return _paragraph(str(block.get("text", "")))
    if btype == "bullet_list":
        return _bullet_list(block.get("items") or [])
    if btype == "table":
        return _table(block.get("headers") or [], block.get("rows") or [])
    if btype == "hr":
        return f"<hr style='border:none;border-top:1px solid {_PRIMARY};margin:12px 0;'/>"
    return ""


def _heading(level: int, text: str) -> str:
    level = max(2, min(level, 4))
    text = _escape(text)
    if level == 2:
        return (
            f"<h2 style='background:{_PRIMARY};color:#ffffff;padding:6px 12px;"
            f"border-radius:4px;margin:14px 0 8px;'>{text}</h2>"
        )
    return f"<h{level} style='color:{_PRIMARY};margin:12px 0 6px;'>{text}</h{level}>"


def _paragraph(text: str) -> str:
    return f"<p style='margin:0.4em 0;'>{_escape(text)}</p>"


def _bullet_list(items: list[dict]) -> str:
    if not items:
        return ""
    lis: list[str] = []
    for item in items:
        text = _escape(item.get("text", ""))
        citations = item.get("citations") or []
        if citations:
            cite_html = "".join(
                f"<div style='margin:2px 0 0 12px;padding:4px 8px;border-left:3px solid {_CITE};"
                f"background:{_BACKGROUND};color:{_CITE};font-size:0.85em;'>"
                f"「📍 {_escape(c.get('location', ''))}：{_escape(c.get('content', ''))}」</div>"
                for c in citations
            )
            lis.append(f"<li style='margin:0.2em 0;'>{text}{cite_html}</li>")
        else:
            lis.append(f"<li style='margin:0.2em 0;'>{text}</li>")
    return f"<ul style='margin:0.4em 0;padding-left:1.5em;'>{''.join(lis)}</ul>"


def _table(headers: list[str], rows: list[list]) -> str:
    thead = "".join(
        f"<th style='background:{_PRIMARY};color:#ffffff;padding:4px 8px;border:1px solid {_BORDER};'>"
        f"{_escape(h)}</th>"
        for h in headers
    )
    body = "".join(
        "<tr>" + "".join(f"<td style='padding:4px 8px;border:1px solid {_BORDER};'>{_escape(c)}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return (
        f"<table style='border-collapse:collapse;margin:8px 0;max-width:100%;'>"
        f"<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"
    )
