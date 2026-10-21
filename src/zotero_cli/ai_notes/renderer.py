"""把 ai-note 的 sections JSON 渲染为 Zotero HTML note。"""

from __future__ import annotations

import html as _html
from typing import Any

from zotero_cli.ai_notes.text import prepare

_PRIMARY = "#EF7060"
_BACKGROUND = "#fff9f9"
_CITE = "#2b6cb0"
_BORDER = "#eeeeee"


def _escape(text: object) -> str:
    return _html.escape(str(text), quote=False)


def _text(text: object) -> str:
    """为 HTML 准备笔记内容：展平、加空格，再转义。"""
    return _escape(prepare(str(text)))


def render_note(sections: list[Any], *, title: str) -> str:
    """把 sections JSON 载荷渲染为内联样式的 HTML note。"""
    # 标题由固定标签和条目标题组成，因此要转义但不再重排空格：
    # 前缀匹配要求该标签保持逐字节一致。
    parts = [
        f"<h1 style='color:{_PRIMARY};border-bottom:2px solid {_PRIMARY};padding-bottom:6px;'>{_escape(title)}</h1>"
    ]
    for section in sections or []:
        rendered = _render_block(section)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def _render_block(block: dict) -> str:
    if not isinstance(block, dict):
        return ""
    btype = block.get("type")
    if btype == "heading":
        lvl = block.get("level", 3)
        try:
            level = int(lvl) if lvl is not None else 3
        except (ValueError, TypeError):
            level = 3
        return _heading(level, str(block.get("text", "")))
    if btype == "paragraph":
        return _paragraph(str(block.get("text", "")))
    if btype == "bullet_list":
        items = block.get("items") or []
        return _bullet_list(items if isinstance(items, list) else [])
    if btype == "table":
        headers = block.get("headers") or []
        rows = block.get("rows") or []
        return _table(headers if isinstance(headers, list) else [], rows if isinstance(rows, list) else [])
    if btype == "hr":
        return f"<hr style='border:none;border-top:1px solid {_PRIMARY};margin:12px 0;'/>"
    return ""


def _heading(level: int, text: str) -> str:
    level = max(2, min(level, 4))
    text = _text(text)
    if level == 2:
        return (
            f"<h2 style='background:{_PRIMARY};color:#ffffff;padding:6px 12px;"
            f"border-radius:4px;margin:14px 0 8px;'>{text}</h2>"
        )
    return f"<h{level} style='color:{_PRIMARY};margin:12px 0 6px;'>{text}</h{level}>"


def _paragraph(text: str) -> str:
    return f"<p style='margin:0.4em 0;'>{_text(text)}</p>"


def _bullet_list(items: list) -> str:
    if not items:
        return ""
    lis: list[str] = []
    for item in items:
        nested_html = ""
        if isinstance(item, dict):
            text = _text(item.get("text", ""))
            citations = item.get("citations") or []
            sub_items = item.get("items")
            if isinstance(sub_items, list) and sub_items:
                nested_html = _bullet_list(sub_items)
        else:
            text = _text(str(item or ""))
            citations = []
        if isinstance(citations, list) and citations:
            cite_divs: list[str] = []
            for c in citations:
                if isinstance(c, dict):
                    loc = _text(c.get("location", ""))
                    content = _text(c.get("content", ""))
                    cite_divs.append(
                        f"<div style='margin:2px 0 0 12px;padding:4px 8px;border-left:3px solid {_CITE};"
                        f"background:{_BACKGROUND};color:{_CITE};font-size:0.85em;'>"
                        f"「📍 {loc}：{content}」</div>"
                    )
                elif c:
                    cite_divs.append(
                        f"<div style='margin:2px 0 0 12px;padding:4px 8px;border-left:3px solid {_CITE};"
                        f"background:{_BACKGROUND};color:{_CITE};font-size:0.85em;'>"
                        f"「📍 {_text(c)}」</div>"
                    )
            cite_html = "".join(cite_divs)
            lis.append(f"<li style='margin:0.2em 0;'>{text}{cite_html}{nested_html}</li>")
        else:
            lis.append(f"<li style='margin:0.2em 0;'>{text}{nested_html}</li>")
    return f"<ul style='margin:0.4em 0;padding-left:1.5em;'>{''.join(lis)}</ul>"


def _table(headers: list, rows: list) -> str:
    thead = "".join(
        f"<th style='background:{_PRIMARY};color:#ffffff;padding:4px 8px;border:1px solid {_BORDER};'>{_text(h)}</th>"
        for h in headers
    )
    row_trs: list[str] = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            cells = "".join(f"<td style='padding:4px 8px;border:1px solid {_BORDER};'>{_text(c)}</td>" for c in row)
        else:
            cells = f"<td style='padding:4px 8px;border:1px solid {_BORDER};'>{_text(row)}</td>"
        row_trs.append(f"<tr>{cells}</tr>")
    body = "".join(row_trs)
    return (
        f"<table style='border-collapse:collapse;margin:8px 0;max-width:100%;'>"
        f"<thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"
    )
