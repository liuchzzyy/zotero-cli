"""Tests for workspace RAG CLI commands (index / embed / query)."""

from __future__ import annotations

import json
from contextlib import ExitStack
from hashlib import sha256
from unittest.mock import patch

from tests.support import invoke_cli as _invoke

from zotero_cli.config import VectorStoreConfig
from zotero_cli.core.mineru import MinerUError, MinerUParseResult
from zotero_cli.core.rag_index import RagIndex


def _patch_workspace(tmp_path):
    """Patch workspace dirs + vector store so tests are fully isolated."""
    stack = ExitStack()
    stack.enter_context(patch("zotero_cli.core.workspace.workspaces_dir", return_value=tmp_path))
    stack.enter_context(
        patch(
            "zotero_cli.commands.workspace.load_vector_store_config",
            return_value=VectorStoreConfig(path=str(tmp_path / "_qdrant")),
        )
    )
    cache_cls = stack.enter_context(patch("zotero_cli.commands.workspace.MinerUParseCache"))

    def fake_ensure_many(paths, _progress=None):
        results = {}
        for pdf_path in paths:
            fingerprint = sha256(pdf_path.read_bytes()).hexdigest()
            root = tmp_path / "mineru" / fingerprint / "vlm"
            root.mkdir(parents=True, exist_ok=True)
            markdown = root / "document.md"
            content = root / "content_list.json"
            manifest = root / "manifest.json"
            markdown.write_text("# Introduction\nAttention mechanism evidence")
            content.write_text(
                json.dumps(
                    [
                        {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
                        {"type": "text", "text": "Attention mechanism evidence", "page_idx": 0},
                    ]
                )
            )
            manifest.write_text("{}")
            results[pdf_path] = MinerUParseResult(
                source_path=pdf_path,
                fingerprint=fingerprint,
                model_version="vlm",
                root=root,
                markdown_path=markdown,
                content_list_path=content,
                manifest_path=manifest,
            )
        return results

    cache_cls.return_value.ensure_many.side_effect = fake_ensure_many
    return stack


class TestWorkspaceIndex:
    def test_index_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-idx"])
            _invoke(["workspace", "add", "test-idx", "ATTN001"])
            result = _invoke(["workspace", "index", "test-idx"])
        assert result.exit_code == 0
        assert "Indexed" in result.output
        idx_path = tmp_path / "test-idx" / "rag.idx.sqlite"
        assert idx_path.exists()

    def test_index_nonexistent_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            result = _invoke(["workspace", "index", "nope"])
        assert "not found" in result.output

    def test_index_empty_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "empty-ws"])
            result = _invoke(["workspace", "index", "empty-ws"])
        assert "empty" in result.output.lower() or "Add items" in result.output

    def test_index_force_rebuild(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-idx"])
            _invoke(["workspace", "add", "test-idx", "ATTN001"])
            _invoke(["workspace", "index", "test-idx"])
            result = _invoke(["workspace", "index", "test-idx", "--force"])
        assert result.exit_code == 0
        assert "Indexed" in result.output

    def test_index_incremental(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-idx"])
            _invoke(["workspace", "add", "test-idx", "ATTN001"])
            _invoke(["workspace", "index", "test-idx"])
            result = _invoke(["workspace", "index", "test-idx"])
        assert "up to date" in result.output

    def test_reindex_forces_rebuild(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-idx"])
            _invoke(["workspace", "add", "test-idx", "ATTN001"])
            _invoke(["workspace", "index", "test-idx"])
            result = _invoke(["workspace", "reindex", "test-idx"])
        assert result.exit_code == 0
        assert "Indexed" in result.output

    def test_index_no_embed_skips_embedding(self, tmp_path):
        with (
            _patch_workspace(tmp_path),
            patch(
                "zotero_cli.commands.workspace.embed_texts",
                side_effect=AssertionError("should not embed"),
            ) as embed_mock,
        ):
            _invoke(["workspace", "new", "test-idx"])
            _invoke(["workspace", "add", "test-idx", "ATTN001"])
            result = _invoke(
                ["workspace", "index", "test-idx", "--no-embed"],
                env={
                    "ZOT_EMBEDDING_URL": "https://ai.gitee.com/v1",
                    "ZOT_EMBEDDING_KEY": "k",
                    "ZOT_EMBEDDING_MODEL": "bge-m3",
                },
            )

        assert result.exit_code == 0
        assert "BM25" in result.output
        embed_mock.assert_not_called()

        idx = RagIndex(tmp_path / "test-idx" / "rag.idx.sqlite")
        try:
            assert len(idx.get_all_chunks()) > 0
        finally:
            idx.close()

    def test_failed_mineru_parse_remains_pending_for_retry(self, tmp_path):
        with _patch_workspace(tmp_path), patch("zotero_cli.commands.workspace.MinerUParseCache") as cache_cls:
            cache_cls.return_value.ensure_many.side_effect = lambda paths, _progress=None: {
                path: MinerUError("cloud parse failed") for path in paths
            }
            _invoke(["workspace", "new", "test-retry"])
            _invoke(["workspace", "add", "test-retry", "ATTN001"])
            result = _invoke(["workspace", "index", "test-retry", "--no-embed"])

        assert result.exit_code == 0
        assert "cloud parse failed" in result.output
        idx = RagIndex(tmp_path / "test-retry" / "rag.idx.sqlite")
        try:
            assert idx.get_meta("pipeline:ATTN001") is None
            assert idx.get_meta("pdf_hash:ATTN001") is None
        finally:
            idx.close()

    def test_embed_missing_configuration_returns_exit_3(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "embed-missing"])
            _invoke(["workspace", "add", "embed-missing", "ATTN001"])
            _invoke(["workspace", "index", "embed-missing", "--no-embed"])
            result = _invoke(
                ["workspace", "embed", "embed-missing"],
                json_output=True,
                env={
                    "ZOT_EMBEDDING_URL": "",
                    "ZOT_EMBEDDING_KEY": "",
                    "ZOT_EMBEDDING_MODEL": "",
                },
            )

        assert result.exit_code == 3
        envelope = json.loads(result.output)
        assert envelope["error"]["code"] == "configuration_error"


class TestWorkspaceQuery:
    def test_query_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-q"])
            _invoke(["workspace", "add", "test-q", "ATTN001"])
            _invoke(["workspace", "index", "test-q"])
            result = _invoke(["workspace", "query", "attention", "--workspace", "test-q"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_query_json_output(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-q"])
            _invoke(["workspace", "add", "test-q", "ATTN001"])
            _invoke(["workspace", "index", "test-q"])
            result = _invoke(
                ["workspace", "query", "attention", "--workspace", "test-q"],
                json_output=True,
            )
        data = json.loads(result.output)["data"]
        results = data["results"]
        assert isinstance(results, list)
        assert len(results) > 0
        assert "item_key" in results[0]
        assert data["mode"] == "bm25"

    def test_query_rerank_json_output(self, tmp_path):
        def fake_rerank(question, candidates, config, *, top_n=50, progress_callback=None):
            assert question == "attention"
            assert config.provider == "gitee"
            assert top_n == 2
            selected = candidates[:top_n]
            return [(cid, 10.0 - idx, chunk) for idx, (cid, _score, chunk) in enumerate(selected)] + candidates[top_n:]

        with _patch_workspace(tmp_path), patch("zotero_cli.commands.workspace.rerank_chunks", fake_rerank):
            _invoke(["workspace", "new", "test-q"])
            _invoke(["workspace", "add", "test-q", "ATTN001"])
            _invoke(["workspace", "index", "test-q"])
            result = _invoke(
                ["workspace", "query", "attention", "--workspace", "test-q", "--rerank", "--rerank-top-n", "2"],
                json_output=True,
                env={
                    "ZOT_RERANK_PROVIDER": "gitee",
                    "ZOT_RERANK_URL": "https://ai.gitee.com/v1/rerank",
                    "ZOT_RERANK_KEY": "fake",
                    "ZOT_RERANK_MODEL": "fake-reranker",
                },
            )

        data = json.loads(result.output)["data"]
        assert data["mode"] == "bm25+rerank"
        assert data["results"][0]["score"] == 10.0

    def test_query_irrelevant(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-q"])
            _invoke(["workspace", "add", "test-q", "ATTN001"])
            _invoke(["workspace", "index", "test-q"])
            result = _invoke(["workspace", "query", "zzzzqqqxxx999", "--workspace", "test-q"])
        assert result.exit_code == 0

    def test_query_no_index(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-q"])
            result = _invoke(["workspace", "query", "test", "--workspace", "test-q"])
        assert "index" in result.output.lower()

    def test_query_nonexistent_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            result = _invoke(["workspace", "query", "test", "--workspace", "nope"])
        assert "not found" in result.output


class TestWorkspaceExport:
    def test_export_markdown(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-exp"])
            _invoke(["workspace", "add", "test-exp", "ATTN001"])
            result = _invoke(["workspace", "export", "test-exp"])
        assert result.exit_code == 0
        assert "Attention" in result.output
        assert "ATTN001" in result.output
        assert "# Workspace: test-exp" in result.output

    def test_export_json(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-exp"])
            _invoke(["workspace", "add", "test-exp", "ATTN001"])
            result = _invoke(["workspace", "export", "test-exp", "--format", "json"])
        data = json.loads(result.output)["data"]
        assert len(data) >= 1

    def test_export_bibtex(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-exp"])
            _invoke(["workspace", "add", "test-exp", "ATTN001"])
            result = _invoke(["workspace", "export", "test-exp", "--format", "bibtex"])
        assert result.exit_code == 0
        assert "@" in result.output
        assert "Attention" in result.output

    def test_export_nonexistent(self, tmp_path):
        with _patch_workspace(tmp_path):
            result = _invoke(["workspace", "export", "nope"])
        assert "not found" in result.output

    def test_export_empty_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-exp"])
            result = _invoke(["workspace", "export", "test-exp"])
        assert "empty" in result.output.lower()


class TestWorkspaceImport:
    def test_import_from_search(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-imp"])
            result = _invoke(["workspace", "import", "test-imp", "--search", "attention"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_from_collection(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-imp"])
            result = _invoke(["workspace", "import", "test-imp", "--collection", "Machine Learning"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_from_tag(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-imp"])
            result = _invoke(["workspace", "import", "test-imp", "--tag", "transformer"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_no_source(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-imp"])
            result = _invoke(["workspace", "import", "test-imp"])
        assert "specify" in result.output.lower() or "at least" in result.output.lower()

    def test_import_dedup(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-imp"])
            _invoke(["workspace", "add", "test-imp", "ATTN001"])
            result = _invoke(["workspace", "import", "test-imp", "--search", "attention"])
        assert result.exit_code == 0
        assert "skipped" in result.output.lower()

    def test_import_nonexistent_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            result = _invoke(["workspace", "import", "nope", "--search", "test"])
        assert "not found" in result.output


class TestWorkspaceSearch:
    def test_search_in_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-src"])
            _invoke(["workspace", "add", "test-src", "ATTN001"])
            result = _invoke(["workspace", "search", "attention", "--workspace", "test-src"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_search_no_results(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-src"])
            _invoke(["workspace", "add", "test-src", "ATTN001"])
            result = _invoke(["workspace", "search", "xyznonexistent", "--workspace", "test-src"])
        assert "No matching" in result.output or result.output.strip() == ""

    def test_search_by_author(self, tmp_path):
        with _patch_workspace(tmp_path):
            _invoke(["workspace", "new", "test-src"])
            _invoke(["workspace", "add", "test-src", "ATTN001"])
            result = _invoke(["workspace", "search", "Vaswani", "--workspace", "test-src"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_search_nonexistent_workspace(self, tmp_path):
        with _patch_workspace(tmp_path):
            result = _invoke(["workspace", "search", "test", "--workspace", "nope"])
        assert "not found" in result.output
