"""工作区领域层测试：名称校验、条目模型与落盘读写。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zotero_cli.workspace.store import (
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
        assert validate_name("trailing-newline\n") is False
        assert validate_name("mid\nline") is False


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
        with patch("zotero_cli.workspace.store.state_dir", return_value=state_root):
            assert mineru_cache_dir() == state_root / "mineru"

    def test_save_and_load(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
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
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                load_workspace("nonexistent")

    def test_list_workspaces(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
            save_workspace(Workspace(name="ws-a", created="2026-01-01"))
            save_workspace(Workspace(name="ws-b", created="2026-01-02"))
            result = list_workspaces()
        assert len(result) == 2
        assert result[0].name == "ws-a"
        assert result[1].name == "ws-b"

    def test_list_empty(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
            result = list_workspaces()
        assert result == []

    def test_delete_workspace(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
            save_workspace(Workspace(name="doomed", created="2026-01-01"))
            assert workspace_exists("doomed")
            delete_workspace("doomed")
            assert not workspace_exists("doomed")

    def test_delete_nonexistent(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
            with pytest.raises(FileNotFoundError):
                delete_workspace("nonexistent")

    def test_save_with_special_chars_in_title(self, tmp_path):
        with patch("zotero_cli.workspace.store.workspaces_dir", return_value=tmp_path):
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
