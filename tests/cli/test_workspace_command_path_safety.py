"""`workspace` 子命令必须在任何读写之前拒绝越界路径。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from tests.support import invoke_cli as _invoke

from zotero_cli.workspace import store as workspace

_MANIFEST = 'created = "2026-01-01"\ndescription = "outside sentinel"\n'


@pytest.fixture
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把工作区根指向临时目录，避免任何实现失误波及真实状态。"""
    assert tmp_path.is_absolute()
    root = tmp_path / ".workspace"
    root.mkdir()
    monkeypatch.setattr(workspace, "workspaces_dir", lambda: root)
    return root


def test_delete_traversal_never_reaches_rmtree(workspace_root: Path) -> None:
    victim = workspace_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "workspace.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    with patch.object(workspace.shutil, "rmtree") as delete:
        result = _invoke(["--no-json", "workspace", "delete", "../victim", "--yes"])
    assert result.exit_code != 0, result.output
    delete.assert_not_called()
    assert "Workspace deleted" not in result.output
    assert manifest.read_text(encoding="utf-8") == _MANIFEST


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
def test_every_command_rejects_outside_paths_before_io(
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
        result = _invoke(["workspace", *command], json_output=True)
        assert result.exit_code != 0, result.output
        assert "Workspace deleted" not in result.output
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST
