"""Global output flags must never steal another option's value."""

from __future__ import annotations

import importlib
import json
from unittest.mock import Mock

import click
import pytest
from click.testing import CliRunner

from zotero_cli.cli import _hoist_global_flags, main
from zotero_cli.cli.workspace import _output as workspace_output
from zotero_cli.cli.workspace import commands as workspace_commands
from zotero_cli.config import AppConfig


@pytest.mark.parametrize("value", ["--json", "--no-json"])
@pytest.mark.parametrize(("command", "option"), [("update", "--title"), ("note", "--add")])
def test_flag_like_option_values_do_not_disable_dry_run(
    monkeypatch: pytest.MonkeyPatch, command: str, option: str, value: str
) -> None:
    module_path = {
        "update": "zotero_cli.cli.zotero.update",
        "note": "zotero_cli.cli.zotero.notes",
    }[command]
    module = importlib.import_module(module_path)
    monkeypatch.setattr(module, "load_config", Mock(return_value=AppConfig()))
    writer = Mock()
    writer.return_value.add_note.return_value = "NOTE123"
    monkeypatch.setattr(module, "ZoteroWriter", writer)

    result = CliRunner().invoke(
        main,
        ["zotero"] + [command, "ABC123", option, value, "--dry-run"],
        env={"ZOT_FORMAT": "json", "ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "test-key"},
    )

    writer.assert_not_called()
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["dry_run"] is True
    if command == "update":
        assert envelope["data"]["would"]["fields"] == {"title": value}
    else:
        assert envelope["data"]["would"]["content_preview"] == value


@pytest.mark.parametrize("value", ["--json", "--no-json"])
@pytest.mark.parametrize("option", ["--description", "-d"])
def test_nested_option_values_are_preserved(monkeypatch: pytest.MonkeyPatch, option: str, value: str) -> None:
    monkeypatch.setattr(workspace_output, "workspace_exists", Mock(return_value=False))
    save = Mock()
    monkeypatch.setattr(workspace_commands, "save_workspace", save)

    result = CliRunner().invoke(main, ["workspace", "new", "flag-safety", option, value])

    assert result.exit_code == 0, result.output
    save.assert_called_once()
    assert save.call_args.args[0].description == value


@pytest.mark.parametrize("value", ["--json", "--no-json"])
@pytest.mark.parametrize(
    "args",
    [
        ["--profile", "VALUE", "schema"],
        ["zotero", "update", "ABC123", "--title", "VALUE", "--dry-run"],
        ["zotero", "note", "ABC123", "--add", "VALUE", "--dry-run"],
        ["workspace", "new", "flag-safety", "--description", "VALUE"],
        ["workspace", "new", "flag-safety", "-d", "VALUE"],
    ],
)
def test_option_values_do_not_hide_separate_output_flags(args: list[str], value: str) -> None:
    argv = [value if token == "VALUE" else token for token in args]
    for output_flag in ("--json", "--no-json"):
        assert _hoist_global_flags([*argv, output_flag], main) == [output_flag, *argv]


@pytest.mark.parametrize("value", ["--json", "--no-json"])
@pytest.mark.parametrize("prefix", ["--description=", "-d"])
def test_attached_values_do_not_consume_separate_output_flags(prefix: str, value: str) -> None:
    args = ["workspace", "new", "flag-safety", f"{prefix}{value}"]
    assert _hoist_global_flags([*args, "--json"], main) == ["--json", *args]


@pytest.mark.parametrize("output_flag", ["--json", "--no-json"])
@pytest.mark.parametrize("args", [["schema"], ["schema", "zotero", "search"], ["workspace", "--help"]])
def test_standalone_output_flags_still_follow_subcommands(args: list[str], output_flag: str) -> None:
    result = CliRunner().invoke(main, [*args, output_flag])
    assert result.exit_code == 0, result.output
    assert _hoist_global_flags([*args, output_flag], main) == [output_flag, *args]


@pytest.mark.parametrize("output_flag", ["--json", "--no-json"])
@pytest.mark.parametrize("prefix", [[], ["schema"], ["workspace", "new", "flag-safety"]])
def test_double_dash_terminates_hoisting(prefix: list[str], output_flag: str) -> None:
    args = [*prefix, "--", output_flag]
    assert _hoist_global_flags(args, main) == args


@pytest.mark.parametrize("output_flag", ["--json", "--no-json"])
def test_double_dash_leaves_flag_like_positionals_for_click(output_flag: str) -> None:
    result = CliRunner().invoke(main, ["schema", "--", output_flag], env={"ZOT_FORMAT": "json"})

    assert result.exit_code == 4, result.output
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "not_found"
    assert output_flag in envelope["error"]["message"]


@pytest.mark.parametrize("output_flag", ["--json", "--no-json"])
def test_double_dash_consumed_as_value_does_not_terminate_hoisting(output_flag: str) -> None:
    args = ["workspace", "new", "flag-safety", "--description", "--"]
    assert _hoist_global_flags([*args, output_flag], main) == [output_flag, *args]


@pytest.mark.parametrize("value", ["--json", "--no-json"])
@pytest.mark.parametrize(
    "tokens",
    [
        ["--pair", "first", "VALUE"],
        ["--pair", "VALUE", "second"],
        ["--pair=first", "VALUE"],
        ["-pfirst", "VALUE"],
        ["-vp", "first", "VALUE"],
        ["-vpfirst", "VALUE"],
        ["-vvp", "VALUE", "second"],
        ["--pair", "first", "second", "--pair", "VALUE", "last"],
    ],
)
def test_hoisting_uses_declared_click_arity(tokens: list[str], value: str) -> None:
    callback = Mock()
    leaf = click.Command(
        "mutate",
        params=[
            click.Option(["--pair", "-p"], nargs=2, multiple=True),
            click.Option(["-v"], count=True),
            click.Option(["--dry-run"], is_flag=True),
        ],
        callback=callback,
    )
    nested = click.Group("nested", commands={"mutate": leaf})
    root = click.Group(params=[click.Option(["--json"], is_flag=True)], commands={"nested": nested})
    args = ["nested", "mutate", *(value if token == "VALUE" else token for token in tokens), "--dry-run"]

    normalized = _hoist_global_flags([*args, "--json"], root)

    assert normalized == ["--json", *args]
    callback.assert_not_called()
    result = CliRunner().invoke(root, normalized)
    assert result.exit_code == 0, result.output
    assert callback.call_args.kwargs["dry_run"] is True
    assert any(value in pair for pair in callback.call_args.kwargs["pair"])
