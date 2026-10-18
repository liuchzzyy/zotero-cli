"""M-22: persistent state must not depend on the current working directory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zotero_cli.config import paths


def test_checkout_state_paths_stay_repo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    assert paths.checkout_root() is not None
    assert paths.state_root() == paths.project_root() / ".zot" / "state"
    assert paths.state_dir() == paths.state_root()
    assert paths.workspace_root() == paths.project_root() / ".workspace"
    assert paths.data_root() == paths.project_root()


def test_checkout_ignores_state_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ZOT_STATE_DIR", str(tmp_path / "override"))
    assert paths.state_root() == paths.project_root() / ".zot" / "state"


def _simulate_installed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "_source_project_root", lambda: None)
    isolated_cwd = tmp_path / "cwd"
    isolated_cwd.mkdir()
    monkeypatch.chdir(isolated_cwd)


def test_installed_state_root_uses_zot_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("ZOT_STATE_DIR", str(tmp_path / "state-root"))

    assert paths.checkout_root() is None
    assert paths.state_root() == tmp_path / "state-root"
    assert paths.state_dir() == tmp_path / "state-root"
    assert paths.data_root() == tmp_path / "state-root"
    assert paths.workspace_root() == tmp_path / "state-root" / ".workspace"


def test_installed_state_root_falls_back_to_platform_state_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.delenv("ZOT_STATE_DIR", raising=False)
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
        expected = tmp_path / "localappdata" / "zotero-cli"
    else:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
        expected = tmp_path / "xdg" / "zotero-cli"

    assert paths.state_root() == expected


def test_installed_state_root_ignores_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    first = paths.state_root()
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    assert paths.state_root() == first
