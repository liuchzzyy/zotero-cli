"""版本检查模块测试（惰性缓存路径与可关闭加固）。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.cli.app.version_check import (
    _cache_file,
    _parse_version,
    check_for_update,
    update_check_disabled,
    upgrade_command,
)


class TestParseVersion:
    def test_simple(self):
        assert _parse_version("0.2.3") == (0, 2, 3)

    def test_major(self):
        assert _parse_version("1.0.0") == (1, 0, 0)

    def test_comparison(self):
        assert _parse_version("0.3.0") > _parse_version("0.2.3")
        assert _parse_version("0.2.3") == _parse_version("0.2.3")
        assert _parse_version("0.2.2") < _parse_version("0.2.3")


def _response(version: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps({"info": {"version": version}}).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestUpdateCheckToggle:
    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_opt_out_values(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv("ZOT_NO_UPDATE_CHECK", value)
        assert update_check_disabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
    def test_enabled_values(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv("ZOT_NO_UPDATE_CHECK", value)
        assert update_check_disabled() is False

    def test_disabled_skips_network_and_cache(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ZOT_NO_UPDATE_CHECK", "1")
        with (
            patch("zotero_cli.cli.app.version_check._cache_file") as cache_file,
            patch("zotero_cli.cli.app.version_check.urlopen") as urlopen,
        ):
            assert check_for_update("0.2.3") is None
        cache_file.assert_not_called()
        urlopen.assert_not_called()


class TestCachePath:
    def test_cache_path_resolved_lazily_from_state_dir(self, tmp_path: Path):
        state = tmp_path / "relocated-state"
        with patch("zotero_cli.cli.app.version_check._config.state_dir", return_value=state):
            assert _cache_file() == state / "version-check.json"
        state2 = tmp_path / "another-state"
        with patch("zotero_cli.cli.app.version_check._config.state_dir", return_value=state2):
            assert _cache_file() == state2 / "version-check.json"


class TestCheckForUpdate:
    @patch("zotero_cli.cli.app.version_check.urlopen")
    def test_newer_version_available(self, mock_urlopen, tmp_path: Path):
        mock_urlopen.return_value = _response("0.3.0")
        cache = tmp_path / "version-check.json"
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache):
            result = check_for_update("0.2.3")
        assert result == "0.3.0"
        cached = json.loads(cache.read_text(encoding="utf-8"))
        assert cached["latest_version"] == "0.3.0"

    @patch("zotero_cli.cli.app.version_check.urlopen")
    def test_already_latest(self, mock_urlopen, tmp_path: Path):
        mock_urlopen.return_value = _response("0.2.3")
        cache = tmp_path / "version-check.json"
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache):
            assert check_for_update("0.2.3") is None

    def test_cache_hit_newer(self, tmp_path: Path):
        cache = tmp_path / "version-check.json"
        cache.write_text(
            json.dumps({"latest_version": "0.3.0", "checked_at": time.time()}),
            encoding="utf-8",
        )
        with (
            patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache),
            patch("zotero_cli.cli.app.version_check.urlopen") as urlopen,
        ):
            assert check_for_update("0.2.3") == "0.3.0"
        urlopen.assert_not_called()

    def test_cache_hit_same(self, tmp_path: Path):
        cache = tmp_path / "version-check.json"
        cache.write_text(
            json.dumps({"latest_version": "0.2.3", "checked_at": time.time()}),
            encoding="utf-8",
        )
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache):
            assert check_for_update("0.2.3") is None

    @patch("zotero_cli.cli.app.version_check.urlopen")
    def test_cache_expired_fetches_again(self, mock_urlopen, tmp_path: Path):
        cache = tmp_path / "version-check.json"
        cache.write_text(
            json.dumps({"latest_version": "0.2.3", "checked_at": time.time() - 100000}),
            encoding="utf-8",
        )
        mock_urlopen.return_value = _response("0.3.0")
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache):
            assert check_for_update("0.2.3") == "0.3.0"

    @patch("zotero_cli.cli.app.version_check.urlopen", side_effect=Exception("network error"))
    def test_network_error_returns_none(self, mock_urlopen, tmp_path: Path):
        cache = tmp_path / "version-check.json"
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=cache):
            assert check_for_update("0.2.3") is None

    def test_unusable_cache_path_returns_none(self, tmp_path: Path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        with patch("zotero_cli.cli.app.version_check._cache_file", return_value=blocker / "version-check.json"):
            assert check_for_update("0.2.3") is None


class TestUpgradeCommand:
    def test_uv_tool_install(self):
        exe = "/Users/me/.local/share/uv/tools/zotero-cli/bin/python"
        assert upgrade_command(exe) == "uv tool upgrade zotero-cli"

    def test_pipx_install(self):
        exe = "/Users/me/.local/pipx/venvs/zotero-cli/bin/python"
        assert upgrade_command(exe) == "pipx upgrade zotero-cli"

    def test_pip_install_fallback(self):
        assert upgrade_command("/usr/bin/python3") == "pip install -U zotero-cli"
