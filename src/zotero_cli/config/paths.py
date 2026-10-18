"""Repository-relative configuration and state paths."""

from __future__ import annotations

from pathlib import Path


def _find_project_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return None


def _source_project_root() -> Path | None:
    return _find_project_root(Path(__file__).resolve())


def project_root(start: Path | None = None) -> Path:
    if start is not None:
        current = start.resolve()
        return _find_project_root(current) or current

    source_root = _source_project_root()
    if source_root is not None:
        return source_root

    current = Path.cwd().resolve()
    return _find_project_root(current) or current


def settings_dir(start: Path | None = None) -> Path:
    return project_root(start) / SETTINGS_DIRNAME


def config_file_path(start: Path | None = None) -> Path:
    return settings_dir(start) / CONFIG_FILENAME


def state_dir(start: Path | None = None) -> Path:
    return settings_dir(start) / STATE_DIRNAME


SETTINGS_DIRNAME = ".zot"

CONFIG_FILENAME = "config.toml"

STATE_DIRNAME = "state"
