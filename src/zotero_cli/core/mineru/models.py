"""Shared types, errors, and constants for the MinerU pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ProgressCallback = Callable[[str, int, int, int], None]

_MAX_FILE_BYTES = 200 * 1024 * 1024
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class MinerUError(Exception):
    """Raised when a cloud parse or local parse-package operation fails."""


@dataclass(frozen=True)
class MinerUParseResult:
    """One canonical MinerU parse package shared by AI notes and RAG."""

    source_path: Path
    fingerprint: str
    model_version: str
    root: Path
    markdown_path: Path
    content_list_path: Path
    manifest_path: Path

    @property
    def markdown(self) -> str:
        return self.markdown_path.read_text(encoding="utf-8")

    @property
    def content_list(self) -> Any:
        return json.loads(self.content_list_path.read_text(encoding="utf-8"))

    @property
    def image_paths(self) -> list[Path]:
        raw_dir = self.root / "raw"
        if not raw_dir.exists():
            return []
        return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES)
