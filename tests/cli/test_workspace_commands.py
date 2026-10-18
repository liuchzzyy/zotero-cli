"""Tests for workspace feature."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from tests.support import invoke_cli as _invoke

from zotero_cli.core.workspace_store import (
    Workspace,
    WorkspaceItem,
    delete_workspace,
    list_workspaces,
    load_workspace,
    mineru_cache_dir,
    save_workspace,
    validate_name,
    workspace_exists,
)

# --- Core unit tests ---


class TestValidateName:
    def test_valid_names(self):
        assert validate_name("llm-safety") is True
        assert validate_name("protein-folding") is True
        assert validate_name("topic1") is True
        assert validate_name("a") is True
        assert validate_name("my-long-workspace-name") is True

    def test_invalid_names(self):
        assert validate_name("") is False
        assert validate_name("LLM-Safety") is False
        assert validate_name("has spaces") is False
        assert validate_name("under_score") is True
        assert validate_name("00_收件箱") is True
        assert validate_name("-leading") is False
        assert validate_name("trailing-") is False
        assert validate_name("double--dash") is False
        assert validate_name("has.dot") is False


class TestWorkspaceModel:
    def test_has_item(self):
        ws = Workspace(
            name="test",
            created="2026-01-01",
            items=[
                WorkspaceItem(key="ABC", title="Paper A", added="2026-01-01"),
            ],
        )
        assert ws.has_item("ABC") is True
        assert ws.has_item("XYZ") is False

    def test_add_item(self):
        ws = Workspace(name="test", created="2026-01-01")
        assert ws.add_item("ABC", "Paper A") is True
        assert len(ws.items) == 1
        assert ws.items[0].key == "ABC"
        assert ws.items[0].title == "Paper A"

    def test_add_item_duplicate(self):
        ws = Workspace(
            name="test",
            created="2026-01-01",
            items=[
                WorkspaceItem(key="ABC", title="Paper A", added="2026-01-01"),
            ],
        )
        assert ws.add_item("ABC", "Paper A") is False
        assert len(ws.items) == 1

    def test_remove_item(self):
        ws = Workspace(
            name="test",
            created="2026-01-01",
            items=[
                WorkspaceItem(key="ABC", title="Paper A", added="2026-01-01"),
                WorkspaceItem(key="DEF", title="Paper B", added="2026-01-01"),
            ],
        )
        assert ws.remove_item("ABC") is True
        assert len(ws.items) == 1
        assert ws.items[0].key == "DEF"

    def test_remove_item_not_found(self):
        ws = Workspace(name="test", created="2026-01-01")
        assert ws.remove_item("XYZ") is False


class TestWorkspaceIO:
    def test_mineru_cache_dir_uses_repo_state_dir(self, tmp_path):
        state_root = tmp_path / ".zot" / "state"
        with patch("zotero_cli.core.workspace_store.state_dir", return_value=state_root):
            assert mineru_cache_dir() == state_root / "mineru"

    def test_save_and_load(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            ws = Workspace(
                name="test-ws",
                created="2026-03-29T10:00:00+00:00",
                description="Test workspace",
                items=[
                    WorkspaceItem(key="ABC123", title="Paper A", added="2026-03-29T10:05:00+00:00"),
                    WorkspaceItem(key="DEF456", title="Paper B", added="2026-03-29T10:06:00+00:00"),
                ],
            )
            save_workspace(ws)
            loaded = load_workspace("test-ws")

        assert loaded.name == "test-ws"
        assert loaded.created == "2026-03-29T10:00:00+00:00"
        assert loaded.description == "Test workspace"
        assert len(loaded.items) == 2
        assert loaded.items[0].key == "ABC123"
        assert loaded.items[0].title == "Paper A"
        assert loaded.items[1].key == "DEF456"

    def test_load_nonexistent(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                load_workspace("nonexistent")

    def test_list_workspaces(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            save_workspace(Workspace(name="ws-a", created="2026-01-01"))
            save_workspace(Workspace(name="ws-b", created="2026-01-02"))
            result = list_workspaces()
        assert len(result) == 2
        assert result[0].name == "ws-a"
        assert result[1].name == "ws-b"

    def test_list_empty(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = list_workspaces()
        assert result == []

    def test_delete_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            save_workspace(Workspace(name="doomed", created="2026-01-01"))
            assert workspace_exists("doomed")
            delete_workspace("doomed")
            assert not workspace_exists("doomed")

    def test_delete_nonexistent(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                delete_workspace("nonexistent")

    def test_save_with_special_chars_in_title(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            ws = Workspace(
                name="test-special",
                created="2026-01-01",
                items=[
                    WorkspaceItem(key="X1", title='Paper with "quotes" and \\backslash', added="2026-01-01"),
                ],
            )
            save_workspace(ws)
            loaded = load_workspace("test-special")
        assert loaded.items[0].title == 'Paper with "quotes" and \\backslash'


# --- CLI integration tests ---


class TestWorkspaceCLI:
    def test_new_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "new", "test-ws", "--description", "A test"])
        assert result.exit_code == 0
        assert "Workspace created: test-ws" in result.output

    def test_new_workspace_invalid_name(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "new", "Bad Name"])
        assert result.exit_code != 0
        assert "Invalid workspace name" in result.output

    def test_new_workspace_duplicate(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            result = _invoke(["workspace", "new", "test-ws"])
        assert "already exists" in result.output

    def test_list_workspaces_empty(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "list"])
        assert "No workspaces found" in result.output

    def test_list_workspaces(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "ws-a"])
            _invoke(["workspace", "new", "ws-b", "--description", "Second workspace"])
            result = _invoke(["workspace", "list"])
        assert "ws-a" in result.output
        assert "ws-b" in result.output

    def test_list_workspaces_json(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "ws-json"])
            result = _invoke(["workspace", "list"], json_output=True)
        # `workspace list --json` is routed through the agent envelope.
        data = json.loads(result.output)["data"]
        assert len(data) == 1
        assert data[0]["name"] == "ws-json"

    def test_delete_workspace_with_yes(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "doomed"])
            result = _invoke(["workspace", "delete", "doomed", "--yes"])
        assert "Workspace deleted: doomed" in result.output

    def test_delete_workspace_not_found(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "delete", "nonexistent", "--yes"])
        assert "not found" in result.output

    def test_add_item(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            result = _invoke(["workspace", "add", "test-ws", "ATTN001"])
        assert result.exit_code == 0
        assert "Added 1 item(s)" in result.output

    def test_add_item_not_found_in_zotero(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            result = _invoke(["workspace", "add", "test-ws", "NONEXIST"])
        assert "not found in Zotero" in result.output

    def test_add_item_duplicate(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            _invoke(["workspace", "add", "test-ws", "ATTN001"])
            result = _invoke(["workspace", "add", "test-ws", "ATTN001"])
        assert "already in workspace" in result.output

    def test_add_to_nonexistent_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "add", "nope", "ATTN001"])
        assert "not found" in result.output

    def test_remove_item(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            _invoke(["workspace", "add", "test-ws", "ATTN001"])
            result = _invoke(["workspace", "remove", "test-ws", "ATTN001"])
        assert "Removed 1 item(s)" in result.output

    def test_remove_nonexistent_item(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            result = _invoke(["workspace", "remove", "test-ws", "NONEXIST"])
        assert "Removed 0 item(s)" in result.output

    def test_show_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            _invoke(["workspace", "add", "test-ws", "ATTN001"])
            result = _invoke(["workspace", "show", "test-ws"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_show_workspace_json(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            _invoke(["workspace", "add", "test-ws", "ATTN001"])
            result = _invoke(["workspace", "show", "test-ws"], json_output=True)
        data = json.loads(result.output)["data"]
        assert len(data) == 1
        assert data[0]["key"] == "ATTN001"

    def test_show_empty_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "test-ws"])
            result = _invoke(["workspace", "show", "test-ws"])
        assert "empty" in result.output

    def test_show_nonexistent_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "show", "nope"])
        assert "not found" in result.output


# --- Fix-phase: JSON contract, dry-run, confirmation, manifest robustness ---


class TestWorkspaceJsonEnvelopes:
    def test_new_json_envelope(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "new", "json-ws", "--description", "desc"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["name"] == "json-ws"
        assert payload["data"]["description"] == "desc"

    def test_list_empty_json_envelope(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "list"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"] == []
        assert payload["meta"]["count"] == 0

    def test_add_and_remove_json_envelopes(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "json-ws"])
            added = _invoke(["workspace", "add", "json-ws", "ATTN001", "NONEXIST"], json_output=True)
            removed = _invoke(["workspace", "remove", "json-ws", "ATTN001"], json_output=True)
        add_payload = json.loads(added.stdout)
        assert add_payload["data"]["added"] == ["ATTN001"]
        assert add_payload["data"]["missing"] == ["NONEXIST"]
        remove_payload = json.loads(removed.stdout)
        assert remove_payload["data"]["removed"] == ["ATTN001"]

    def test_show_empty_and_search_miss_json_envelopes(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "json-ws"])
            shown = _invoke(["workspace", "show", "json-ws"], json_output=True)
            searched = _invoke(["workspace", "search", "anything", "--workspace", "json-ws"], json_output=True)
        assert json.loads(shown.output)["meta"]["count"] == 0
        assert json.loads(searched.output)["meta"]["count"] == 0

    def test_import_json_envelope(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "json-ws"])
            result = _invoke(["workspace", "import", "json-ws", "--search", "attention"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["imported"] >= 1
        assert "resolved" in payload["data"]

    def test_export_markdown_json_envelope(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "json-ws"])
            _invoke(["workspace", "add", "json-ws", "ATTN001"])
            result = _invoke(["workspace", "export", "json-ws"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["format"] == "markdown"
        assert "# Workspace: json-ws" in payload["data"]["content"]


class TestWorkspaceDryRun:
    def test_new_dry_run_does_not_create(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "new", "dry-ws", "--dry-run"], json_output=True)
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert not (tmp_path / "dry-ws").exists()

    def test_add_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "dry-ws"])
            result = _invoke(["workspace", "add", "dry-ws", "ATTN001", "--dry-run"], json_output=True)
            saved = load_workspace("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_add"] == ["ATTN001"]
        assert saved.items == []

    def test_remove_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "dry-ws"])
            _invoke(["workspace", "add", "dry-ws", "ATTN001"])
            result = _invoke(["workspace", "remove", "dry-ws", "ATTN001", "--dry-run"], json_output=True)
            saved = load_workspace("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_remove"] == ["ATTN001"]
        assert len(saved.items) == 1

    def test_delete_dry_run_keeps_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "dry-ws"])
            result = _invoke(["workspace", "delete", "dry-ws", "--dry-run"], json_output=True)
            exists = workspace_exists("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_delete"] is True
        assert exists

    def test_import_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "dry-ws"])
            result = _invoke(["workspace", "import", "dry-ws", "--search", "attention", "--dry-run"], json_output=True)
            saved = load_workspace("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_add"]
        assert saved.items == []


class TestWorkspaceDeleteConfirmation:
    def test_delete_without_yes_requires_confirmation(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "keep-me"])
            result = _invoke(["workspace", "delete", "keep-me"], json_output=True)
            exists = workspace_exists("keep-me")
        assert result.exit_code == 3, result.output
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "confirmation_required"
        assert exists

    def test_no_interaction_never_implies_yes(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "keep-me"])
            result = _invoke(["--no-interaction", "workspace", "delete", "keep-me"], json_output=True)
            exists = workspace_exists("keep-me")
        assert result.exit_code == 3, result.output
        assert json.loads(result.output)["error"]["code"] == "confirmation_required"
        assert exists

    def test_delete_with_yes_removes_workspace(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "doomed"])
            result = _invoke(["workspace", "delete", "doomed", "--yes"], json_output=True)
            removed = not workspace_exists("doomed")
        payload = json.loads(result.output)
        assert payload["data"]["deleted"] is True
        assert removed


class TestWorkspaceManifestRobustness:
    def test_newline_description_roundtrips(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            created = _invoke(["workspace", "new", "nl-ws", "--description", "line1\nline2"], json_output=True)
            loaded = load_workspace("nl-ws")
            listed = _invoke(["workspace", "list"], json_output=True)
            shown = _invoke(["workspace", "show", "nl-ws"])
        assert created.exit_code == 0, created.output
        assert loaded.description == "line1\nline2"
        payload = json.loads(listed.output)
        assert payload["data"][0]["description"] == "line1\nline2"
        assert shown.exit_code == 0
        assert "Traceback" not in shown.output

    def test_corrupt_manifest_warns_instead_of_vanishing(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            _invoke(["workspace", "new", "good-ws"])
            broken = tmp_path / "broken-ws"
            broken.mkdir()
            (broken / "workspace.toml").write_text('description = "unterminated\n', encoding="utf-8")
            result = _invoke(["workspace", "list"])
        assert "good-ws" in result.output
        assert "Warning" in result.output
        assert "broken-ws" in result.output

    def test_failed_atomic_write_preserves_previous_manifest(self, tmp_path, monkeypatch):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            save_workspace(Workspace(name="atomic-ws", created="2026-01-01", description="old"))
            original = (tmp_path / "atomic-ws" / "workspace.toml").read_text(encoding="utf-8")

            def boom(*_args: object, **_kwargs: object) -> None:
                raise OSError("disk full")

            monkeypatch.setattr("zotero_cli.core.workspace_store.os.fsync", boom)
            with pytest.raises(OSError):
                save_workspace(Workspace(name="atomic-ws", created="2026-01-01", description="new"))
            assert (tmp_path / "atomic-ws" / "workspace.toml").read_text(encoding="utf-8") == original
            assert load_workspace("atomic-ws").description == "old"
            leftovers = [p.name for p in (tmp_path / "atomic-ws").iterdir() if p.name != "workspace.toml"]
            assert leftovers == []

    def test_concurrent_saves_leave_a_valid_manifest(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):

            def writer(index: int) -> None:
                save_workspace(Workspace(name="race-ws", created="2026-01-01", description=f"value-{index}"))

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(writer, range(20)))
            loaded = load_workspace("race-ws")
        assert loaded.description.startswith("value-")
        assert not [p for p in (tmp_path / "race-ws").iterdir() if p.suffix == ".tmp"]

    def test_overlong_name_is_a_validation_error(self, tmp_path):
        with patch("zotero_cli.core.workspace_store.workspaces_dir", return_value=tmp_path):
            result = _invoke(["workspace", "new", "a" * 300], json_output=True)
        assert result.exit_code == 3, result.output
        assert json.loads(result.output)["error"]["code"] == "validation_error"
        assert "Traceback" not in result.output
