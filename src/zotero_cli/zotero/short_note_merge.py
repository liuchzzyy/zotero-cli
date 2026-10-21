"""合并携带在 Zotero 附加字段（Extra）中的注记的辅助函数。"""

from __future__ import annotations


def merge_short_note_into_extra(extra: str, short_note: str) -> str:
    """把 short-note 行合并进 Zotero Extra 文本，并替换已有的同名行。

    保留其他所有 Extra 行（资助方、译名等），并在末尾追加唯一一行
    "short-note: ..."。
    """
    lines = [line for line in (extra or "").splitlines() if not line.strip().lower().startswith("short-note:")]
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(f"short-note: {short_note.strip()}")
    return "\n".join(lines)
