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
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
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
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
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
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data == [entry]

    def test_cache_list_reports_initialization_error_without_secondary_exception(self):
        with patch(
            "zotero_cli.ai_pdf.MinerUParseCache",
            side_effect=OSError("cache unavailable"),
        ):
            result = _invoke(["config", "cache", "list"])
        assert result.exit_code == 1
        assert "cache unavailable" in result.output

    def test_cache_clear_requires_yes(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear"])
        assert result.exit_code == 3, result.output
        envelope = json.loads(result.output)
        assert envelope["error"]["code"] == "confirmation_required"
        cache.clear.assert_not_called()

    def test_cache_clear_dry_run_json(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
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
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear", "--yes"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["data"]["removed"] == 3
        cache.clear.assert_called_once_with()

    def test_cache_stats_json(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 4, "total_bytes": 5678}
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
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


# --- H-07: invalid default profile fails closed ------------------------------

INVALID_DEFAULT_PROFILE_CONFIG = """
[default]
profile = "typo"

[zotero]
library_id = "legacy-999"
api_key = "legacy-secret"
"""


def test_config_show_invalid_default_profile_is_typed_error(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(INVALID_DEFAULT_PROFILE_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 3, result.output
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "configuration_error"
    assert "Unknown config profile 'typo'" in envelope["error"]["message"]


def test_profile_list_invalid_default_profile_is_typed_error(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(INVALID_DEFAULT_PROFILE_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "config", "profile", "list", "--config-path", str(config_path)])
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "configuration_error"


def test_config_init_invalid_default_profile_fails_closed_without_profile(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[default]\nprofile = "typo"\n\n[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    result = _invoke(
        ["--json", "--no-interaction", "config", "init", "--config-path", str(config_path), "--library-id", "5"]
    )
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "configuration_error"


def test_config_init_explicit_profile_repairs_invalid_default(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[default]\nprofile = "typo"\n\n[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    result = _invoke(
        [
            "--json",
            "--no-interaction",
            "--profile",
            "main",
            "config",
            "init",
            "--config-path",
            str(config_path),
            "--library-id",
            "9",
        ]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["profile"] == "main"
    data = read_config_data(config_path)
    assert data["default"]["profile"] == "main"
    assert data["profile"]["main"]["library_id"] == "9"


def test_profile_set_repairs_invalid_default_profile(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[default]\nprofile = "typo"\n\n[profile.main]\nlibrary_id = "1"\n', encoding="utf-8")
    result = _invoke(["--json", "config", "profile", "set", "main", "--config-path", str(config_path)])
    assert result.exit_code == 0, result.output
    assert read_config_data(config_path)["default"]["profile"] == "main"
    show = _invoke(["--json", "config", "show", "--config-path", str(config_path)])
    assert show.exit_code == 0, show.output
    assert json.loads(show.output)["data"]["library_id"] == "1"


def test_write_command_blocked_before_writer_when_profile_config_invalid(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(INVALID_DEFAULT_PROFILE_CONFIG, encoding="utf-8")
    monkeypatch.setattr("zotero_cli.config.paths.config_file_path", lambda start=None: config_path)

    with patch("zotero_cli.cli.commands.zotero.update.ZoteroWriter") as writer_cls:
        result = _invoke(
            ["--json", "zotero", "update", "ATTN001", "--title", "X"],
            env={"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake-key"},
        )

    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "configuration_error"
    writer_cls.assert_not_called()


# --- M-20: invalid UTF-8 config is a typed configuration error ---------------


def test_config_show_invalid_utf8_is_typed_error(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(b'[zotero]\nlibrary_id = "\xff\xfe"\n')
    result = _invoke(["--json", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 3, result.output
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "configuration_error"
    assert "not valid UTF-8" in envelope["error"]["message"]


# --- M-18/M-24: symlink write-through and no-op init -------------------------


def test_config_init_writes_through_symlink(tmp_path):
    real_dir = tmp_path / "dotfiles"
    real_dir.mkdir()
    target = real_dir / "config.toml"
    target.write_text(RICH_CONFIG, encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(target)

    result = _invoke(
        ["--json", "--no-interaction", "config", "init", "--config-path", str(link), "--library-id", "333"]
    )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert link.is_symlink()
    assert read_config_data(link)["profile"]["lab"]["library_id"] == "333"
    backup = real_dir / "config.toml.bak"
    assert backup.exists()
    assert envelope["data"]["backup_path"] == str(backup)


def test_config_init_noop_second_run_skips_rewrite(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")

    first = _invoke(
        ["--json", "--no-interaction", "config", "init", "--config-path", str(config_path), "--library-id", "222"]
    )
    assert first.exit_code == 0, first.output
    backup = tmp_path / "config.toml.bak"
    assert backup.exists()
    first_mtimes = (config_path.stat().st_mtime_ns, backup.stat().st_mtime_ns)

    second = _invoke(
        ["--json", "--no-interaction", "config", "init", "--config-path", str(config_path), "--library-id", "222"]
    )
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["data"]["backup_path"] == ""
    assert (config_path.stat().st_mtime_ns, backup.stat().st_mtime_ns) == first_mtimes


# --- M-14: config init JSON reports the effective profile --------------------


def test_config_init_json_reports_effective_profile(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(
        ["--json", "--no-interaction", "config", "init", "--config-path", str(config_path), "--library-id", "333"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["profile"] == "lab"


def test_config_init_json_reports_created_profile(tmp_path):
    config_path = tmp_path / "new.toml"
    result = _invoke(
        [
            "--json",
            "--no-interaction",
            "--profile",
            "lab",
            "config",
            "init",
            "--config-path",
            str(config_path),
            "--library-id",
            "7",
            "--api-key",
            "fake-key",
        ]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["profile"] == "lab"
    data = read_config_data(config_path)
    assert data["default"]["profile"] == "lab"
    assert data["profile"]["lab"]["library_id"] == "7"


def test_config_init_dry_run_json_reports_profile_without_backup(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(RICH_CONFIG, encoding="utf-8")
    result = _invoke(["--json", "config", "init", "--dry-run", "--config-path", str(config_path), "--library-id", "7"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["dry_run"] is True
    assert envelope["data"]["profile"] == "lab"
    assert envelope["data"]["backup_path"] == ""


# --- M-21/M-23: cache list limits and cache clear envelope -------------------


def _cache_entries(count: int) -> list[dict[str, str]]:
    return [
        {
            "source_name": f"paper{index}.pdf",
            "source_sha256": f"{index:064d}",
            "mineru_model_version": "vlm",
            "parsed_at": "2024-06-01T08:00:00+00:00",
        }
        for index in range(count)
    ]


class TestCacheListLimits:
    def test_default_limit_bounds_json_output(self):
        cache = MagicMock()
        cache.entries.return_value = _cache_entries(250)
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "list"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert len(envelope["data"]) == 100
        assert envelope["meta"]["count"] == 100
        assert envelope["meta"]["total"] == 250
        assert envelope["meta"]["limit"] == 100
        assert len(result.output) < 200_000

    def test_limit_zero_lists_all(self):
        cache = MagicMock()
        cache.entries.return_value = _cache_entries(250)
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "list", "--limit", "0"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert len(envelope["data"]) == 250
        assert envelope["meta"]["limit"] == 0

    def test_custom_limit(self):
        cache = MagicMock()
        cache.entries.return_value = _cache_entries(250)
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "list", "--limit", "7"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert len(envelope["data"]) == 7

    def test_human_output_announces_truncation(self):
        cache = MagicMock()
        cache.entries.return_value = _cache_entries(250)
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--no-json", "config", "cache", "list"])
        assert result.exit_code == 0, result.output
        assert "Showing 100 of 250" in result.output


class TestCacheClearEnvelope:
    def test_success_payload_has_no_would_remove(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        cache.clear.return_value = 3
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear", "--yes"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data == {"entries": 3, "total_bytes": 1234, "removed": 3}
        assert "would_remove" not in data

    def test_dry_run_keeps_would_remove_marker(self):
        cache = MagicMock()
        cache.stats.return_value = {"entries": 3, "total_bytes": 1234}
        with patch("zotero_cli.ai_pdf.MinerUParseCache", return_value=cache):
            result = _invoke(["--json", "config", "cache", "clear", "--dry-run"])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["dry_run"] is True
        assert envelope["data"]["would_remove"] == 3
        cache.clear.assert_not_called()
