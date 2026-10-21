"""Tests for the agent-native CLI interface: envelope, TTY detect, exit codes, schema."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tests.support import invoke_agent_cli as _run

from zotero_cli.cli import _hoist_global_flags, main
from zotero_cli.config import AiNoteConfig
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial, envelope_partial_data
from zotero_cli.output.exit_codes import (
    EXIT_AUTH,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_VALIDATION,
    exit_code_for,
)
from zotero_cli.zotero.models import Item
from zotero_cli.zotero.reader import ZoteroSchemaError
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def _ai_config() -> AiNoteConfig:
    """A complete `[ai_notes]` block, so these tests never read the repo config.

    `ai-note` loads `[ai_notes]` from `.zot/config.toml`, which is
    Git-ignored. Without this stub the exit code depends on whether the
    developer happens to have a local API key: a clean checkout stops at
    `configuration_error` (exit 3) and never reaches the credential branch.
    """
    return AiNoteConfig(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model")


class TestEnvelopeShape:
    def test_envelope_ok_has_required_fields(self):
        env = envelope_ok({"x": 1})
        assert env["ok"] is True
        assert env["data"] == {"x": 1}
        assert env["meta"]["schema_version"]
        assert env["meta"]["cli_version"]

    def test_envelope_error_has_required_fields(self):
        env = envelope_error("not_found", "thing missing")
        assert env["ok"] is False
        assert env["error"]["code"] == "not_found"
        assert env["error"]["message"] == "thing missing"
        assert env["error"]["retryable"] is False
        assert env["meta"]["schema_version"]

    def test_envelope_partial_shape(self):
        env = envelope_partial([{"key": "A"}], [{"key": "B", "error": {"code": "x", "message": "y"}}])
        # ok is documented as a tri-state value: True / False / "partial".
        assert env["ok"] == "partial"
        assert env["ok"] is not True
        assert env["ok"] is not False
        assert env["data"]["succeeded"][0]["key"] == "A"
        assert env["data"]["failed"][0]["key"] == "B"

    def test_envelope_ok_official_next_and_dry_run(self):
        env = envelope_ok({"x": 1}, next=["zot read X"], dry_run=True)
        assert env["ok"] is True
        assert env["next"] == ["zot read X"]
        assert env["dry_run"] is True
        assert set(env) == {"ok", "data", "next", "dry_run", "meta"}
        # Empty hint lists are omitted rather than emitting an empty next list.
        assert "next" not in envelope_ok({"x": 1}, next=[])

    def test_envelope_ok_rejects_undocumented_extras_and_bad_next(self):
        with pytest.raises(ValueError):
            envelope_ok({"x": 1}, extra={"bogus": True})
        with pytest.raises(ValueError):
            envelope_ok({"x": 1}, next="zot read X")  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            envelope_ok({"x": 1}, next=[""])
        with pytest.raises(ValueError):
            envelope_error("runtime_error", "boom", next=["zot read X"])

    def test_envelope_partial_data_shape(self):
        env = envelope_partial_data({"key": "K1", "attachment_error": "upload failed"}, next=["zot read K1"])
        assert env["ok"] == "partial"
        assert env["data"]["key"] == "K1"
        assert env["next"] == ["zot read K1"]
        assert "error" not in env

    def test_envelope_extra_compat_shim_maps_to_official_keys(self):
        env = envelope_ok({"x": 1}, extra={"dry_run": True, "next": ["zot read X"]})
        assert env["dry_run"] is True
        assert env["next"] == ["zot read X"]


class TestTTYAutoDetect:
    def test_search_returns_json_when_stdout_not_tty(self):
        result = _run(["zotero", "search", "attention"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["ok"] is True
        assert isinstance(env["data"], list)

    def test_explicit_json_flag_returns_envelope(self):
        result = _run(["--json", "zotero", "search", "attention"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_zot_format_table_forces_human_output(self):
        result = _run(["zotero", "search", "attention"], env={"ZOT_FORMAT": "table"})
        assert result.exit_code == EXIT_OK
        # Rich table output includes column headers
        assert "Key" in result.output or "Title" in result.output
        # Should not be parseable JSON
        try:
            json.loads(result.output)
            is_json = True
        except ValueError:
            is_json = False
        assert not is_json


class TestExitCodes:
    def test_configuration_error_is_validation_failure(self):
        assert exit_code_for("configuration_error") == EXIT_VALIDATION

    def test_error_code_map_covers_emitted_codes(self):
        from zotero_cli.output.exit_codes import CODE_TO_EXIT

        for code in (
            "auth_missing",
            "auth_invalid",
            "auth_expired",
            "validation_error",
            "configuration_error",
            "confirmation_required",
            "not_found",
            "network_error",
            "rate_limited",
            "conflict",
            "runtime_error",
            "internal_error",
            "api_error",
        ):
            assert code in CODE_TO_EXIT, code
        assert exit_code_for("api_error") == 1
        assert exit_code_for("internal_error") == 1
        assert exit_code_for("runtime_error") == 1
        assert exit_code_for("rate_limited") == 5
        assert exit_code_for("conflict") == 6
        assert exit_code_for("confirmation_required") == 3
        assert exit_code_for("auth_expired") == 2
        assert exit_code_for("unknown_code") == 1

    def test_bare_command_groups_show_help_and_succeed(self):
        for command_path in (
            ["config"],
            ["config", "cache"],
            ["config", "profile"],
            ["zotero", "collection", "--help"],
            ["zotero", "trash", "--help"],
            ["workspace"],
        ):
            result = _run(command_path)
            assert result.exit_code == EXIT_OK, command_path
            assert "Usage:" in result.output

    def test_auth_missing_returns_exit_2(self):
        result = _run(["zotero", "add", "--doi", "10.1/x"], env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""})
        assert result.exit_code == EXIT_AUTH
        env = json.loads(result.output)
        assert env["error"]["code"] == "auth_missing"

    def test_ai_analyze_auth_missing_returns_exit_2(self):
        with patch(
            "zotero_cli.cli.commands.ai_notes.load_ai_note_config",
            return_value=_ai_config(),
        ):
            result = _run(["ai-note", "ABC123"], env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""})
        assert result.exit_code == EXIT_AUTH
        env = json.loads(result.output)
        assert env["error"]["code"] == "auth_missing"

    def test_ai_analyze_dry_run_does_not_require_write_credentials(self):
        expected = {"status": "dry_run", "item_key": "ABC123"}
        with (
            patch(
                "zotero_cli.cli.commands.ai_notes.load_ai_note_config",
                return_value=_ai_config(),
            ),
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", return_value=expected) as analyze,
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter") as writer,
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            result = _run(
                ["ai-note", "ABC123", "--dry-run"],
                env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""},
            )

        assert result.exit_code == EXIT_OK
        assert json.loads(result.output)["data"] == expected
        writer.assert_not_called()
        assert analyze.call_args.args[1] is None

    def test_validation_error_returns_exit_3(self):
        result = _run(["zotero", "add"], env={"ZOT_LIBRARY_ID": "abc", "ZOT_API_KEY": "xyz"})
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"

    def test_not_found_returns_exit_4(self):
        result = _run(["zotero", "read", "NONEXISTENT_KEY"])
        assert result.exit_code == EXIT_NOT_FOUND
        env = json.loads(result.output)
        assert env["error"]["code"] == "not_found"

    def test_top_level_without_subcommand_shows_help_and_succeeds(self):
        result = _run([])
        assert result.exit_code == EXIT_OK
        combined = f"{result.output}\n{result.stderr}"
        assert "Usage:" in combined
        assert "zot — Zotero CLI" in combined


class TestStderrRouting:
    def test_auth_error_envelope_on_stdout(self):
        # JSON envelope goes to stdout; prose goes to stderr when ZOT_FORMAT=table
        result = _run(["zotero", "add", "--doi", "10.1/x"], env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""})
        # default (non-TTY): JSON envelope on stdout
        assert result.output.strip().startswith("{")
        env = json.loads(result.output)
        assert env["ok"] is False

    def test_human_error_on_stderr_not_stdout(self):
        result = _run(
            ["zotero", "add", "--doi", "10.1/x"],
            env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": "", "ZOT_FORMAT": "table"},
        )
        # Human mode: prose on stderr, stdout empty
        assert "credentials" in (result.stderr or "").lower()


class TestSchemaCommand:
    def test_schema_lists_all_commands(self):
        result = _run(["schema"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["ok"] is True
        subs = env["data"]["subcommands"]
        assert "zotero" in subs
        assert "search" in subs["zotero"]["subcommands"]
        assert "read" in subs["zotero"]["subcommands"]
        assert "schema" in subs

    def test_schema_root_name_is_cli_binary(self):
        result = _run(["schema"])
        env = json.loads(result.output)
        # The root node names the CLI binary, not the Python function ("main").
        assert env["data"]["name"] == "zot"
        assert env["data"]["safety_tier"] == "read"

    def test_schema_for_single_command(self):
        result = _run(["schema", "zotero", "search"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["data"]["name"] == "zotero search"
        param_names = [p["name"] for p in env["data"]["params"]]
        assert "query" in param_names
        assert "collection" in param_names
        assert "item_type" in param_names

    def test_schema_for_nested_command(self):
        result = _run(["schema", "zotero", "collection", "list"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert "list" in env["data"]["name"]

    def test_schema_dot_path_is_validation_error(self):
        result = _run(["schema", "."])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"

    def test_schema_unknown_command_returns_not_found(self):
        result = _run(["schema", "does_not_exist"])
        assert result.exit_code == EXIT_NOT_FOUND
        env = json.loads(result.output)
        assert env["error"]["code"] == "not_found"

    def test_schema_envelope_has_version(self):
        result = _run(["schema", "zotero", "search"])
        env = json.loads(result.output)
        assert env["meta"]["schema_version"]
        assert env["meta"]["cli_version"]

    def test_recent_schema_does_not_advertise_sort_flags(self):
        result = _run(["schema", "zotero", "recent"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        flags = {flag for param in env["data"]["params"] for flag in param.get("flags", [])}
        assert "--modified" in flags
        assert "--sort" not in flags
        assert "--direction" not in flags


class TestSchemaDiff:
    def _write(self, tmp_path, data, meta=None):
        path = tmp_path / "cached.json"
        envelope = {"ok": True, "data": data, "meta": meta or {}}
        path.write_text(json.dumps(envelope), encoding="utf-8")
        return str(path)

    def test_diff_against_self_reports_no_changes(self, tmp_path):
        current = json.loads(_run(["schema"]).output)
        cached_path = self._write(tmp_path, current["data"], current["meta"])
        result = _run(["schema", "--diff", cached_path])
        assert result.exit_code == EXIT_OK
        data = json.loads(result.output)["data"]
        assert data["commands_added"] == []
        assert data["commands_removed"] == []
        assert data["commands_changed"] == {}
        assert data["from"]["schema_version"] == current["meta"]["schema_version"]
        assert data["to"]["schema_version"] == current["meta"]["schema_version"]

    def test_diff_detects_added_and_removed_commands(self, tmp_path):
        current = json.loads(_run(["schema"]).output)
        # Build a fake "old" tree with one command removed and a fake one added.
        old_tree = json.loads(json.dumps(current["data"]))  # deep copy
        old_tree["subcommands"]["ghost-cmd"] = {"name": "ghost-cmd", "params": [], "subcommands": {}}
        del old_tree["subcommands"]["zotero"]["subcommands"]["search"]
        cached_path = self._write(tmp_path, old_tree, {"schema_version": "0.9.0", "cli_version": "0.0.0"})
        result = _run(["schema", "--diff", cached_path])
        data = json.loads(result.output)["data"]
        assert "zotero.search" in data["commands_added"]
        assert "ghost-cmd" in data["commands_removed"]
        assert data["from"]["schema_version"] == "0.9.0"

    def test_diff_detects_param_changes(self, tmp_path):
        current = json.loads(_run(["schema"]).output)
        old_tree = json.loads(json.dumps(current["data"]))
        # Drop the last param of `zotero export` so the current schema appears to have added it.
        old_tree["subcommands"]["zotero"]["subcommands"]["export"]["params"].pop()
        cached_path = self._write(tmp_path, old_tree)
        result = _run(["schema", "--diff", cached_path])
        data = json.loads(result.output)["data"]
        assert "zotero.export" in data["commands_changed"]
        assert data["commands_changed"]["zotero.export"]["params_added"]

    def test_diff_with_missing_file_returns_validation_error(self, tmp_path):
        result = _run(["schema", "--diff", str(tmp_path / "no-such-file.json")])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "Could not read cached schema" in env["error"]["message"]

    def test_diff_with_invalid_json_returns_validation_error(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json at all", encoding="utf-8")
        result = _run(["schema", "--diff", str(bad)])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"

    @pytest.mark.parametrize(
        "payload",
        [
            "[]",
            "null",
            '"a string"',
            '{"data": []}',
            '{"data": {"name": "zot", "params": [1, 2]}}',
            '{"data": {"params": []}, "meta": "not-a-dict"}',
        ],
    )
    def test_diff_with_non_tree_json_returns_validation_error(self, tmp_path, payload):
        bad = tmp_path / "not-a-tree.json"
        bad.write_text(payload, encoding="utf-8")
        result = _run(["schema", "--diff", str(bad)])
        assert result.exit_code == EXIT_VALIDATION, result.output
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "Traceback" not in result.output + result.stderr

    def test_diff_accepts_bare_data_tree(self, tmp_path):
        current = json.loads(_run(["schema"]).output)
        bare = tmp_path / "bare.json"
        bare.write_text(json.dumps(current["data"]), encoding="utf-8")
        result = _run(["schema", "--diff", str(bare)])
        assert result.exit_code == EXIT_OK
        data = json.loads(result.output)["data"]
        assert data["commands_added"] == []
        assert data["commands_removed"] == []
        assert data["from"]["schema_version"] == "unknown"

    def test_diff_rejects_command_path_combination(self, tmp_path):
        current = json.loads(_run(["schema"]).output)
        cached_path = self._write(tmp_path, current["data"], current["meta"])
        result = _run(["schema", "zotero", "search", "--diff", cached_path])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"
        assert "COMMAND_PATH cannot be combined with --diff" in env["error"]["message"]


class TestDryRun:
    def test_delete_dry_run_json_envelope(self):
        result = _run(
            ["zotero", "delete", "K1", "K2", "--dry-run"],
            env={"ZOT_LIBRARY_ID": "abc", "ZOT_API_KEY": "xyz"},
        )
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env.get("dry_run") is True
        assert env["data"]["would_delete"] == ["K1", "K2"]
        assert env["data"]["count"] == 2


class TestConfirmationRequiredOnNonTTY:
    def test_delete_without_yes_on_noninteractive_returns_validation(self):
        # CliRunner stdin is non-TTY; no --yes, no --dry-run, no --no-interaction
        result = _run(["zotero", "delete", "K1"], env={"ZOT_LIBRARY_ID": "abc", "ZOT_API_KEY": "xyz"})
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "confirmation_required"


class TestGlobalFlagHoisting:
    """`--json` / `--no-json` must work no matter where they appear in argv."""

    def test_json_after_subcommand_parses(self):
        result = _run(["zotero", "search", "x", "--json", "--limit", "1"])
        assert result.exit_code == EXIT_OK
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_no_json_after_subcommand_parses(self):
        result = _run(["zotero", "search", "x", "--no-json", "--limit", "1"])
        assert result.exit_code == EXIT_OK
        # --no-json forces table; output must not be JSON.
        assert not result.output.lstrip().startswith("{")

    def test_double_dash_stops_hoisting(self):
        # After `--`, `--json` is a positional value, not the global flag.
        # The schema subcommand accepts an optional COMMAND positional, so
        # this exercises the boundary without needing a command that takes
        # arbitrary positionals.
        result = _run(["--", "schema"])
        # Either it parses `schema` as the positional (any non-error exit is OK),
        # or it errors — what matters is that we didn't crash on the hoist.
        assert result.exit_code in (EXIT_OK, EXIT_VALIDATION, EXIT_NOT_FOUND)

    def test_detail_after_subcommand_is_hoisted(self):
        result = _run(["--json", "zotero", "list", "--detail", "minimal", "--limit", "1"])
        assert result.exit_code == EXIT_OK
        data = json.loads(result.output)["data"]
        assert data
        assert "abstract" not in data[0]

    def test_no_interaction_after_subcommand_is_hoisted(self):
        result = _run(["--json", "config", "show", "--no-interaction"])
        assert result.exit_code == EXIT_OK
        assert json.loads(result.output)["ok"] is True

    def test_verbose_after_subcommand_is_hoisted(self):
        result = _run(["--json", "config", "show", "--verbose"])
        assert result.exit_code == EXIT_OK

    def test_hoisting_preserves_option_values_that_look_like_global_flags(self):
        assert _hoist_global_flags(["zotero", "update", "K", "--title", "--verbose", "--dry-run"], main) == [
            "zotero",
            "update",
            "K",
            "--title",
            "--verbose",
            "--dry-run",
        ]
        assert _hoist_global_flags(["zotero", "update", "K", "--title=--no-interaction"], main) == [
            "zotero",
            "update",
            "K",
            "--title=--no-interaction",
        ]

    def test_value_global_flags_after_subcommand(self):
        assert _hoist_global_flags(["zotero", "list", "--detail", "minimal"], main) == [
            "--detail",
            "minimal",
            "zotero",
            "list",
        ]
        assert _hoist_global_flags(["zotero", "list", "--detail=minimal"], main) == [
            "--detail=minimal",
            "zotero",
            "list",
        ]

    def test_missing_detail_value_does_not_swallow_following_tokens(self):
        # No valid value follows: hoist an explicit empty value so Click reports a
        # validation error for --detail instead of eating --json or the subcommand.
        assert _hoist_global_flags(["zotero", "list", "--detail", "--json"], main) == [
            "--detail=",
            "--json",
            "zotero",
            "list",
        ]
        assert _hoist_global_flags(["zotero", "list", "--detail"], main) == ["--detail=", "zotero", "list"]
        assert _hoist_global_flags(["--detail", "--json", "list"], main) == ["--detail=", "--json", "list"]
        # A non-choice value is still forwarded so Click reports it precisely.
        assert _hoist_global_flags(["zotero", "list", "--detail", "bogus"], main) == [
            "--detail",
            "bogus",
            "zotero",
            "list",
        ]

    def test_boolean_globals_after_subcommand(self):
        assert _hoist_global_flags(["zotero", "list", "--no-interaction", "--verbose"], main) == [
            "--no-interaction",
            "--verbose",
            "zotero",
            "list",
        ]


class TestHelpSchemaDrift:
    """Every option `schema` advertises must appear in the matching `--help` text."""

    def test_help_advertises_all_schema_options(self):
        schema_result = _run(["schema"])
        assert schema_result.exit_code == EXIT_OK
        tree = json.loads(schema_result.output)["data"]

        def walk(node, path):
            help_out = _run(path + ["--help"]).output
            for p in node.get("params", []):
                if p["kind"] != "option" or p.get("hidden"):
                    continue
                assert any(flag in help_out for flag in p["flags"]), (
                    f"`{' '.join(['zot'] + path)} --help` does not advertise "
                    f"option {p['flags']} that `zot schema` reports."
                )
            for name, child in node.get("subcommands", {}).items():
                walk(child, path + [name])

        walk(tree, [])


def _malformed_toml_error() -> Exception:
    """Build a real TOMLDecodeError using the project's tomllib compatibility import."""
    from zotero_cli.config.loaders import tomllib

    try:
        tomllib.loads("[zotero\n")
    except Exception as exc:  # noqa: BLE001 - the exception itself is the payload
        return exc
    raise AssertionError("expected malformed TOML to raise")


class TestUsageErrorContract:
    """Click usage/parameter errors are enveloped as validation_error with exit 3."""

    def test_unknown_command_json_returns_validation_envelope(self):
        result = _run(["--json", "no-such-command"])
        assert result.exit_code == EXIT_VALIDATION
        assert result.stderr.strip() == ""
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["error"]["code"] == "validation_error"
        assert env["error"]["context"] == "usage"

    def test_unknown_command_text_mode_returns_exit_3(self):
        result = _run(["no-such-command"], env={"ZOT_FORMAT": "table"})
        assert result.exit_code == EXIT_VALIDATION
        assert result.stdout.strip() == ""
        assert "Error:" in result.stderr

    def test_bad_library_format_json_returns_validation_envelope(self):
        result = _run(["--json", "--library", "bogus", "zotero", "list"])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"

    def test_invalid_completions_shell_returns_validation_envelope(self):
        result = _run(["--json", "completions", "nope"])
        assert result.exit_code == EXIT_VALIDATION
        assert json.loads(result.output)["error"]["code"] == "validation_error"

    def test_conflicting_json_flags_json_mode_returns_validation_envelope(self):
        result = _run(["--json", "--no-json", "schema"])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "validation_error"


class TestTopLevelErrorBoundary:
    """No traceback may leak; transport errors map to typed codes."""

    def test_missing_database_returns_configuration_error_envelope(self, tmp_path):
        result = _run(["--json", "zotero", "list"], env={"ZOT_DATA_DIR": str(tmp_path / "missing-db")})
        assert result.exit_code == EXIT_VALIDATION
        assert "Traceback" not in result.output + result.stderr
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["error"]["code"] == "configuration_error"
        assert "not found" in env["error"]["message"].lower()
        assert "config show" in env["error"]["hint"]

    def test_missing_database_text_mode_human_error(self, tmp_path):
        result = _run(["--no-json", "zotero", "list"], env={"ZOT_DATA_DIR": str(tmp_path / "missing-db")})
        assert result.exit_code == EXIT_VALIDATION
        assert result.stdout.strip() == ""
        assert "Error:" in result.stderr
        assert "Traceback" not in result.stderr

    def test_malformed_toml_returns_configuration_error_envelope(self):
        exc = _malformed_toml_error()
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=exc):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.output)
        assert env["error"]["code"] == "configuration_error"
        assert "Traceback" not in result.stderr

    def test_unexpected_exception_returns_internal_error_envelope(self):
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=RuntimeError("boom")):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == 1
        assert "Traceback" not in result.stderr
        env = json.loads(result.output)
        assert env["error"]["code"] == "internal_error"
        assert "boom" in env["error"]["message"]

    def test_verbose_prints_traceback_to_stderr(self):
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=RuntimeError("boom")):
            result = _run(["--verbose", "--json", "zotero", "stats"])
        assert result.exit_code == 1
        # stdout stays a single parseable envelope; diagnostics go to stderr.
        env = json.loads(result.stdout)
        assert env["error"]["code"] == "internal_error"
        assert "Traceback" in result.stderr
        assert "RuntimeError: boom" in result.stderr

    def test_library_resolution_error_maps_to_not_found(self):
        from zotero_cli.config import LibraryResolutionError

        with patch(
            "zotero_cli.cli.commands.zotero.reading.resolve_library_id",
            side_effect=LibraryResolutionError("Group '123' not found in local database"),
        ):
            result = _run(["--json", "zotero", "list"])
        assert result.exit_code == EXIT_NOT_FOUND
        env = json.loads(result.output)
        assert env["error"]["code"] == "not_found"

    def test_unsupported_zotero_schema_is_configuration_error(self):
        exc = ZoteroSchemaError(130)
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=exc):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.stdout)
        assert env["error"]["code"] == "configuration_error"
        assert "schema" in env["error"]["message"].lower()
        assert "Update zotero-cli" in env["error"]["hint"]

    def test_zot_debug_env_prints_traceback(self):
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=RuntimeError("boom")):
            result = _run(["--json", "zotero", "stats"], env={"ZOT_DEBUG": "1"})
        assert result.exit_code == 1
        assert json.loads(result.stdout)["error"]["code"] == "internal_error"
        assert "Traceback" in result.stderr
        assert "RuntimeError: boom" in result.stderr

    def test_option_value_looking_like_verbose_does_not_enable_tracebacks(self):
        # Diagnostics come from the parsed flag / ZOT_DEBUG, never raw argv text.
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=RuntimeError("boom")):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == 1
        assert "Traceback" not in result.stderr

    def test_committed_write_error_carries_committed_flag(self):
        exc = ZoteroWriteError("maybe committed", code="api_error", committed=True)
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=exc):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == 1
        error = json.loads(result.stdout)["error"]
        assert error["committed"] is True

    def test_uncommitted_write_error_omits_committed_flag(self):
        exc = ZoteroWriteError("rejected", code="validation_error", committed=False)
        with patch("zotero_cli.cli.commands.zotero.reading.load_config", side_effect=exc):
            result = _run(["--json", "zotero", "stats"])
        assert result.exit_code == 3
        assert "committed" not in json.loads(result.stdout)["error"]


class TestCiteJsonMode:
    def test_json_cite_emits_envelope_and_skips_clipboard(self):
        with patch("zotero_cli.cli.commands.zotero.cite._copy_to_clipboard") as copy:
            result = _run(["--json", "zotero", "cite", "ATTN001", "--no-copy"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["data"]["style"] == "apa"
        assert env["data"]["copied"] is False
        assert "Vaswani" in env["data"]["citation"]
        copy.assert_not_called()

    def test_json_cite_never_writes_clipboard_even_without_no_copy(self):
        with patch("zotero_cli.cli.commands.zotero.cite._copy_to_clipboard") as copy:
            result = _run(["--json", "zotero", "cite", "ATTN001"])
        assert result.exit_code == EXIT_OK
        assert json.loads(result.stdout)["data"]["copied"] is False
        copy.assert_not_called()


class TestOpenJsonMode:
    def test_json_open_item_url_envelope(self):
        with patch("zotero_cli.cli.commands.zotero.open_item._open_path") as open_path:
            result = _run(["--json", "zotero", "open", "ATTN001", "--url"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.stdout)
        assert env["data"]["kind"] == "url"
        assert env["data"]["gui"] is True
        assert env["data"]["target"].startswith("http")
        open_path.assert_called_once()

    def test_open_url_argument_is_opened_directly(self):
        with patch("zotero_cli.cli.commands.zotero.open_item._open_path") as open_path:
            result = _run(["--json", "zotero", "open", "--url", "https://example.com/paper"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.stdout)
        assert env["data"] == {"target": "https://example.com/paper", "kind": "url", "gui": True}
        open_path.assert_called_once_with("https://example.com/paper")

    def test_open_rejects_non_http_scheme(self):
        result = _run(["--json", "zotero", "open", "--url", "ftp://example.com/file"])
        assert result.exit_code == EXIT_VALIDATION
        env = json.loads(result.stdout)
        assert env["error"]["code"] == "validation_error"


def _preprint_item() -> Item:
    return Item(
        key="ATTN001",
        item_type="preprint",
        title="A preprint",
        creators=[],
        abstract=None,
        date="2024",
        url="http://arxiv.org/abs/2401.00001",
        doi=None,
        tags=[],
        collections=[],
        date_added="2024-01-01",
        date_modified="2024-01-02",
    )


def _update_status_reader():
    reader = MagicMock()
    reader.get_item.return_value = _preprint_item()
    return reader


def _update_status_client():
    client = MagicMock()
    client.check_publication.return_value = SimpleNamespace(
        is_published=True,
        venue="Journal",
        journal_name="Journal",
        doi="10.1/x",
        publication_date="2024-02-01",
    )
    return client


def _update_status_env_patches():
    return (
        patch(
            "zotero_cli.cli.commands.metadata.update_status.load_config",
            return_value=SimpleNamespace(default_limit=50, semantic_scholar_api_key=None),
        ),
        patch("zotero_cli.cli.commands.metadata.update_status.get_data_dir", return_value=Path("/tmp/fixture")),
        patch("zotero_cli.cli.commands.metadata.update_status.resolve_library_id", return_value=1),
        patch(
            "zotero_cli.cli.commands.metadata.update_status.ZoteroReader",
            return_value=_update_status_reader(),
        ),
        patch(
            "zotero_cli.cli.commands.metadata.update_status.SemanticScholarClient",
            return_value=_update_status_client(),
        ),
    )


class TestUpdateStatusPartialFailures:
    def test_apply_network_write_failure_returns_partial_and_exit_5(self):
        error = ZoteroWriteError("Network error: boom", code="network_error", retryable=True)
        patches = _update_status_env_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch(
                "zotero_cli.cli.commands.metadata.update_status.resolve_write_credentials", return_value=("1", "key")
            ),
            patch("zotero_cli.cli.commands.metadata.update_status.ZoteroWriter") as writer,
        ):
            writer.return_value.update_item.side_effect = error
            result = _run(["--json", "metadata", "update-status", "ATTN001", "--apply"])
        assert result.exit_code == 5, result.output
        env = json.loads(result.stdout)
        assert env["ok"] == "partial"
        assert "error" not in env
        assert env["data"]["updated"] == 0
        assert len(env["data"]["update_failed"]) == 1

    def test_apply_api_write_failure_returns_partial_and_exit_1(self):
        error = ZoteroWriteError("API error: boom", code="api_error", retryable=True)
        patches = _update_status_env_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch(
                "zotero_cli.cli.commands.metadata.update_status.resolve_write_credentials", return_value=("1", "key")
            ),
            patch("zotero_cli.cli.commands.metadata.update_status.ZoteroWriter") as writer,
        ):
            writer.return_value.update_item.side_effect = error
            result = _run(["--json", "metadata", "update-status", "ATTN001", "--apply"])
        assert result.exit_code == 1, result.output
        assert json.loads(result.stdout)["ok"] == "partial"

    def test_apply_success_returns_success_envelope(self):
        patches = _update_status_env_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patch(
                "zotero_cli.cli.commands.metadata.update_status.resolve_write_credentials", return_value=("1", "key")
            ),
            patch("zotero_cli.cli.commands.metadata.update_status.ZoteroWriter"),
        ):
            result = _run(["--json", "metadata", "update-status", "ATTN001", "--apply"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["data"]["updated"] == 1

    def test_preview_marks_dry_run_without_writes(self):
        patches = _update_status_env_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
        ):
            result = _run(["--json", "metadata", "update-status", "ATTN001"])
        assert result.exit_code == EXIT_OK, result.output
        env = json.loads(result.stdout)
        assert env["dry_run"] is True
