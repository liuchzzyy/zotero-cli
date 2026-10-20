"""Resolve AI note prompt templates from the checkout or the installed wheel.

Prompts live in `tools/ainote/templates/` in a checkout. An installed CLI has no
`tools/`, so the same files are force-included into the wheel at
`zotero_cli/templates/` and probed second.
"""

from __future__ import annotations

import re
from pathlib import Path

from zotero_cli.config import project_root
from zotero_cli.core.notes.constants import MAX_PROMPT_CHARS, PROMPT_RESERVE_CHARS


def _candidate_template_dirs() -> list[Path]:
    root = project_root()
    # core/notes/<this file>: three levels up is the zotero_cli package root,
    # which is where the wheel force-include places templates/.
    package_dir = Path(__file__).resolve().parents[2]
    return [
        root / "tools" / "ainote" / "templates",
        # Shipped inside the wheel by hatchling's force-include, so
        # `ai_analyze` works when the CLI is installed outside a checkout.
        package_dir / "templates",
    ]


_TEMPLATES_DIR = _candidate_template_dirs()[0]


# Alias -> template filename. Keys are accepted from the classifier output and
# from explicit overrides; the classifier emits research_article / review_article / book.
_TEMPLATE_ALIASES: dict[str, str] = {
    "research": "research-article.md",
    "research_article": "research-article.md",
    "review": "review-article.md",
    "review_article": "review-article.md",
    "book": "book.md",
}

_MISSING = "未知"


def template_path(name: str) -> Path:
    filename = _TEMPLATE_ALIASES.get(name, name)
    if not filename.endswith(".md"):
        filename += ".md"
    for d in _candidate_template_dirs():
        candidate = d / filename
        if candidate.exists():
            return candidate
    dirs_checked = ", ".join(str(d) for d in _candidate_template_dirs())
    raise FileNotFoundError(f"AI note template '{name}' ({filename}) not found in: {dirs_checked}")


def load_template(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8")


def format_template(
    template: str,
    *,
    title: str = "",
    authors: str = "",
    journal: str = "",
    date: str = "",
    doi: str = "",
    fulltext: str = "",
) -> str:
    """Fill the title/authors/journal/date/doi/fulltext placeholders in a prompt template."""
    values = {
        "title": title.strip() or _MISSING,
        "authors": authors.strip() or _MISSING,
        "journal": journal.strip() or _MISSING,
        "date": date.strip() or _MISSING,
        "doi": doi.strip() or _MISSING,
        "fulltext": fulltext,
    }
    return re.sub(r"\{(title|authors|journal|date|doi|fulltext)\}", lambda match: values[match.group(1)], template)


def fit_text_to_prompt(template: str, text: str) -> str:
    """Truncate untrusted prompt text so template + text stays under the hard cap."""
    budget = MAX_PROMPT_CHARS - len(template) - PROMPT_RESERVE_CHARS
    if budget <= 0:
        return ""
    return text[:budget]
