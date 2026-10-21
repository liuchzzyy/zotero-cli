"""Formatting rules for Chinese metadata written to Zotero.

Two rules, both required of the Chinese title *and* the Chinese abstract:

1. No super/subscripts. ``MnO₂``, ``Zn²⁺``, ``mAh g⁻¹``, ``<sub>2</sub>`` and
   ``^{2+}`` all become flat text, so a formula reads as ``MnO2`` /
   ``Zn2+`` / ``mAh g^-1``. Only a negative exponent keeps a caret, because
   ``g-1`` would read as a subtraction.
2. Exactly one half-width space at every CJK↔Latin boundary, never inside a
   unit or formula.

Zotero renders `abstractNote` and an Extra line as plain text, so a surviving
script character is visible damage rather than typography.

The character classes and the Unicode flattening come from
`zotero_cli.ai_notes.text`, so an abstract translated here and an AI note
rendered by the package cannot drift apart. Those two functions do not cover
HTML or LaTeX script markup, which a model may emit instead of a Unicode
character, so this module flattens those first, then delegates.

`normalize` is idempotent: metadata is re-run whenever an item is repaired, and
a second pass must not add a second space or a stray caret.
"""

from __future__ import annotations

import re

from zotero_cli.ai_notes.text import pad_cjk_latin, plain_super_subscripts

# `<sub>2</sub>` carries the same meaning as `₂`; the tag goes, the digit stays.
_SCRIPT_TAG = re.compile(r"</?(?:sup|sub)\b[^>]*>", re.IGNORECASE)

# LaTeX forms written by a model: `_{2}`, `^{2+}`, `^{-1}`.
_LATEX_SCRIPT = re.compile(r"([_^])\{([^{}]*)\}")

# Inline math that wraps nothing but a script: `MnO$_{2}$`, `$g^{-1}$`. Only a
# span carrying `_`/`^` is treated as math, so a price like "$500" survives.
_MATH_SCRIPT = re.compile(r"\$([^$\n]{0,60}?[_^](?:\{[^{}]*\}|[0-9A-Za-z+\-])[^$\n]{0,60}?)\$")

_WHITESPACE = re.compile(r"\s+")


def _flatten_latex(match: re.Match[str]) -> str:
    marker, body = match.group(1), match.group(2).strip()
    if marker == "^" and body.startswith("-"):
        return f"^{body}"
    return body


def flatten_scripts(text: str) -> str:
    """Write HTML-tagged, LaTeX and Unicode super/subscripts as plain characters."""
    unwrapped = _MATH_SCRIPT.sub(r"\1", _SCRIPT_TAG.sub("", text))
    return str(plain_super_subscripts(_LATEX_SCRIPT.sub(_flatten_latex, unwrapped)))


def collapse_whitespace(text: str) -> str:
    """Collapse every whitespace run to one space, so a field stays one paragraph."""
    return _WHITESPACE.sub(" ", text).strip()


def normalize(text: str) -> str:
    """Return ``text`` ready for a Zotero field: no scripts, CJK/Latin spaced."""
    return str(pad_cjk_latin(collapse_whitespace(flatten_scripts(text))))


# A model that echoes the source's own "Abstract"/"摘要" label leaves a stray word
# at the start of the field.
_LEADING_LABEL = re.compile(r"^\s*(?:abstract|摘要)\s*[:：]?\s*", re.IGNORECASE)


def normalize_abstract(text: str) -> str:
    """`normalize` for an abstract, without a leading "摘要：" label."""
    normalized = normalize(text)
    while True:
        stripped = _LEADING_LABEL.sub("", normalized)
        if stripped == normalized:
            return normalized
        normalized = normalize(stripped)


def require_chinese_abstract(text: object) -> str:
    """Normalize a translation and reject empty/label-only or non-Chinese output."""
    if not isinstance(text, str):
        raise ValueError("中文摘要必须是非空文本")
    normalized = normalize_abstract(text)
    if not normalized or not re.search(r"[\u4e00-\u9fff]", normalized):
        raise ValueError("正规化后的中文摘要为空或不含中文正文")
    return normalized


def has_script(text: str) -> bool:
    """True when ``text`` still carries a super/subscript in any of the three forms."""
    if _SCRIPT_TAG.search(text) or _LATEX_SCRIPT.search(text):
        return True
    return bool(plain_super_subscripts(text) != text)
