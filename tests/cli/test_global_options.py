from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main


def test_no_interaction_flag_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["--no-interaction", "--help"])
    assert result.exit_code == 0


def test_delete_no_interaction_skips_prompt():
    """--no-interaction should auto-confirm delete without prompting."""
    runner = CliRunner()
    # Will fail at credentials check, but should NOT prompt for confirmation
    result = runner.invoke(main, ["--no-interaction", "delete", "FAKEKEY"])
    assert "Cancelled" not in result.output


def test_config_init_no_interaction_succeeds_with_auto_detect(tmp_path):
    """--no-interaction + config init without args should succeed with auto-detected values."""
    runner = CliRunner()
    result = runner.invoke(main, ["--no-interaction", "config", "init", "--config-path", str(tmp_path / "config.toml")])
    assert result.exit_code == 0
    assert "saved" in result.output.lower()
    assert (tmp_path / "config.toml").exists()


def test_config_init_no_interaction_with_options(tmp_path):
    """--no-interaction + config init with all args should succeed."""
    config_file = tmp_path / "config.toml"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--no-interaction",
            "config",
            "init",
            "--config-path",
            str(config_file),
            "--library-id",
            "123",
            "--api-key",
            "abc",
        ],
    )
    assert "saved" in result.output.lower()
    assert config_file.exists()


def test_detail_minimal_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--detail", "minimal", "--help"])
    assert result.exit_code == 0


def test_detail_invalid_value():
    runner = CliRunner()
    result = runner.invoke(main, ["--detail", "invalid", "search", "test"])
    assert result.exit_code != 0


def test_profile_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--profile", "test", "--help"])
    assert result.exit_code == 0


def test_cache_clear_command(tmp_path):
    with patch("zotero_cli.core.mineru.MinerUParseCache") as cache_cls:
        cache_cls.return_value.clear.return_value = 0
        runner = CliRunner()
        result = runner.invoke(main, ["config", "cache", "clear"])
        assert result.exit_code == 0


def test_cache_stats_command(tmp_path):
    with patch("zotero_cli.core.mineru.MinerUParseCache") as cache_cls:
        cache_cls.return_value.stats.return_value = {"entries": 0, "total_bytes": 0}
        runner = CliRunner()
        result = runner.invoke(main, ["config", "cache", "stats"])
        assert result.exit_code == 0
        assert "Cached PDFs" in result.output


def test_profile_list_no_profiles():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "profile", "list"])
    # Either shows profiles or says none configured
    assert result.exit_code == 0
