"""Repo-local workspace manifests, index paths, and MinerU cache directories."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
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

from zotero_cli.config import state_dir, workspace_root

# Workspace names are path components: allow Unicode/CJK names plus _ and -,
# while requiring an alphanumeric/CJK first and last character.
_NAME_RE = re.compile(r"^[a-z0-9\u4e00-\u9fff]+(?:[-_][a-z0-9\u4e00-\u9fff]+)*$")
_MAX_NAME_BYTES = 200


def workspaces_dir() -> Path:
    return workspace_root()


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
    return state_dir() / "mineru"


def validate_name(name: str) -> bool:
    if not name or len(name.encode("utf-8")) > _MAX_NAME_BYTES:
        return False
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


def _toml_string(value: str) -> str:
    """Serialize a Python string as a TOML basic string literal.

    JSON string escaping covers every character TOML requires to be escaped
    (quote, backslash, control characters); non-ASCII text stays raw. DEL is the
    one control character JSON leaves bare and TOML requires escaped.
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")


def _manifest_text(ws: Workspace) -> str:
    lines = [
        f"created = {_toml_string(ws.created)}",
        f"description = {_toml_string(ws.description)}",
        "",
    ]
    for item in ws.items:
        lines.append("[[items]]")
        lines.append(f"key = {_toml_string(item.key)}")
        lines.append(f"title = {_toml_string(item.title)}")
        lines.append(f"added = {_toml_string(item.added)}")
        lines.append("")
    return "\n".join(lines)


def _write_atomic(path: Path, content: str) -> None:
    """Write content through a same-directory temp file and atomic replace."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_workspace(ws: Workspace) -> None:
    path = _workspace_path(ws.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, _manifest_text(ws))


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


def _list_warning(path: Path, exc: Exception) -> str | None:
    """Describe why a workspace directory was skipped; None for non-workspaces."""
    if path.is_symlink():
        return f"skipping workspace '{path.name}': symbolic links are not allowed"
    manifest = path / "workspace.toml"
    if manifest.is_symlink():
        return f"skipping workspace '{path.name}': manifest is a symbolic link"
    if not manifest.exists():
        return None
    return f"skipping unreadable workspace '{path.name}': {exc}"


def list_workspaces() -> list[Workspace]:
    ws_dir = _workspace_root()
    if not ws_dir.exists():
        return []
    result = []
    warnings: list[str] = []
    for path in sorted(ws_dir.iterdir()):
        if not path.is_dir():
            continue
        try:
            result.append(load_workspace(path.name))
        except Exception as exc:
            warning = _list_warning(path, exc)
            if warning is not None:
                warnings.append(warning)
    for warning in warnings:
        sys.stderr.write(f"Warning: {warning}\n")
    if warnings:
        sys.stderr.flush()
    return result


def delete_workspace(name: str) -> None:
    path = workspace_dir(name)
    if not path.exists():
        raise FileNotFoundError(f"Workspace '{name}' not found")
    shutil.rmtree(path)


def workspace_exists(name: str) -> bool:
    return _workspace_path(name).exists()
