"""`pdf_rag` 子命令测试：JSON envelope、试运行、删除确认与清单健壮性。"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from tests.support import invoke_cli as _invoke

from zotero_cli.pdf_rag.store import PdfRag, load_pdf_rag, pdf_rag_exists, save_pdf_rag


class TestPdfRagCLI:
    def test_new_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "new", "test-ws", "--description", "A test"])
        assert result.exit_code == 0
        assert "PdfRag created: test-ws" in result.output

    def test_new_pdf_rag_invalid_name(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "new", "Bad Name"])
        assert result.exit_code != 0
        assert "Invalid pdf_rag name" in result.output

    def test_new_pdf_rag_duplicate(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            result = _invoke(["pdf-rag", "new", "test-ws"])
        assert "already exists" in result.output

    def test_list_pdf_rags_empty(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "list"])
        assert "No pdf_rags found" in result.output

    def test_list_pdf_rags(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "ws-a"])
            _invoke(["pdf-rag", "new", "ws-b", "--description", "Second pdf_rag"])
            result = _invoke(["pdf-rag", "list"])
        assert "ws-a" in result.output
        assert "ws-b" in result.output

    def test_list_pdf_rags_json(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "ws-json"])
            result = _invoke(["pdf-rag", "list"], json_output=True)
        # `pdf_rag list --json` 走代理 envelope 输出。
        data = json.loads(result.output)["data"]
        assert len(data) == 1
        assert data[0]["name"] == "ws-json"

    def test_delete_pdf_rag_with_yes(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "doomed"])
            result = _invoke(["pdf-rag", "delete", "doomed", "--yes"])
        assert "PdfRag deleted: doomed" in result.output

    def test_delete_pdf_rag_not_found(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "delete", "nonexistent", "--yes"])
        assert "not found" in result.output

    def test_add_item(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            result = _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
        assert result.exit_code == 0
        assert "Added 1 item(s)" in result.output

    def test_add_item_not_found_in_zotero(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            result = _invoke(["pdf-rag", "add", "test-ws", "NONEXIST"])
        assert "not found in Zotero" in result.output

    def test_add_item_duplicate(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
        assert "already in pdf_rag" in result.output

    def test_add_to_nonexistent_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "add", "nope", "ATTN001"])
        assert "not found" in result.output

    def test_remove_item(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "remove", "test-ws", "ATTN001"])
        assert "Removed 1 item(s)" in result.output

    def test_remove_nonexistent_item(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            result = _invoke(["pdf-rag", "remove", "test-ws", "NONEXIST"])
        assert "Removed 0 item(s)" in result.output

    def test_show_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "show", "test-ws"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_show_pdf_rag_json(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            _invoke(["pdf-rag", "add", "test-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "show", "test-ws"], json_output=True)
        data = json.loads(result.output)["data"]
        assert len(data) == 1
        assert data[0]["key"] == "ATTN001"

    def test_show_empty_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "test-ws"])
            result = _invoke(["pdf-rag", "show", "test-ws"])
        assert "empty" in result.output

    def test_show_nonexistent_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "show", "nope"])
        assert "not found" in result.output


class TestPdfRagJsonEnvelopes:
    def test_new_json_envelope(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "new", "json-ws", "--description", "desc"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["name"] == "json-ws"
        assert payload["data"]["description"] == "desc"

    def test_list_empty_json_envelope(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "list"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"] == []
        assert payload["meta"]["count"] == 0

    def test_add_and_remove_json_envelopes(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "json-ws"])
            added = _invoke(["pdf-rag", "add", "json-ws", "ATTN001", "NONEXIST"], json_output=True)
            removed = _invoke(["pdf-rag", "remove", "json-ws", "ATTN001"], json_output=True)
        add_payload = json.loads(added.stdout)
        assert add_payload["data"]["added"] == ["ATTN001"]
        assert add_payload["data"]["missing"] == ["NONEXIST"]
        remove_payload = json.loads(removed.stdout)
        assert remove_payload["data"]["removed"] == ["ATTN001"]

    def test_show_empty_and_search_miss_json_envelopes(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "json-ws"])
            shown = _invoke(["pdf-rag", "show", "json-ws"], json_output=True)
            searched = _invoke(["pdf-rag", "search", "anything", "--pdf-rag", "json-ws"], json_output=True)
        assert json.loads(shown.output)["meta"]["count"] == 0
        assert json.loads(searched.output)["meta"]["count"] == 0

    def test_import_json_envelope(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "json-ws"])
            result = _invoke(["pdf-rag", "import", "json-ws", "--search", "attention"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["imported"] >= 1
        assert "resolved" in payload["data"]

    def test_export_markdown_json_envelope(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "json-ws"])
            _invoke(["pdf-rag", "add", "json-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "export", "json-ws"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["format"] == "markdown"
        assert "# PdfRag: json-ws" in payload["data"]["content"]


class TestPdfRagDryRun:
    def test_new_dry_run_does_not_create(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "new", "dry-ws", "--dry-run"], json_output=True)
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert not (tmp_path / "dry-ws").exists()

    def test_add_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "dry-ws"])
            result = _invoke(["pdf-rag", "add", "dry-ws", "ATTN001", "--dry-run"], json_output=True)
            saved = load_pdf_rag("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_add"] == ["ATTN001"]
        assert saved.items == []

    def test_remove_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "dry-ws"])
            _invoke(["pdf-rag", "add", "dry-ws", "ATTN001"])
            result = _invoke(["pdf-rag", "remove", "dry-ws", "ATTN001", "--dry-run"], json_output=True)
            saved = load_pdf_rag("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_remove"] == ["ATTN001"]
        assert len(saved.items) == 1

    def test_delete_dry_run_keeps_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "dry-ws"])
            result = _invoke(["pdf-rag", "delete", "dry-ws", "--dry-run"], json_output=True)
            exists = pdf_rag_exists("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_delete"] is True
        assert exists

    def test_import_dry_run_does_not_save(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "dry-ws"])
            result = _invoke(["pdf-rag", "import", "dry-ws", "--search", "attention", "--dry-run"], json_output=True)
            saved = load_pdf_rag("dry-ws")
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_add"]
        assert saved.items == []


class TestPdfRagDeleteConfirmation:
    def test_delete_without_yes_requires_confirmation(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "keep-me"])
            result = _invoke(["pdf-rag", "delete", "keep-me"], json_output=True)
            exists = pdf_rag_exists("keep-me")
        assert result.exit_code == 3, result.output
        payload = json.loads(result.output)
        assert payload["error"]["code"] == "confirmation_required"
        assert exists

    def test_no_interaction_never_implies_yes(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "keep-me"])
            result = _invoke(["--no-interaction", "pdf-rag", "delete", "keep-me"], json_output=True)
            exists = pdf_rag_exists("keep-me")
        assert result.exit_code == 3, result.output
        assert json.loads(result.output)["error"]["code"] == "confirmation_required"
        assert exists

    def test_delete_with_yes_removes_pdf_rag(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "doomed"])
            result = _invoke(["pdf-rag", "delete", "doomed", "--yes"], json_output=True)
            removed = not pdf_rag_exists("doomed")
        payload = json.loads(result.output)
        assert payload["data"]["deleted"] is True
        assert removed


class TestPdfRagManifestRobustness:
    def test_newline_description_roundtrips(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            created = _invoke(["pdf-rag", "new", "nl-ws", "--description", "line1\nline2"], json_output=True)
            loaded = load_pdf_rag("nl-ws")
            listed = _invoke(["pdf-rag", "list"], json_output=True)
            shown = _invoke(["pdf-rag", "show", "nl-ws"])
        assert created.exit_code == 0, created.output
        assert loaded.description == "line1\nline2"
        payload = json.loads(listed.output)
        assert payload["data"][0]["description"] == "line1\nline2"
        assert shown.exit_code == 0
        assert "Traceback" not in shown.output

    def test_corrupt_manifest_warns_instead_of_vanishing(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            _invoke(["pdf-rag", "new", "good-ws"])
            broken = tmp_path / "broken-ws"
            broken.mkdir()
            (broken / "pdf_rag.toml").write_text('description = "unterminated\n', encoding="utf-8")
            result = _invoke(["pdf-rag", "list"])
        assert "good-ws" in result.output
        assert "Warning" in result.output
        assert "broken-ws" in result.output

    def test_failed_atomic_write_preserves_previous_manifest(self, tmp_path, monkeypatch):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            save_pdf_rag(PdfRag(name="atomic-ws", created="2026-01-01", description="old"))
            original = (tmp_path / "atomic-ws" / "pdf_rag.toml").read_text(encoding="utf-8")

            def boom(*_args: object, **_kwargs: object) -> None:
                raise OSError("disk full")

            monkeypatch.setattr("zotero_cli.pdf_rag.store.os.fsync", boom)
            with pytest.raises(OSError):
                save_pdf_rag(PdfRag(name="atomic-ws", created="2026-01-01", description="new"))
            assert (tmp_path / "atomic-ws" / "pdf_rag.toml").read_text(encoding="utf-8") == original
            assert load_pdf_rag("atomic-ws").description == "old"
            leftovers = [p.name for p in (tmp_path / "atomic-ws").iterdir() if p.name != "pdf_rag.toml"]
            assert leftovers == []

    def test_concurrent_saves_leave_a_valid_manifest(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):

            def writer(index: int) -> None:
                save_pdf_rag(PdfRag(name="race-ws", created="2026-01-01", description=f"value-{index}"))

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(writer, range(20)))
            loaded = load_pdf_rag("race-ws")
        assert loaded.description.startswith("value-")
        assert not [p for p in (tmp_path / "race-ws").iterdir() if p.suffix == ".tmp"]

    def test_overlong_name_is_a_validation_error(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            result = _invoke(["pdf-rag", "new", "a" * 300], json_output=True)
        assert result.exit_code == 3, result.output
        assert json.loads(result.output)["error"]["code"] == "validation_error"
        assert "Traceback" not in result.output


class TestPdfRagManifestSchemaErrors:
    def _write_schema_invalid_manifest(self, root):
        pdf_rag_dir = root / "bad-manifest"
        pdf_rag_dir.mkdir(parents=True)
        (pdf_rag_dir / "pdf_rag.toml").write_text('created = "x"\n[[items]]\nkey = "ATTN001"\n', encoding="utf-8")
        return pdf_rag_dir

    def test_schema_invalid_manifest_is_a_typed_configuration_error(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            self._write_schema_invalid_manifest(tmp_path)
            result = _invoke(["pdf-rag", "show", "bad-manifest"], json_output=True)
        assert result.exit_code == 3, result.output
        payload = json.loads(result.stdout)
        assert payload["error"]["code"] == "configuration_error"
        assert "items[1].title" in payload["error"]["message"]

    def test_delete_removes_pdf_rag_with_schema_invalid_manifest(self, tmp_path):
        with patch("zotero_cli.pdf_rag.store.pdf_rags_dir", return_value=tmp_path):
            pdf_rag_dir = self._write_schema_invalid_manifest(tmp_path)
            result = _invoke(["pdf-rag", "delete", "bad-manifest", "--yes"], json_output=True)
            exists = pdf_rag_dir.exists()
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["data"]["deleted"] is True
        assert exists is False
        assert "unreadable" in result.stderr


class TestPdfRagRootCleanup:
    def test_delete_last_pdf_rag_removes_empty_pdf_rag_root(self, tmp_path):
        # 仓库级 conftest 把工作区根隔离到 tmp_path/.pdf_rag。
        created = _invoke(["pdf-rag", "new", "last-pdf_rag"], json_output=True)
        assert created.exit_code == 0, created.output
        root = tmp_path / ".zot" / "pdf_rag"
        assert root.exists()
        deleted = _invoke(["pdf-rag", "delete", "last-pdf_rag", "--yes"], json_output=True)
        assert deleted.exit_code == 0, deleted.output
        assert not root.exists()

    def test_delete_keeps_root_when_other_pdf_rags_remain(self, tmp_path):
        _invoke(["pdf-rag", "new", "keep-a"], json_output=True)
        _invoke(["pdf-rag", "new", "keep-b"], json_output=True)
        deleted = _invoke(["pdf-rag", "delete", "keep-a", "--yes"], json_output=True)
        assert deleted.exit_code == 0, deleted.output
        assert (tmp_path / ".zot" / "pdf_rag" / "keep-b" / "pdf_rag.toml").exists()
