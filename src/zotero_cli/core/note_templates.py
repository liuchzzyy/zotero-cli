from __future__ import annotations

from pathlib import Path

from zotero_cli.config import project_root

_TEMPLATES_DIR = project_root() / "tools" / "templates"

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
    return _TEMPLATES_DIR / filename


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
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result
