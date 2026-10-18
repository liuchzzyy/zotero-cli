"""Text normalization and paper-kind metadata helpers for the RAG pipeline."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

# Covers/headers that identify a supplementary document. These are matched at the
# *start* of a line near the top of the text, so ordinary prose that merely
# mentions "Supporting Information" does not trigger a classification.
_SUPPLEMENTARY_COVER_HINTS = (
    "electronic supplementary material",
    "electronic supplementary information",
    "supplementary material",
    "supplementary information",
    "supporting information",
    "supporting info",
    "supporting data",
    "supplementary data",
    "electronic annex",
    "si appendix",
)
_SUPPLEMENTARY_HINTS = _SUPPLEMENTARY_COVER_HINTS + ("figure s1", "table s1")

# Words that may follow a cover phrase on a genuine cover line ("Supporting
# Information *for* <title>"). Anything else means the line is prose.
_COVER_CONTINUATIONS = frozenset({"for", "of", "to", "esi"})

# Strong main-article cues: a typeset article front matter. Weak cues are the
# publisher stamps that a supplement inherits from its parent article — the SI
# cover of `sciadv.aea1401_sm.pdf` carries the article DOI — so they are checked
# only after every supplementary cue has been ruled out.
_MAIN_PAPER_HINTS = (
    "cite this:",
    "broader context",
    "abstract",
    "introduction",
)
_WEAK_MAIN_PAPER_HINTS = (
    "received ",
    "accepted ",
    "doi:",
)

# Attachment filenames are a reliable supplementary signal ("..._SI.pdf",
# "supplementary-material.pdf"); tokens are matched between non-alphanumerics so
# ordinary words such as "sodium" or "spin" are not misread as "si".
_SUPPLEMENTARY_FILENAME_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:si|esi|supp|suppl|supplementary|supporting)(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)


def _supplementary_filename(filename: str) -> bool:
    return bool(filename) and bool(_SUPPLEMENTARY_FILENAME_RE.search(filename))


def _cover_line(line: str) -> bool:
    """True for `Supporting Information`, `Supplementary Material for <title>`, …

    Matched as a prefix rather than a whole line, because cover variants append
    the parent paper's title or the journal name. A continuation word or an
    opening bracket must follow the phrase; prose such as "Supporting
    Information is available free of charge" keeps its default main kind.
    """
    text = line.strip(" #\t\r*-_")
    lowered = text.lower()
    for phrase in _SUPPLEMENTARY_COVER_HINTS:
        if not lowered.startswith(phrase):
            continue
        rest = text[len(phrase) :].lstrip(" \t")
        if not rest.strip(" .,:;-–—"):
            return True
        if rest[0] in "(:[—–-":
            return True
        if rest.split()[0].strip(".,;:").lower() in _COVER_CONTINUATIONS:
            return True
    return False


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in text.lower().split():
        word = re.sub(r"[.,;:!?()\"'\[\]{}]+$", "", word)
        word = re.sub(r"^[.,;:!?()\"'\[\]{}]+", "", word)
        if word:
            tokens.append(word)
    return tokens


def build_metadata_chunk(title: str, authors: str, abstract: str | None, tags: list[str]) -> str:
    parts = [f"Title: {title}", f"Authors: {authors}"]
    if abstract:
        parts.append(f"Abstract: {abstract}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def infer_pdf_kind(text: str, filename: str = "") -> str:
    head = f"{filename}\n{text[:2500]}".lower()
    extended = f"{filename}\n{text[:10000]}".lower()
    lead = f"{filename}\n{text[:500]}".lower()
    early = f"{filename}\n{text[:1200]}".lower()
    if _supplementary_filename(filename) and "cite this:" not in lead:
        return "supplementary"
    if "cite this:" not in lead and any(_cover_line(line) for line in lead.splitlines()):
        return "supplementary"
    if any(hint in head for hint in _MAIN_PAPER_HINTS):
        return "main"
    if "experimental section" in head:
        return "supplementary"
    # An SI cover the line rules above did not recognise still outranks a
    # publisher stamp: `sciadv.aea1401_sm.pdf` carries the article DOI at char
    # 247. Only an *early* mention counts — a main article cites its own SI
    # thousands of characters in (item DDM2563M: char 9073), which must not
    # flip it.
    if any(hint in early for hint in _SUPPLEMENTARY_HINTS):
        return "supplementary"
    if any(hint in head for hint in _WEAK_MAIN_PAPER_HINTS):
        return "main"
    if any(hint in extended for hint in _SUPPLEMENTARY_HINTS):
        return "supplementary"
    return "main"


# --- Figure/table numbering ------------------------------------------------
# Cue-free structure: a document that numbers its own panels "Figure S1",
# "Supplementary Fig. 3" or "Table S2" is a supplement; one that numbers them
# "Figure 1"/"Table 1" is the article. The numbering survives every publisher
# layout, so it beats cover text and filenames — but MinerU puts the "S" in two
# places (on the number, or on a qualifier word), and both must be accepted.
_CAPTION_LABEL_RE = re.compile(
    r"\b(?:(?P<qualifier>supplementary|supporting|esi)\s+)?"
    r"(?P<label>figure|fig\.?|scheme|table)\s*\.?\s*"
    r"(?P<number>s\s*\d+|\d+)",
    re.IGNORECASE,
)
_CAPTION_FIELDS = {"image": "image_caption", "chart": "chart_caption", "table": "table_caption"}

# A typeset article front matter. This is the one signal that outranks caption
# numbering, because demoting a main text to supplementary is the expensive
# mistake: its figures then become optional and get sampled away.
_FRONT_MATTER_ABSTRACT_CHARS = 6000
_FRONT_MATTER_INTRO_CHARS = 12_000


@dataclass(frozen=True)
class CaptionCounts:
    """How one document numbers its own figures and tables."""

    figure_supplementary: int = 0
    figure_plain: int = 0
    table_supplementary: int = 0
    table_plain: int = 0

    @property
    def supplementary(self) -> int:
        return self.figure_supplementary + self.table_supplementary

    @property
    def plain(self) -> int:
        return self.figure_plain + self.table_plain

    @property
    def kind(self) -> str:
        """`supplementary`, `main`, or `unknown` from numbering alone.

        Figures decide first and tables only break a tie: a supplement numbers
        *all* its panels, so a document with plain figures is an article even if
        it also carries a stray "Supplementary Table" caption.
        """
        for supplementary, plain in (
            (self.figure_supplementary, self.figure_plain),
            (self.table_supplementary, self.table_plain),
        ):
            if supplementary > plain:
                return "supplementary"
            if plain > supplementary:
                return "main"
        return "unknown"


@dataclass(frozen=True)
class PdfDocument:
    """One parsed attachment as the kind classifier sees it."""

    identifier: str
    filename: str
    markdown: str
    blocks: Any


def _iter_blocks(content: Any) -> Iterator[dict[str, Any]]:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block
    elif isinstance(content, dict):
        for key in ("content_list", "blocks", "content"):
            if isinstance(content.get(key), list):
                yield from _iter_blocks(content[key])
                return


def _caption_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return value if isinstance(value, str) else ""


def caption_counts(blocks: Any) -> CaptionCounts:
    """Count S-numbered versus plain-numbered figure and table captions."""
    figure_supplementary = figure_plain = table_supplementary = table_plain = 0
    for block in _iter_blocks(blocks):
        field = _CAPTION_FIELDS.get(str(block.get("type") or ""))
        if field is None:
            continue
        match = _CAPTION_LABEL_RE.search(_caption_text(block.get(field)))
        if match is None:
            continue
        numbered_s = bool(match.group("qualifier")) or match.group("number").lower().startswith("s")
        if match.group("label").lower().rstrip(".") == "table":
            if numbered_s:
                table_supplementary += 1
            else:
                table_plain += 1
        elif numbered_s:
            figure_supplementary += 1
        else:
            figure_plain += 1
    return CaptionCounts(figure_supplementary, figure_plain, table_supplementary, table_plain)


def _has_front_matter(markdown: str) -> bool:
    low = markdown.lower()
    return "abstract" in low[:_FRONT_MATTER_ABSTRACT_CHARS] and "introduction" in low[:_FRONT_MATTER_INTRO_CHARS]


def _kinds(documents: Sequence[PdfDocument], supplements: Sequence[str]) -> dict[str, str]:
    """Name the supplement set; every other document is main text."""
    return {doc.identifier: "supplementary" if doc.identifier in supplements else "main" for doc in documents}


def resolve_pdf_kinds(documents: Sequence[PdfDocument]) -> dict[str, str]:
    """Classify an item's attachments together, not one at a time.

    A single attachment is the main text by definition; with two or more, the
    document whose captions are S-numbered is the supplement and the rest are
    main text. The rules are ordered so the *conservative* answer wins: leaving
    a document as main text costs nothing but images, while demoting a real
    article to a supplement lets its figures be sampled away.
    """
    if not documents:
        return {}
    if len(documents) == 1:
        return {documents[0].identifier: "main"}
    counts = {doc.identifier: caption_counts(doc.blocks) for doc in documents}
    front = {doc.identifier: _has_front_matter(doc.markdown) for doc in documents}

    def rest(main: str) -> list[str]:
        """Every document but `main` — the supplement set when one article is named."""
        return [doc.identifier for doc in documents if doc.identifier != main]

    # A merged article+SI PDF carries both front matter and S-numbered panels;
    # the front matter wins.
    numbered = {
        doc.identifier: (
            "main"
            if front[doc.identifier] and counts[doc.identifier].kind == "supplementary"
            else counts[doc.identifier].kind
        )
        for doc in documents
    }

    # 1. Exactly one document numbers its panels with an S.
    s_numbered = [identifier for identifier, kind in numbered.items() if kind == "supplementary"]
    if len(s_numbered) == 1:
        return _kinds(documents, s_numbered)
    # 2. No document's figures say "S", but exactly one carries any S-numbered
    #    caption at all — a Nature-style supplement numbers figures plainly and
    #    tables "Supplementary Table 1".
    any_s = [identifier for identifier, value in counts.items() if value.supplementary and not front[identifier]]
    if len(any_s) == 1:
        return _kinds(documents, any_s)
    # 3. Exactly one plainly numbered document; anything unnumbered beside it is
    #    the supplement.
    plain = [identifier for identifier, kind in numbered.items() if kind == "main"]
    if len(plain) == 1:
        return _kinds(documents, rest(plain[0]))
    # 4. Fall back to the cover/filename heuristics of `infer_pdf_kind`.
    textual = [
        doc.identifier
        for doc in documents
        if not front[doc.identifier] and infer_pdf_kind(doc.markdown, doc.filename) == "supplementary"
    ]
    if len(textual) == 1:
        return _kinds(documents, textual)
    # 5. Nothing conclusive: the longer document is the article.
    return _kinds(documents, rest(max(documents, key=lambda doc: len(doc.markdown)).identifier))


def get_pdf_kind_from_source(source: str) -> str | None:
    if source.startswith("pdf:main:"):
        return "main"
    if source.startswith("pdf:supplementary:"):
        return "supplementary"
    return None


def filter_ranked_results_by_pdf_kind(
    results: list[tuple[int, float, dict]],
    pdf_kind: str | None,
) -> list[tuple[int, float, dict]]:
    if not pdf_kind or pdf_kind == "any":
        return results
    filtered: list[tuple[int, float, dict]] = []
    for cid, score, chunk in results:
        kind = get_pdf_kind_from_source(chunk.get("source", ""))
        if kind == pdf_kind:
            filtered.append((cid, score, chunk))
    return filtered


def _decode_numeric_entity(match: re.Match[str], *, base: int) -> str:
    raw = match.group(1)
    try:
        codepoint = int(raw, base)
    except ValueError:
        return match.group(0)
    # Reject NUL, surrogate code points, and values outside Unicode; malformed
    # entities are kept literally instead of crashing the caller.
    if codepoint <= 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
        return match.group(0)
    return chr(codepoint)


def clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<td[^>]*>(.*?)</td>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"<th[^>]*>(.*?)</th>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<tr[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</table>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<table[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&#(\d+);", lambda match: _decode_numeric_entity(match, base=10), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda match: _decode_numeric_entity(match, base=16), text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&apos;", "'", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
