"""Attach the figures of a parsed PDF to the AI request.

MinerU writes each figure to ``<cache>/raw/images/<sha>.jpg`` and references it
from the Markdown as ``![](images/<sha>.jpg)``. Sending only the Markdown leaves
a bare filename where a plot should be, so any judgement about figure evidence
rests on captions alone. This module turns those references into image parts for
a multimodal chat request, keeping the numbers used in the prompt text.
"""

from __future__ import annotations

import base64
import re
import stat
from pathlib import Path

_IMAGE_REF = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# MinerU also emits rules and logos as images; skip the tiny ones.
_MIN_BYTES = 4096
_MAX_BYTES = 5 * 1024 * 1024


def mark_figures(markdown: str, raw_dir: Path, start: int = 1) -> tuple[str, list[tuple[int, Path]]]:
    """Replace image references with ``【图 N】`` and return ``(N, path)`` pairs.

    ``start`` continues the numbering when several PDFs feed one prompt, so the
    markers stay unique across the main text and its supporting information.
    Resolve untrusted Markdown paths against the parse-assets root before any
    image content can be read, including paths through symlinked directories.
    """
    figures: list[tuple[int, Path]] = []
    try:
        root = raw_dir.resolve()
    except (OSError, RuntimeError):
        return markdown, figures

    def replace(match: re.Match[str]) -> str:
        try:
            path = (root / match.group(1)).resolve()
            path.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return match.group(0)
        number = start + len(figures)
        figures.append((number, path))
        return f"【图 {number}】"

    return _IMAGE_REF.sub(replace, markdown), figures


def load_figures(refs: list[tuple[int, Path]], limit: int) -> list[tuple[int, str]]:
    """Resolve ``(number, path)`` pairs to ``(number, data_url)``, up to ``limit``.

    A reference that is missing, unreadable, or outside the accepted size window
    is skipped rather than failing the analysis: a partial figure set still beats
    none, and the caller reports how many were kept.
    """
    figures: list[tuple[int, str]] = []
    for number, path in refs:
        if len(figures) >= limit:
            break
        mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
        if not mime:
            continue
        try:
            metadata = path.stat()
            if not stat.S_ISREG(metadata.st_mode):
                continue
            size = metadata.st_size
            if size < _MIN_BYTES or size > _MAX_BYTES:
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        figures.append((number, f"data:{mime};base64,{encoded}"))
    return figures
