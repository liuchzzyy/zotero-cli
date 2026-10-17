"""Flatten AI-note prose for Zotero.

Two normalisations, applied in this order:

1. No super/subscripts. Unicode superscripts survive into the note only as
   typography and mangle the reading flow, so they are written out. A negative
   exponent keeps a caret (``g⁻¹`` → ``g^-1``) because ``g-1`` would read as a
   subtraction; subscripts and charge signs are simply flattened (``MnO₂`` →
   ``MnO2``, ``Zn²⁺`` → ``Zn2+``).
2. A half-width space between CJK and Latin. MinerU output and the model glue
   Chinese to English terms and numbers ("层状δ-MnO2"), which reads as a typo in
   Zotero's proportional font.

Prompts ask for both; the renderer enforces them because an instruction alone is
only probabilistic. Both steps are idempotent.
"""

from __future__ import annotations

import re

_SUPERSCRIPT = {
    "\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3", "\u2074": "4",
    "\u2075": "5", "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
    "\u207a": "+", "\u207b": "-", "\u207c": "=", "\u207d": "(", "\u207e": ")",
    "\u207f": "n", "\u2071": "i",
}

_SUBSCRIPT = {
    "\u2080": "0", "\u2081": "1", "\u2082": "2", "\u2083": "3", "\u2084": "4",
    "\u2085": "5", "\u2086": "6", "\u2087": "7", "\u2088": "8", "\u2089": "9",
    "\u208a": "+", "\u208b": "-", "\u208c": "=", "\u208d": "(", "\u208e": ")",
    "\u2090": "a", "\u2091": "e", "\u2092": "o", "\u2093": "x", "\u2095": "h",
    "\u2096": "k", "\u2097": "l", "\u2098": "m", "\u2099": "n", "\u209a": "p",
    "\u209b": "s", "\u209c": "t",
}

_SCRIPT_RUN = re.compile(
    f"[{''.join(_SUPERSCRIPT)}]+|[{''.join(_SUBSCRIPT)}]+"
)

_CJK = (
    "\u3040-\u30ff"  # Hiragana, Katakana
    "\u3400-\u4dbf"  # CJK Ext. A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK Compatibility Ideographs
)

_LATIN = (
    "0-9A-Za-z"
    "\u0370-\u03ff"  # Greek: δ-MnO2, α-MnO2
    "\u1f00-\u1fff"  # Greek Extended
)

# A charge sign, permille/percent or degree sign ends a Latin run, so the space
# goes after it: "Zn2+" + "嵌入".
_LATIN_TAIL = _LATIN + "%\u2030\u00b0+"

# Zero-width matches at each boundary; re.sub drops the separator into them.
_BOUNDARY = re.compile(rf"(?<=[{_CJK}])(?=[{_LATIN}])|(?<=[{_LATIN_TAIL}])(?=[{_CJK}])")


# MinerU's VLM tokenises LaTeX digits separately, so prices and capacities
# arrive as "$2 0 1 . 6$". Models then either misread or invent precision to
# fill the gap, so the digits are rejoined before the text is sent.
_SPACED_DIGITS = re.compile(r"(?<=\d) +(?=\d)")
_SPACE_BEFORE_DOT = re.compile(r"(?<=\d) +\.(?= *\d)")
_SPACE_AFTER_DOT = re.compile(r"(?<=\d\.) +(?=\d)")


def join_spaced_digits(text: str) -> str:
    """Rejoin digits and decimal points that MinerU split with spaces.

    Digits are joined first: "2 0 1 . 6" has to become "201 . 6" before the
    decimal rule can see a number on both sides of the dot.
    """
    # Each pass fixes one side of a dot ("2 0 1 . 6" -> "201 . 6" -> "201. 6"
    # -> "201.6"), so iterate to a fixed point.
    for _ in range(3):
        updated = _SPACE_AFTER_DOT.sub("", _SPACE_BEFORE_DOT.sub(".", _SPACED_DIGITS.sub("", text)))
        if updated == text:
            break
        text = updated
    return text


def plain_super_subscripts(text: str) -> str:
    """Write Unicode super/subscripts out as plain characters."""

    def flatten(match: re.Match[str]) -> str:
        run = match.group(0)
        if run[0] in _SUBSCRIPT:
            return "".join(_SUBSCRIPT.get(char, char) for char in run)
        body = "".join(_SUPERSCRIPT.get(char, char) for char in run)
        return f"^{body}" if body.startswith("-") else body

    return _SCRIPT_RUN.sub(flatten, text)


def pad_cjk_latin(text: str) -> str:
    """Return ``text`` with a half-width space at every CJK/Latin boundary."""
    return _BOUNDARY.sub(" ", text)


def prepare(text: str) -> str:
    """Flatten a note string for Zotero: no super/subscripts, CJK/Latin spaced."""
    return pad_cjk_latin(plain_super_subscripts(text))
