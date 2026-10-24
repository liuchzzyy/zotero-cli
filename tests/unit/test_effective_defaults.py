"""运行策略的 TOML、覆盖顺序与可观测性契约。"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.config import ConfigError, load_ai_note_config, load_config


def test_empty_fallback_replaces_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[ai_notes]\nfallback_models = []\n")
    assert load_ai_note_config(path, apply_env_overrides=False).fallback_models == []


def test_profile_inherits_top_level_output(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[default]\nprofile = "main"\n[profile.main]\n[output]\nlimit=17\n[export]\ndefault_style="ris"\n')
    result = load_config(path)
    assert result.default_limit == 17
    assert result.default_export_style == "ris"


def test_unknown_ai_note_option_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[ai_notes]\nrequest_ceilig = 123\n")
    with pytest.raises(ConfigError, match="request_ceilig"):
        load_ai_note_config(path, apply_env_overrides=False)


def test_empty_model_chain_is_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[ai_notes]\nmodel=""\nfallback_models=[]\n')
    with pytest.raises(ConfigError, match="model"):
        load_ai_note_config(path, apply_env_overrides=False)


def test_implicit_cli_limit_respects_toml(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from zotero_cli.cli.commands.zotero import items
    from zotero_cli.zotero.models import SearchResult

    path = tmp_path / "config.toml"
    path.write_text("[output]\nlimit=17\n")
    cfg = load_config(path)
    reader = MagicMock()
    reader.search.return_value = SearchResult(items=[], total=0, query="probe")
    monkeypatch.setattr(items, "_create_reader", lambda ctx: (reader, cfg))
    result = CliRunner().invoke(main, ["--json", "zotero", "search", "probe"], env={"ZOT_NO_UPDATE_CHECK": "1"})
    assert result.exit_code == 0, result.output
    assert reader.search.call_args.kwargs["limit"] == 17


def test_config_show_exposes_effective_values_without_secrets(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[ai_notes]\napi_key="TOP_SECRET_123"\nmax_tokens=2048\n')
    result = CliRunner().invoke(main, ["--json", "config", "show", "--config-path", str(path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert "TOP_SECRET_123" not in result.output
    assert data["effective"]["ai_notes.max_tokens"]["value"] == 2048
    assert data["effective"]["ai_notes.max_tokens"]["source"] == "toml"
    assert data["effective"]["ai_notes.max_tokens"]["unit"] == "tokens"


def _flatten(data, prefix=""):
    out = {}
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        else:
            out[name] = value
    return out


def test_example_config_lists_every_built_in_default():
    import tomllib
    from pathlib import Path

    from zotero_cli.config.runtime import built_in_defaults

    example = Path(__file__).resolve().parents[2] / ".zot/config.example.toml"
    documented = _flatten(tomllib.loads(example.read_text(encoding="utf-8")))
    missing = sorted(set(_flatten(built_in_defaults())) - set(documented))
    assert not missing, missing


def test_toml_budget_override_reaches_request_preflight(tmp_path, monkeypatch):
    from zotero_cli.ai_notes import evidence
    from zotero_cli.config import runtime

    path = tmp_path / "config.toml"
    path.write_text("[ai_notes.budget]\nsafety_reserve = 1\n")
    monkeypatch.setattr(runtime, "config_file_path", lambda: path)
    runtime._cached_settings.cache_clear()
    try:
        assert evidence.SAFETY_RESERVE == 1
        assert evidence.model_supports_request("group/deepseek", "x" * 10, 0, max_tokens=0)
    finally:
        runtime._cached_settings.cache_clear()
