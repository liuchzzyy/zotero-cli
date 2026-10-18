"""Helpers for the parsed-document payload sent to the analysis model.

Re-imported items often carry the same PDF twice, and prompts need a neutral
author/journal context; both transformations stay independent of any AI client.
"""

from __future__ import annotations

from zotero_cli.models import Item


def _item_authors(item: Item) -> str:
    return ", ".join(c.full_name for c in item.creators)


def _item_journal(item: Item) -> str:
    return str(item.extra.get("publicationTitle") or item.extra.get("journalAbbreviation") or "")


def _drop_duplicate_parts(parts: list[str]) -> list[str]:
    """Drop repeated copies of one document, keeping the longest copy.

    A re-imported item often carries the same PDF twice, which would put the
    paper into the prompt twice. Two parts match when at least 90% of one's long
    lines also occur in the other; the longer part wins, the earlier one breaks
    a tie. Surviving parts keep their original order, since main text must stay
    ahead of any appendix.
    """
    if len(parts) < 2:
        return parts

    def lines(text: str) -> set[str]:
        return {line.strip() for line in text.splitlines() if len(line.strip()) >= 60}

    sigs = [lines(text) for text in parts]

    def repeated(index: int) -> bool:
        return any(
            other != index
            and (len(parts[other]), -other) > (len(parts[index]), -index)
            and sigs[index]
            and len(sigs[index] & sigs[other]) >= 0.9 * len(sigs[index])
            for other in range(len(parts))
        )

    return [text for index, text in enumerate(parts) if not repeated(index)]


def _sections_to_text(sections: list[dict]) -> str:
    """Flatten a sections-JSON payload into compact text for keyword extraction."""
    lines: list[str] = []
    for block in sections or []:
        btype = block.get("type")
        if btype == "heading":
            lines.append("## " + str(block.get("text", "")))
        elif btype == "paragraph":
            lines.append(str(block.get("text", "")))
        elif btype == "bullet_list":
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    lines.append("- " + str(item.get("text", "")))
        elif btype == "table":
            headers = block.get("headers") or []
            if headers:
                lines.append("| " + " | ".join(str(h) for h in headers) + " |")
            for row in block.get("rows") or []:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(lines)
