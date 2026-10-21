import json
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main


def test_no_interaction_flag_exists():
    runner = CliRunner()
    result = runner.invoke(main, ["--no-interaction", "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "--no-interaction" in result.output


_WRITE_ENV = {"ZOT_LIBRARY_ID": "abc", "ZOT_API_KEY": "xyz"}


def test_delete_no_interaction_requires_yes():
    """对破坏性命令而言 `--no-interaction` 绝不等于 `--yes`。"""
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "--no-interaction", "zotero", "delete", "FAKEKEY"], env=_WRITE_ENV)
    assert result.exit_code == 3
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "confirmation_required"
    assert envelope["error"]["context"] == "delete"


def test_delete_no_interaction_hoisted_after_subcommand():
    """全局 `--no-interaction` 也能从子命令之后被上提。"""
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "zotero", "delete", "FAKEKEY", "--no-interaction"], env=_WRITE_ENV)
    assert result.exit_code == 3
    envelope = json.loads(result.stdout)
    assert envelope["error"]["code"] == "confirmation_required"


def test_detail_flag_hoisted_after_subcommand(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "zotero", "list", "--detail", "minimal", "--limit", "1"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent)},
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_verbose_flag_hoisted_after_subcommand():
    runner = CliRunner()
    result = runner.invoke(main, ["--json", "config", "show", "--verbose"])
    assert result.exit_code == 0, result.output


def test_config_init_no_interaction_succeeds_with_auto_detect(tmp_path):
    """`--no-interaction` 下无参数执行 config init 应使用自动探测值成功。"""
    runner = CliRunner()
    result = runner.invoke(main, ["--no-interaction", "config", "init", "--config-path", str(tmp_path / "config.toml")])
    assert result.exit_code == 0
    assert "saved" in result.output.lower()
    assert (tmp_path / "config.toml").exists()


def test_config_init_no_interaction_with_options(tmp_path):
    """`--no-interaction` 下带全部参数执行 config init 应成功。"""
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
    assert "Usage:" in result.output


def test_detail_invalid_value():
    runner = CliRunner()
    result = runner.invoke(main, ["--detail", "invalid", "zotero", "search", "test"])
    assert result.exit_code != 0


def test_profile_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--profile", "test", "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.output


def test_cache_clear_requires_yes_or_dry_run(tmp_path):
    with patch("zotero_cli.ai_pdf.MinerUParseCache") as cache_cls:
        cache_cls.return_value.stats.return_value = {"entries": 0, "total_bytes": 0}
        cache_cls.return_value.clear.return_value = 0
        runner = CliRunner()
        refused = runner.invoke(main, ["--json", "config", "cache", "clear"])
        assert refused.exit_code == 3
        assert json.loads(refused.stdout)["error"]["code"] == "confirmation_required"
        dry_run = runner.invoke(main, ["--json", "config", "cache", "clear", "--dry-run"])
        assert dry_run.exit_code == 0, dry_run.output
        assert json.loads(dry_run.stdout)["dry_run"] is True
        confirmed = runner.invoke(main, ["--json", "config", "cache", "clear", "--yes"])
        assert confirmed.exit_code == 0, confirmed.output


def test_detail_missing_value_reports_flag_error_without_swallowing_tokens(test_db_path):
    """裸 `--detail` 不得吞掉 `--json` 或子命令记号。"""
    env = {"ZOT_DATA_DIR": str(test_db_path.parent)}
    runner = CliRunner()
    for args in (
        ["--json", "zotero", "list", "--detail"],
        ["zotero", "list", "--detail", "--json"],
        ["--detail", "--json", "zotero", "list"],
    ):
        result = runner.invoke(main, args, env=env)
        assert result.exit_code == 3, (args, result.output)
        envelope = json.loads(result.stdout)
        assert envelope["error"]["code"] == "validation_error"
        message = envelope["error"]["message"]
        assert "detail" in message
        # 子命令名不得被当作错误的 --detail 值上报。
        assert "'list'" not in message, message


def test_cache_stats_command(tmp_path):
    with patch("zotero_cli.ai_pdf.MinerUParseCache") as cache_cls:
        cache_cls.return_value.stats.return_value = {"entries": 0, "total_bytes": 0}
        runner = CliRunner()
        result = runner.invoke(main, ["config", "cache", "stats"])
        assert result.exit_code == 0
        assert "Cached PDFs" in result.output


def test_profile_list_no_profiles():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "profile", "list"])
    assert result.exit_code == 0
    # 自动夹具把配置隔离到空的临时文件，因此不存在任何配置档。
    assert "No profiles configured" in result.output
