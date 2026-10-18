"""Tests for the zot config command group (init/show/profile/cache)."""

from __future__ import annotations

import json
import os
import stat
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from zotero_cli import __version__
from zotero_cli.cli import main
from zotero_cli.config import read_config_data

RICH_CONFIG = """
[default]
profile = "lab"

[profile.personal]
library_id = "111"
api_key = "aaa"

[profile.lab]
data_dir = "/old/data"
library_id = "222"
api_key = "old-key"
semantic_scholar_api_key = "s2-key"

[profile.lab.output]
default_format = "json"
limit = 25

[ai_notes]
api_key = "ai-key"
model = "file-model"

[pdf]
mineru_token = "mineru-token"

[semantic_search]
candidate_k = 12
top_k = 5
rrf_k = 30
"""


def _invoke(args: list[str], *, env: dict[str, str] | None = None, input: str | None = None):
    return CliRunner().invoke(main, args, env={"ZOT_FORMAT": "table", **(env or {})}, input=input)


def test_cli_version():
    result = _invoke(["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help():
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_config_init(tmp_path):
    config_path = tmp_path / "config.toml"
    result = _invoke(
        ["config", "init", "--config-path", str(config_path)],
        input="12345\nmy-api-key\n\n",
    )
    assert result.exit_code == 0
    assert config_path.exists()
    content = config_path.read_text()
    assert "12345" in content
    assert "my-api-key" in content


def test_config_init_json_envelope(tmp_path):
    config_path = tmp_path / "config.toml"
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    result = _invoke(
        [
            "--json",
            "config",
            "init",
            "--config-path",
            str(config_path),
            "--library-id",
            "123",
            "--api-key",
            "super-secret-key",
            "--data-dir",
            str(data_dir),
        ]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"]["library_id"] == "123"
    assert envelope["data"]["api_key_set"] is True
    assert "super-secret-key" not in result.output
    assert config_path.exists()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_config_init_preserves_profile_sections_and_backs_up(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    result = _invoke(
        [
            "--no-interaction",
            "config",
            "init",
            "--config-path",
            str(config_path),
            "--library-id",
            "999",
            "--api-key",
            "new-key",
            "--data-dir",
            str(tmp_path),
        ]
    )
    assert result.exit_code == 0, result.output

    data = read_config_data(config_path)
    assert data["default"]["profile"] == "lab"
    assert data["profile"]["lab"]["library_id"] == "999"
    assert data["profile"]["lab"]["api_key"] == "new-key"
    # Never drop credentials or unrelated sections.
    assert data["profile"]["lab"]["semantic_scholar_api_key"] == "s2-key"
    assert data["profile"]["personal"] == {"library_id": "111", "api_key": "aaa"}
    assert data["ai_notes"] == {"api_key": "ai-key", "model": "file-model"}
    assert data["pdf"] == {"mineru_token": "mineru-token"}
    assert data["semantic_search"] == {"candidate_k": 12, "top_k": 5, "rrf_k": 30}
    assert data["profile"]["lab"]["output"] == {"default_format": "json", "limit": 25}

    backup = tmp_path / "config.toml.bak"
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_config_init_keeps_existing_credentials_when_not_passed(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")

    result = _invoke(["--no-interaction", "config", "init", "--config-path", str(config_path), "--library-id", "888"])
    assert result.exit_code == 0, result.output
    data = read_config_data(config_path)
    assert data["profile"]["lab"]["library_id"] == "888"
    assert data["profile"]["lab"]["api_key"] == "old-key"


def test_config_init_dry_run_writes_nothing(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    result = _invoke(
        [
            "--json",
            "config",
            "init",
            "--dry-run",
            "--config-path",
            str(config_path),
            "--library-id",
            "777",
        ]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["dry_run"] is True
    assert "profile.lab.library_id" in envelope["data"]["changed"]
    assert config_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "config.toml.bak").exists()


def test_config_show(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[zotero]\nlibrary_id = "123"\napi_key = "abc"\n')
    result = _invoke(["config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 0
    assert "123" in result.output


def test_config_show_json_envelope(tmp_path):
    config_path = tmp_path / "config.toml"
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    (data_dir / "zotero.sqlite").write_text("", encoding="utf-8")
    config_path.write_text(
        f'[zotero]\nlibrary_id = "123"\napi_key = "abcdef"\ndata_dir = "{data_dir.as_posix()}"\n',
        encoding="utf-8",
    )
    result = _invoke(["--json", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["api_key_set"] is True
    assert env["data"]["api_key_tail"] == "cdef"
    assert env["data"]["database_ok"] is True


def test_config_show_honors_profile_flag(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "--profile", "lab", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["library_id"] == "222"
    assert data["profile"] == "lab"


def test_config_show_honors_zot_profile_env(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(
        ["--json", "config", "show", "--config-path", str(config_path)],
        env={"ZOT_PROFILE": "personal"},
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["library_id"] == "111"
    assert data["profile"] == "personal"


def test_config_show_explicit_missing_path_is_typed_error(tmp_path):
    result = _invoke(
        ["--json", "config", "show", "--config-path", str(tmp_path / "missing.toml")],
    )
    assert result.exit_code == 3, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "configuration_error"


def test_config_show_malformed_toml_is_typed_error(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[zotero\nlibrary_id = "1"\n', encoding="utf-8")
    result = _invoke(["--json", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "configuration_error"


def test_profile_list_json(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "config", "profile", "list", "--config-path", str(config_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert set(data["profiles"]) == {"lab", "personal"}
    assert data["default"] == "lab"


def test_profile_set_preserves_config_and_backs_up(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    result = _invoke(["config", "profile", "set", "personal", "--config-path", str(config_path)])
    assert result.exit_code == 0, result.output
    data = read_config_data(config_path)
    assert data["default"]["profile"] == "personal"
    assert data["ai_notes"] == {"api_key": "ai-key", "model": "file-model"}
    assert data["profile"]["lab"]["api_key"] == "old-key"
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == original
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_profile_set_json_and_dry_run(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    result = _invoke(["--json", "config", "profile", "set", "personal", "--config-path", str(config_path), "--dry-run"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["dry_run"] is True
    assert envelope["data"]["profile"] == "personal"
    assert config_path.read_text(encoding="utf-8") == original


def test_profile_set_unknown_profile_is_not_found(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "config", "profile", "set", "typo", "--config-path", str(config_path)])
    assert result.exit_code == 4, result.output
    assert json.loads(result.output)["error"]["code"] == "not_found"


class TestCacheCommands:
    def test_cache_list_empty(self):
        cache = MagicMock()
        cache.entries.return_value = []
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["config", "cache", "list"])
        assert result.exit_code == 0
        assert "Cache is empty." in result.output

    def test_cache_list_populated(self):
        cache = MagicMock()
        cache.entries.return_value = [
            {
                "source_name": "paper1.pdf",
                "source_sha256": "abcdef1234567890",
                "mineru_model_version": "vlm",
                "parsed_at": "2024-01-15T10:30:00+00:00",
            }
        ]
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["config", "cache", "list"])
        assert result.exit_code == 0
        assert "paper1.pdf" in result.output
        assert "abcdef123456" in result.output

    def test_cache_list_json(self):
        cache = MagicMock()
        entry = {
            "source_name": "paper2.pdf",
            "source_sha256": "0123456789abcdef",
            "mineru_model_version": "vlm",
            "parsed_at": "2024-06-01T08:00:00+00:00",
        }
        cache.entries.return_value = [entry]
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data == [entry]

    def test_cache_list_reports_initialization_error_without_secondary_exception(self):
        with patch(
            "zotero_cli.core.mineru.MinerUParseCache",
            side_effect=OSError("cache unavailable"),
        ):
            result = _invoke(["config", "cache", "list"])
        assert result.exit_code == 1
        assert "cache unavailable" in result.output

    def test_cache_clear_requires_yes(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear"])
        assert result.exit_code == 3, result.output
        envelope = json.loads(result.output)
        assert envelope["error"]["code"] == "confirmation_required"
        cache.clear.assert_not_called()

    def test_cache_clear_dry_run_json(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear", "--dry-run"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["dry_run"] is True
        assert envelope["data"]["entries"] == 3
        assert envelope["data"]["total_bytes"] == 1234
        assert envelope["data"]["would_remove"] == 3
        cache.clear.assert_not_called()

    def test_cache_clear_with_yes(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        cache.clear.return_value = 3
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear", "--yes"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["data"]["removed"] == 3
        cache.clear.assert_called_once_with()

    def test_cache_stats_json(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 4, "total_bytes": 5678}
        with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "stats"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data == {"entries": 4, "total_bytes": 5678}


PROFILE_CONFIG = """
[default]
profile = "main"

[profile.main]
data_dir = "/Users/your-name/Zotero"
library_id = "111"
api_key = "old-secret"

[ai_notes]
api_key = "ai-key"
model = "file-model"

[pdf]
mineru_token = "mineru-token"
"""


def test_config_init_handles_preexisting_foreign_parent(tmp_path):
    """task-31: init must never chmod a foreign/sticky parent directory (/tmp case)."""
    parent = tmp_path / "foreign"
    parent.mkdir()
    os.chmod(parent, 0o1777)
    parent_mode = stat.S_IMODE(parent.stat().st_mode)
    config_path = parent / "fix-profile.toml"
    config_path.write_text(PROFILE_CONFIG, encoding="utf-8")
    original = config_path.read_text(encoding="utf-8")

    result = _invoke(
        [
            "--json",
            "--no-interaction",
            "config",
            "init",
            "--config-path",
            str(config_path),
            "--data-dir",
            str(tmp_path / "zotero"),
            "--library-id",
            "42",
            "--api-key",
            "secret-abc",
        ]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert stat.S_IMODE(parent.stat().st_mode) == parent_mode
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    backup = parent / "fix-profile.toml.bak"
    assert backup.read_text(encoding="utf-8") == original

    data = read_config_data(config_path)
    assert data["default"]["profile"] == "main"
    assert data["profile"]["main"]["library_id"] == "42"
    assert data["profile"]["main"]["api_key"] == "secret-abc"
    # Unrelated sections and profile keys survive the merge.
    assert data["ai_notes"] == {"api_key": "ai-key", "model": "file-model"}
    assert data["pdf"] == {"mineru_token": "mineru-token"}

    # --dry-run is zero-write.
    before_dry_run = config_path.read_text(encoding="utf-8")
    dry_run = _invoke(["--json", "config", "init", "--dry-run", "--config-path", str(config_path), "--library-id", "7"])
    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.output)["dry_run"] is True
    assert config_path.read_text(encoding="utf-8") == before_dry_run
