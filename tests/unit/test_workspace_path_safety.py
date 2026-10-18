"""Workspace paths must never cross their temporary, isolated root."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core import workspace_store as workspace
from zotero_cli.core.rag.index import RagIndex

_MANIFEST = 'created = "2026-01-01"\ndescription = "outside sentinel"\n'
_OPERATIONS = ("directory", "manifest", "index", "load", "exists", "save", "delete")


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep even the vulnerable implementation away from persistent state."""
    assert tmp_path.is_absolute()
    root = tmp_path / ".workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "workspaces_dir", lambda: root)
    return root


def _operate(operation: str, name: str) -> object:
    if operation == "save":
        return workspace.save_workspace(workspace.Workspace(name=name, created="2026-01-01"))
    functions = {
        "directory": workspace.workspace_dir,
        "manifest": workspace.workspace_file_path,
        "index": workspace.workspace_index_path,
        "load": workspace.load_workspace,
        "exists": workspace.workspace_exists,
        "delete": workspace.delete_workspace,
    }
    return functions[operation](name)


def test_cli_delete_traversal_never_reaches_rmtree(workspace_root: Path) -> None:
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "workspace.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    with patch.object(workspace.shutil, "rmtree") as delete:
        result = CliRunner().invoke(main, ["--no-json", "workspace", "delete", "../victim", "--yes"])
    assert result.exit_code != 0, result.output
    delete.assert_not_called()
    assert "Workspace deleted" not in result.output
    assert manifest.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize(
    "name",
    (
        "",
        ".",
        "..",
        "../victim",
        "..\\victim",
        "nested/victim",
        "nested\\victim",
        "./victim",
        "victim/",
        "C:\\victim",
        "C:victim",
        "bad\x00name",
    ),
)
def test_unsafe_names_are_rejected_at_every_path_entry(workspace_root: Path, operation: str, name: str) -> None:
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, name)
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_absolute_name_cannot_access_an_outside_workspace(workspace_root: Path, operation: str) -> None:
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "workspace.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, str(victim))
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("name", ("has spaces", "00_收件箱", "材料 研究-2026", "llm-safety"))
def test_existing_safe_names_keep_their_exact_spelling(workspace_root: Path, name: str) -> None:
    ws = workspace.Workspace(name=name, created="2026-01-01", description="preserved")
    workspace.save_workspace(ws)
    assert workspace.workspace_dir(name) == workspace_root / name
    assert workspace.workspace_file_path(name) == workspace_root / name / "workspace.toml"
    assert workspace.workspace_index_path(name) == workspace_root / name / "rag.idx.sqlite"
    idx = RagIndex(workspace.workspace_index_path(name))
    try:
        idx.insert_chunk("TESTKEY", "metadata", "path safety sentinel", 3)
        assert idx.search_bm25("sentinel")
    finally:
        idx.close()
    assert workspace.workspace_exists(name)
    assert workspace.load_workspace(name) == ws
    assert [entry.name for entry in workspace.list_workspaces()] == [name]
    with patch.object(workspace.shutil, "rmtree") as delete:
        workspace.delete_workspace(name)
        delete.assert_called_once_with(workspace_root / name)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("target_kind", ("outside", "inside", "dangling", "loop"))
def test_workspace_directory_symlinks_are_rejected(workspace_root: Path, operation: str, target_kind: str) -> None:
    link = workspace_root / "linked"
    if target_kind == "outside":
        target = workspace_root.parent / "victim"
    elif target_kind == "inside":
        target = workspace_root / "actual"
    elif target_kind == "dangling":
        target = workspace_root.parent / "missing"
    else:
        target = link
    if target_kind in {"inside", "outside"}:
        target.mkdir()
        (target / "workspace.toml").write_text(_MANIFEST, encoding="utf-8")
    link.symlink_to(target, target_is_directory=True)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "linked")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if target_kind in {"inside", "outside"}:
        assert (target / "workspace.toml").read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", (*_OPERATIONS, "list"))
@pytest.mark.parametrize("dangling", (False, True))
def test_symlinked_workspace_root_is_rejected(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch, operation: str, dangling: bool
) -> None:
    victim = workspace_root.parent / "victim"
    if not dangling:
        (victim / "safe").mkdir(parents=True)
        (victim / "safe" / "workspace.toml").write_text(_MANIFEST, encoding="utf-8")
    link = workspace_root.parent / "linked-root"
    link.symlink_to(victim, target_is_directory=True)
    monkeypatch.setattr(workspace, "workspaces_dir", lambda: link)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            workspace.list_workspaces() if operation == "list" else _operate(operation, "safe")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if not dangling:
        assert (victim / "safe" / "workspace.toml").read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", ("manifest", "load", "exists", "save"))
@pytest.mark.parametrize("target_kind", ("outside", "inside", "dangling", "loop"))
def test_manifest_symlinks_are_rejected(workspace_root: Path, operation: str, target_kind: str) -> None:
    directory = workspace_root / "safe"
    directory.mkdir()
    manifest = directory / "workspace.toml"
    target = directory / "actual.toml" if target_kind == "inside" else workspace_root.parent / "victim.toml"
    if target_kind in {"inside", "outside"}:
        target.write_text(_MANIFEST, encoding="utf-8")
    elif target_kind == "loop":
        target = manifest
    manifest.symlink_to(target)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "safe")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if target_kind in {"inside", "outside"}:
        assert target.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("suffix", ("", "-journal", "-wal", "-shm"))
@pytest.mark.parametrize("dangling", (False, True))
def test_index_and_sqlite_sidecar_symlinks_are_rejected(workspace_root: Path, suffix: str, dangling: bool) -> None:
    directory = workspace_root / "safe"
    directory.mkdir()
    victim = workspace_root.parent / "victim.sqlite"
    if not dangling:
        victim.write_bytes(b"outside SQLite sentinel")
    (directory / f"rag.idx.sqlite{suffix}").symlink_to(victim)
    with pytest.raises(ValueError):
        workspace.workspace_index_path("safe")
    if not dangling:
        assert victim.read_bytes() == b"outside SQLite sentinel"


def test_listing_skips_symlinks_without_reading_outside_the_root(workspace_root: Path) -> None:
    workspace.save_workspace(workspace.Workspace(name="safe", created="2026-01-01"))
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    (victim / "workspace.toml").write_text(_MANIFEST, encoding="utf-8")
    (workspace_root / "linked").symlink_to(victim, target_is_directory=True)
    (workspace_root / "safe" / "unrelated").symlink_to(victim, target_is_directory=True)
    real_read = Path.read_text
    reads = []

    def read_contained(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        assert path.resolve().is_relative_to(workspace_root.resolve()), f"Outside read: {path}"
        reads.append(path)
        return real_read(path, encoding=encoding, errors=errors)

    with patch.object(Path, "read_text", autospec=True, side_effect=read_contained):
        assert [entry.name for entry in workspace.list_workspaces()] == ["safe"]
    assert reads == [workspace_root / "safe" / "workspace.toml"]


@pytest.mark.parametrize(
    "arguments",
    (
        ("new", "{name}"),
        ("delete", "{name}", "--yes"),
        ("show", "{name}"),
        ("add", "{name}", "TESTKEY"),
        ("remove", "{name}", "TESTKEY"),
        ("import", "{name}", "--search", "sentinel"),
        ("export", "{name}"),
        ("search", "sentinel", "--workspace", "{name}"),
        ("index", "{name}", "--no-embed"),
        ("reindex", "{name}"),
        ("embed", "{name}"),
        ("query", "sentinel", "--workspace", "{name}"),
    ),
)
@pytest.mark.parametrize("kind", ("traversal", "directory-link", "manifest-link"))
def test_every_workspace_command_rejects_outside_paths_before_io(
    workspace_root: Path, arguments: tuple[str, ...], kind: str
) -> None:
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "workspace.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    if kind == "traversal":
        name = "../victim"
    elif kind == "directory-link":
        name = "linked"
        (workspace_root / name).symlink_to(victim, target_is_directory=True)
    else:
        name = "linked"
        (workspace_root / name).mkdir()
        (workspace_root / name / "workspace.toml").symlink_to(manifest)
    command = [argument.format(name=name) for argument in arguments]
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        result = CliRunner().invoke(main, ["--json", "workspace", *command])
        assert result.exit_code != 0, result.output
        assert "Workspace deleted" not in result.output
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", ("load", "exists", "save", "index", "delete"))
def test_real_outside_manifest_is_unchanged_by_rejected_traversal(workspace_root: Path, operation: str) -> None:
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "workspace.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    real_read = Path.read_text
    real_write = Path.write_text
    reads = []
    writes = []

    def read_contained(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        assert path.resolve().is_relative_to(workspace_root.resolve()), f"Outside read: {path}"
        reads.append(path)
        return real_read(path, encoding=encoding, errors=errors)

    def write_contained(path: Path, data: str, encoding: str | None = None, errors: str | None = None) -> int:
        assert path.resolve().is_relative_to(workspace_root.resolve()), f"Outside write: {path}"
        writes.append(path)
        return real_write(path, data, encoding=encoding, errors=errors)

    with (
        patch.object(Path, "read_text", autospec=True, side_effect=read_contained),
        patch.object(Path, "write_text", autospec=True, side_effect=write_contained),
        patch.object(workspace.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "../victim")
        assert not reads
        assert not writes
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST
    assert not (victim / "rag.idx.sqlite").exists()


@pytest.mark.parametrize("operation", ("directory", "manifest", "index", "load", "exists", "save", "list"))
def test_paths_resolve_safely_before_workspace_root_exists(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    missing_root = workspace_root.parent / "new-root"
    monkeypatch.setattr(workspace, "workspaces_dir", lambda: missing_root)
    if operation == "list":
        assert workspace.list_workspaces() == []
    elif operation == "load":
        with pytest.raises(FileNotFoundError):
            workspace.load_workspace("safe")
    elif operation == "exists":
        assert not workspace.workspace_exists("safe")
    else:
        result = _operate(operation, "safe")
        if operation == "save":
            assert workspace.load_workspace("safe").name == "safe"
        else:
            assert isinstance(result, Path)
            assert result.is_relative_to(missing_root)
            assert not result.exists()


def test_parent_alias_is_canonicalized_without_changing_workspace_names(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = workspace_root.parent / "project-alias"
    alias.symlink_to(workspace_root.parent, target_is_directory=True)
    logical_root = alias / workspace_root.name
    monkeypatch.setattr(workspace, "workspaces_dir", lambda: logical_root)
    ws = workspace.Workspace(name="研究 topic-1", created="2026-01-01")
    workspace.save_workspace(ws)
    assert workspace.workspace_dir(ws.name) == logical_root / ws.name
    assert workspace.load_workspace(ws.name) == ws
    assert [entry.name for entry in workspace.list_workspaces()] == [ws.name]
    assert (workspace_root / ws.name / "workspace.toml").is_file()


@pytest.mark.parametrize("target", ("self", "sibling"))
def test_resolved_path_cannot_equal_or_leave_workspace_root(workspace_root: Path, target: str) -> None:
    escaped = workspace_root if target == "self" else workspace_root.with_name(workspace_root.name + "-victim")
    child = workspace_root / "safe"
    real_resolve = Path.resolve

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        return escaped if path == child else real_resolve(path)

    with patch.object(Path, "resolve", autospec=True, side_effect=resolve):
        with pytest.raises(ValueError, match="inside"):
            workspace.workspace_dir("safe")


@pytest.mark.parametrize("error", (OSError("unresolvable path"), RuntimeError("symlink loop")))
def test_unresolvable_paths_fail_closed(workspace_root: Path, error: Exception) -> None:
    with patch.object(Path, "resolve", side_effect=error):
        with pytest.raises(ValueError, match="unable to resolve") as caught:
            workspace.workspace_dir("safe")
    assert caught.value.__cause__ is error
