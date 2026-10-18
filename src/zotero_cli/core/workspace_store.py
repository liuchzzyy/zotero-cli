"""Repo-local workspace manifests, index paths, and MinerU cache directories."""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

from zotero_cli.config import project_root, state_dir

# Workspace names are path components: allow Unicode/CJK names plus `_` and
# `-`, while requiring an alphanumeric/CJK first and last character.
_NAME_RE = re.compile(r"^[a-z0-9\u4e00-\u9fff]+(?:[-_][a-z0-9\u4e00-\u9fff]+)*$")


def workspaces_dir() -> Path:
    return project_root() / ".workspace"


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Unsafe workspace path: {path}; symbolic links are not allowed")


def _workspace_root() -> Path:
    root = workspaces_dir()
    _reject_symlink(root)
    return root


def _contained_path(path: Path, root: Path) -> Path:
    """Check existing and dangling links before resolving a workspace path."""
    _reject_symlink(path)
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Unsafe workspace path: {path}; unable to resolve path") from exc
    if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Unsafe workspace path: {path}; path must stay inside {root}")
    return path


def workspace_dir(name: str) -> Path:
    """Resolve one workspace component without allowing user-supplied paths.

    Keep legacy names (including spaces and Unicode) usable independently of
    the stricter naming convention applied when creating workspaces in the CLI.
    """
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or Path(name).is_absolute()
        or PureWindowsPath(name).drive
    ):
        raise ValueError(f"Invalid workspace name: {name!r}; expected a single directory name")
    root = _workspace_root()
    return _contained_path(root / name, root)


def workspace_file_path(name: str) -> Path:
    directory = workspace_dir(name)
    return _contained_path(directory / "workspace.toml", directory)


def workspace_index_path(name: str) -> Path:
    directory = workspace_dir(name)
    path = _contained_path(directory / "rag.idx.sqlite", directory)
    # SQLite can open these alongside the database, including during recovery.
    for suffix in ("-journal", "-wal", "-shm"):
        _contained_path(path.with_name(path.name + suffix), directory)
    return path


def mineru_cache_dir() -> Path:
    # Shared cloud parse packages are runtime state, not workspace definitions.
    return state_dir(project_root()) / "mineru"


def validate_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


@dataclass
class WorkspaceItem:
    key: str
    title: str
    added: str


@dataclass
class Workspace:
    name: str
    created: str
    description: str = ""
    items: list[WorkspaceItem] = field(default_factory=list)

    def has_item(self, key: str) -> bool:
        return any(i.key == key for i in self.items)

    def add_item(self, key: str, title: str) -> bool:
        """Add item. Returns False if already present."""
        if self.has_item(key):
            return False
        self.items.append(
            WorkspaceItem(
                key=key,
                title=title,
                added=datetime.now(timezone.utc).isoformat(),
            )
        )
        return True

    def remove_item(self, key: str) -> bool:
        """Remove item. Returns False if not present."""
        before = len(self.items)
        self.items = [i for i in self.items if i.key != key]
        return len(self.items) < before


def _workspace_path(name: str) -> Path:
    return workspace_file_path(name)


def _escape_toml_string(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def save_workspace(ws: Workspace) -> None:
    path = _workspace_path(ws.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'created = "{_escape_toml_string(ws.created)}"',
        f'description = "{_escape_toml_string(ws.description)}"',
        "",
    ]
    for item in ws.items:
        lines.append("[[items]]")
        lines.append(f'key = "{_escape_toml_string(item.key)}"')
        lines.append(f'title = "{_escape_toml_string(item.title)}"')
        lines.append(f'added = "{_escape_toml_string(item.added)}"')
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def load_workspace(name: str) -> Workspace:
    path = _workspace_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Workspace '{name}' not found")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    items = [WorkspaceItem(key=i["key"], title=i["title"], added=i["added"]) for i in data.get("items", [])]
    return Workspace(
        name=name,
        created=data.get("created", ""),
        description=data.get("description", ""),
        items=items,
    )


def list_workspaces() -> list[Workspace]:
    ws_dir = _workspace_root()
    if not ws_dir.exists():
        return []
    result = []
    # Validate each directory before looking for its manifest; globbing through
    # a symlinked workspace would otherwise probe outside the workspace root.
    for path in sorted(ws_dir.iterdir()):
        try:
            result.append(load_workspace(path.name))
        except Exception:
            continue
    return result


def delete_workspace(name: str) -> None:
    path = workspace_dir(name)
    if not path.exists():
        raise FileNotFoundError(f"Workspace '{name}' not found")
    shutil.rmtree(path)


def workspace_exists(name: str) -> bool:
    return _workspace_path(name).exists()
