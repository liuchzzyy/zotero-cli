"""M-22: persistent state must not depend on the current working directory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from zotero_cli.config import paths
from zotero_cli.config.paths import config_file_path as _real_config_file_path


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


# --- M-26: relative XDG/ZOT_STATE_DIR values are ignored ---------------------


def _user_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, kind: str) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    if sys.platform == "win32":
        if kind == "config":
            base = tmp_path / "appdata"
            monkeypatch.setenv("APPDATA", str(base))
        else:
            base = tmp_path / "localappdata"
            monkeypatch.setenv("LOCALAPPDATA", str(base))
        return base / "zotero-cli"
    if kind == "config":
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        return home / ".config" / "zotero-cli"
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return home / ".local" / "state" / "zotero-cli"


def test_relative_zot_state_dir_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("ZOT_STATE_DIR", "relative/state")
    expected = _user_fallback(monkeypatch, tmp_path, kind="state")
    assert paths.state_root() == expected


def test_relative_xdg_state_home_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.delenv("ZOT_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    expected = _user_fallback(monkeypatch, tmp_path, kind="state")
    assert paths.state_root() == expected


def test_tilde_zot_state_dir_is_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ZOT_STATE_DIR", "~/mystate")
    assert paths.state_root() == home / "mystate"


# --- M-25: installed config discovery is per-user, never CWD-based ------------


def test_installed_config_search_uses_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    assert _real_config_file_path() == tmp_path / "xdg-config" / "zotero-cli" / "config.toml"


def test_installed_config_ignores_relative_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _simulate_installed(monkeypatch, tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
    expected = _user_fallback(monkeypatch, tmp_path, kind="config") / "config.toml"
    assert _real_config_file_path() == expected


def test_installed_config_does_not_read_foreign_cwd_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    foreign = tmp_path / "foreign-project"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_text('[project]\nname = "foreign"\n', encoding="utf-8")
    (foreign / ".zot").mkdir()
    (foreign / ".zot" / "config.toml").write_text(
        '[zotero]\nlibrary_id = "attacker-lib"\napi_key = "attacker-key"\n', encoding="utf-8"
    )
    monkeypatch.setattr(paths, "_source_project_root", lambda: None)
    monkeypatch.chdir(foreign)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))

    resolved = _real_config_file_path()
    assert resolved == tmp_path / "xdg-config" / "zotero-cli" / "config.toml"
    assert foreign not in resolved.parents

    from zotero_cli.config import load_config

    # The installed CLI never reads the foreign project's .zot/config.toml.
    assert load_config(resolved).library_id == ""


def test_checkout_config_path_stays_repo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _real_config_file_path() == paths.project_root() / ".zot" / "config.toml"


# --- version-check test isolation -------------------------------------------


def test_version_check_cache_file_is_isolated(tmp_path: Path) -> None:
    from zotero_cli.core import version_check

    assert version_check._cache_file() == tmp_path / ".zot" / "state" / "version-check.json"
