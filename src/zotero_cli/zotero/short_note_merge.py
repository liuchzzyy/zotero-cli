"""Merge helpers for notes carried in the Zotero Extra field."""

from __future__ import annotations


def merge_short_note_into_extra(extra: str, short_note: str) -> str:
    """Merge a short-note line into Zotero Extra text, replacing any existing one.

    Preserves all other Extra lines (funder, translated-title, ...) and appends
    a single "short-note: ..." line at the end.
    """
    lines = [line for line in (extra or "").splitlines() if not line.strip().lower().startswith("short-note:")]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(f"short-note: {short_note.strip()}")
    return "\n".join(lines)
