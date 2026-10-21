"""Tests for the completions command: bounded subprocess and deterministic fallback."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main

_SNIPPET = 'eval "$(_ZOT_COMPLETE=bash_source zot)"'


def _invoke(shell: str = "bash"):
    return CliRunner().invoke(main, ["completions", shell])


@patch("zotero_cli.cli.completions.subprocess.run")
def test_success_prints_generated_script(mock_run):
    mock_run.return_value = SimpleNamespace(returncode=0, stdout="# generated completion\n")
    result = _invoke()
    assert result.exit_code == 0
    assert "# generated completion" in result.stdout
    assert "Warning:" not in result.stderr
    assert mock_run.call_args.kwargs["timeout"] == 10


@patch("zotero_cli.cli.completions.subprocess.run")
def test_timeout_emits_deterministic_fallback(mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd="zot", timeout=10)
    result = _invoke()
    assert result.exit_code == 0
    assert _SNIPPET in result.stdout
    assert "timed out after 10s" in result.stderr
    assert "static completion snippet" in result.stderr


@patch("zotero_cli.cli.completions.subprocess.run")
def test_missing_executable_emits_deterministic_fallback(mock_run):
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "zot")
    result = _invoke()
    assert result.exit_code == 0
    assert _SNIPPET in result.stdout
    assert "could not execute 'zot' to generate completions" in result.stderr


@patch("zotero_cli.cli.completions.subprocess.run")
def test_non_zero_exit_emits_fallback(mock_run):
    mock_run.return_value = SimpleNamespace(returncode=2, stdout="")
    result = _invoke()
    assert result.exit_code == 0
    assert _SNIPPET in result.stdout
    assert "exited with status 2" in result.stderr


@patch("zotero_cli.cli.completions.subprocess.run")
def test_empty_script_emits_fallback(mock_run):
    mock_run.return_value = SimpleNamespace(returncode=0, stdout="")
    result = _invoke()
    assert result.exit_code == 0
    assert _SNIPPET in result.stdout
    assert "produced no completion script" in result.stderr


@patch("zotero_cli.cli.completions.subprocess.run")
def test_zsh_and_fish_fallbacks_are_shell_specific(mock_run):
    mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "zot")
    zsh = _invoke("zsh")
    fish = _invoke("fish")
    assert "_ZOT_COMPLETE=zsh_source zot" in zsh.stdout
    assert "~/.zshrc" in zsh.stdout
    assert "_ZOT_COMPLETE=fish_source zot" in fish.stdout
    assert "~/.config/fish/completions/zot.fish" in fish.stdout


def _invoke_json(shell: str = "bash"):
    return CliRunner().invoke(main, ["--json", "completions", shell])


@patch("zotero_cli.cli.completions.subprocess.run")
def test_json_mode_returns_single_envelope_with_script(mock_run):
    import json

    mock_run.return_value = SimpleNamespace(returncode=0, stdout="# generated completion\n")
    result = _invoke_json()
    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["data"] == {
        "shell": "bash",
        "script": "# generated completion\n",
        "fallback": False,
        "warning": None,
    }
    assert "Warning:" not in result.stderr


@patch("zotero_cli.cli.completions.subprocess.run")
def test_json_fallback_envelope_reports_warning(mock_run):
    import json

    mock_run.side_effect = FileNotFoundError(2, "No such file or directory", "zot")
    result = _invoke_json("zsh")
    assert result.exit_code == 0
    envelope = json.loads(result.stdout)
    assert envelope["data"]["fallback"] is True
    assert "could not execute 'zot'" in envelope["data"]["warning"]
    assert "_ZOT_COMPLETE=zsh_source zot" in envelope["data"]["script"]
    # Warnings still go to stderr so stdout stays one clean envelope.
    assert "could not execute 'zot'" in result.stderr
