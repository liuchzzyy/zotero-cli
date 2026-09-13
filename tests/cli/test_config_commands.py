import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from zotero_cli import __version__
from zotero_cli.cli import main


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output


def test_config_init(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    result = runner.invoke(
        main,
        ["config", "init", "--config-path", str(config_path)],
        input="12345\nmy-api-key\n\n",
    )
    assert result.exit_code == 0
    assert config_path.exists()
    content = config_path.read_text()
    assert "12345" in content
    assert "my-api-key" in content


def test_config_show(tmp_path):
    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    config_path.write_text('[zotero]\nlibrary_id = "123"\napi_key = "abc"\n')
    result = runner.invoke(main, ["config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 0
    assert "123" in result.output


def test_config_show_json_envelope(tmp_path):
    import json

    runner = CliRunner()
    config_path = tmp_path / "config.toml"
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    (data_dir / "zotero.sqlite").write_text("", encoding="utf-8")
    config_path.write_text(
        f'[zotero]\nlibrary_id = "123"\napi_key = "abcdef"\ndata_dir = "{data_dir.as_posix()}"\n',
        encoding="utf-8",
    )
    result = runner.invoke(main, ["--json", "config", "show", "--config-path", str(config_path)])
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["data"]["api_key_set"] is True
    assert env["data"]["api_key_tail"] == "cdef"
    assert env["data"]["database_ok"] is True


def test_cache_list_empty(tmp_path):
    cache = MagicMock()
    cache.entries.return_value = []
    with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "cache", "list"])
        assert result.exit_code == 0
        assert "Cache is empty." in result.output


def test_cache_list_populated(tmp_path):
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
        runner = CliRunner()
        result = runner.invoke(main, ["config", "cache", "list"])
        assert result.exit_code == 0
        assert "paper1.pdf" in result.output
        assert "abcdef123456" in result.output


def test_cache_list_json(tmp_path):
    cache = MagicMock()
    entry = {
        "source_name": "paper2.pdf",
        "source_sha256": "0123456789abcdef",
        "mineru_model_version": "vlm",
        "parsed_at": "2024-06-01T08:00:00+00:00",
    }
    cache.entries.return_value = [entry]
    with patch("zotero_cli.core.mineru.MinerUParseCache", return_value=cache):
        runner = CliRunner()
        result = runner.invoke(main, ["--json", "config", "cache", "list"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data == [entry]


def test_cache_list_reports_initialization_error_without_secondary_exception():
    from unittest.mock import patch

    runner = CliRunner()
    with patch(
        "zotero_cli.core.mineru.MinerUParseCache",
        side_effect=OSError("cache unavailable"),
    ):
        result = runner.invoke(main, ["config", "cache", "list"])
    assert result.exit_code == 1
    assert "cache unavailable" in result.output
