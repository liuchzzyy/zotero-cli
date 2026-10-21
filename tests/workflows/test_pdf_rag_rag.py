"""工作区 RAG 子命令（index / embed / query）的命令层测试。"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from tests.support import invoke_cli as _invoke
from tests.support import isolate_pdf_rag_backend

from zotero_cli.ai_pdf import MinerUError
from zotero_cli.pdf_rag.index import RagIndex


class TestPdfRagIndex:
    def test_index_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-idx"])
            _invoke(["pdf-rag", "add", "test-idx", "ATTN001"])
            result = _invoke(["pdf-rag", "index", "test-idx"])
        assert result.exit_code == 0
        assert "Indexed" in result.output
        idx_path = tmp_path / "test-idx" / "pdf_rag.sqlite"
        assert idx_path.exists()

    def test_index_nonexistent_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            result = _invoke(["pdf-rag", "index", "nope"])
        assert "not found" in result.output

    def test_index_empty_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "empty-ws"])
            result = _invoke(["pdf-rag", "index", "empty-ws"])
        assert "empty" in result.output.lower() or "Add items" in result.output

    def test_index_force_rebuild(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-idx"])
            _invoke(["pdf-rag", "add", "test-idx", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-idx"])
            result = _invoke(["pdf-rag", "index", "test-idx", "--force"])
        assert result.exit_code == 0
        assert "Indexed" in result.output

    def test_index_incremental(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-idx"])
            _invoke(["pdf-rag", "add", "test-idx", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-idx"])
            result = _invoke(["pdf-rag", "index", "test-idx"])
        assert "up to date" in result.output

    def test_reindex_forces_rebuild(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-idx"])
            _invoke(["pdf-rag", "add", "test-idx", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-idx"])
            result = _invoke(["pdf-rag", "reindex", "test-idx"])
        assert result.exit_code == 0
        assert "Indexed" in result.output

    def test_index_no_embed_skips_embedding(self, tmp_path):
        with (
            isolate_pdf_rag_backend(tmp_path),
            patch(
                "zotero_cli.cli.commands.pdf_rag.indexing.embed_texts",
                side_effect=AssertionError("should not embed"),
            ) as embed_mock,
        ):
            _invoke(["pdf-rag", "new", "test-idx"])
            _invoke(["pdf-rag", "add", "test-idx", "ATTN001"])
            result = _invoke(
                ["pdf-rag", "index", "test-idx", "--no-embed"],
                env={
                    "ZOT_EMBEDDING_URL": "https://ai.gitee.com/v1",
                    "ZOT_EMBEDDING_KEY": "k",
                    "ZOT_EMBEDDING_MODEL": "bge-m3",
                },
            )

        assert result.exit_code == 0
        assert "BM25" in result.output
        embed_mock.assert_not_called()

        idx = RagIndex(tmp_path / "test-idx" / "pdf_rag.sqlite")
        try:
            assert len(idx.get_all_chunks()) > 0
        finally:
            idx.close()

    def test_failed_mineru_parse_remains_pending_for_retry(self, tmp_path):
        with (
            isolate_pdf_rag_backend(tmp_path),
            patch("zotero_cli.cli.commands.pdf_rag.indexing.MinerUParseCache") as cache_cls,
        ):
            cache_cls.return_value.ensure_many.side_effect = lambda paths, _progress=None: {
                path: MinerUError("cloud parse failed") for path in paths
            }
            _invoke(["pdf-rag", "new", "test-retry"])
            _invoke(["pdf-rag", "add", "test-retry", "ATTN001"])
            result = _invoke(["pdf-rag", "index", "test-retry", "--no-embed"])

        assert result.exit_code == 0
        assert "cloud parse failed" in result.output
        idx = RagIndex(tmp_path / "test-retry" / "pdf_rag.sqlite")
        try:
            assert idx.get_meta("pipeline:ATTN001") is None
            assert idx.get_meta("pdf_hash:ATTN001") is None
        finally:
            idx.close()

    def test_embed_missing_configuration_returns_exit_3(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "embed-missing"])
            _invoke(["pdf-rag", "add", "embed-missing", "ATTN001"])
            _invoke(["pdf-rag", "index", "embed-missing", "--no-embed"])
            result = _invoke(
                ["pdf-rag", "embed", "embed-missing"],
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


class TestPdfRagQuery:
    def test_query_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-q"])
            _invoke(["pdf-rag", "add", "test-q", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-q"])
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "test-q"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_query_json_output(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-q"])
            _invoke(["pdf-rag", "add", "test-q", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-q"])
            result = _invoke(
                ["pdf-rag", "query", "attention", "--pdf-rag", "test-q"],
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

        with (
            isolate_pdf_rag_backend(tmp_path),
            patch("zotero_cli.cli.commands.pdf_rag.query.rerank_chunks", fake_rerank),
        ):
            _invoke(["pdf-rag", "new", "test-q"])
            _invoke(["pdf-rag", "add", "test-q", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-q"])
            result = _invoke(
                ["pdf-rag", "query", "attention", "--pdf-rag", "test-q", "--rerank", "--rerank-top-n", "2"],
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
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-q"])
            _invoke(["pdf-rag", "add", "test-q", "ATTN001"])
            _invoke(["pdf-rag", "index", "test-q"])
            result = _invoke(["pdf-rag", "query", "zzzzqqqxxx999", "--pdf-rag", "test-q"])
        assert result.exit_code == 0

    def test_query_no_index(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-q"])
            result = _invoke(["pdf-rag", "query", "test", "--pdf-rag", "test-q"])
        assert "index" in result.output.lower()

    def test_query_nonexistent_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            result = _invoke(["pdf-rag", "query", "test", "--pdf-rag", "nope"])
        assert "not found" in result.output


class TestPdfRagExport:
    def test_export_markdown(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-exp"])
            _invoke(["pdf-rag", "add", "test-exp", "ATTN001"])
            result = _invoke(["pdf-rag", "export", "test-exp"])
        assert result.exit_code == 0
        assert "Attention" in result.output
        assert "ATTN001" in result.output
        assert "# PdfRag: test-exp" in result.output

    def test_export_json(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-exp"])
            _invoke(["pdf-rag", "add", "test-exp", "ATTN001"])
            result = _invoke(["pdf-rag", "export", "test-exp", "--format", "json"])
        # H-07 约定：--format json 在文本模式下是原始 JSON，在 --json 模式下是 envelope。
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["key"] == "ATTN001"

    def test_export_json_envelope_mode(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-exp"])
            _invoke(["pdf-rag", "add", "test-exp", "ATTN001"])
            result = _invoke(["pdf-rag", "export", "test-exp", "--format", "json"], json_output=True)
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["format"] == "json"
        assert len(payload["data"]["items"]) >= 1
        assert payload["meta"]["count"] >= 1

    def test_export_bibtex(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-exp"])
            _invoke(["pdf-rag", "add", "test-exp", "ATTN001"])
            result = _invoke(["pdf-rag", "export", "test-exp", "--format", "bibtex"])
        assert result.exit_code == 0
        assert "@" in result.output
        assert "Attention" in result.output

    def test_export_nonexistent(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            result = _invoke(["pdf-rag", "export", "nope"])
        assert "not found" in result.output

    def test_export_empty_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-exp"])
            result = _invoke(["pdf-rag", "export", "test-exp"])
        assert "empty" in result.output.lower()


class TestPdfRagImport:
    def test_import_from_search(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-imp"])
            result = _invoke(["pdf-rag", "import", "test-imp", "--search", "attention"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_from_collection(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-imp"])
            result = _invoke(["pdf-rag", "import", "test-imp", "--collection", "Machine Learning"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_from_tag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-imp"])
            result = _invoke(["pdf-rag", "import", "test-imp", "--tag", "transformer"])
        assert result.exit_code == 0
        assert "Imported" in result.output

    def test_import_no_source(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-imp"])
            result = _invoke(["pdf-rag", "import", "test-imp"])
        assert "specify" in result.output.lower() or "at least" in result.output.lower()

    def test_import_dedup(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-imp"])
            _invoke(["pdf-rag", "add", "test-imp", "ATTN001"])
            result = _invoke(["pdf-rag", "import", "test-imp", "--search", "attention"])
        assert result.exit_code == 0
        assert "skipped" in result.output.lower()

    def test_import_nonexistent_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            result = _invoke(["pdf-rag", "import", "nope", "--search", "test"])
        assert "not found" in result.output


class TestPdfRagSearch:
    def test_search_in_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-src"])
            _invoke(["pdf-rag", "add", "test-src", "ATTN001"])
            result = _invoke(["pdf-rag", "search", "attention", "--pdf-rag", "test-src"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_search_no_results(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-src"])
            _invoke(["pdf-rag", "add", "test-src", "ATTN001"])
            result = _invoke(["pdf-rag", "search", "xyznonexistent", "--pdf-rag", "test-src"])
        assert "No matching" in result.output or result.output.strip() == ""

    def test_search_by_author(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-src"])
            _invoke(["pdf-rag", "add", "test-src", "ATTN001"])
            result = _invoke(["pdf-rag", "search", "Vaswani", "--pdf-rag", "test-src"])
        assert result.exit_code == 0
        assert "ATTN001" in result.output

    def test_search_nonexistent_pdf_rag(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            result = _invoke(["pdf-rag", "search", "test", "--pdf-rag", "nope"])
        assert "not found" in result.output


class TestPdfRagIndexPruning:
    def test_remove_immediately_prunes_index(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-prune"])
            _invoke(["pdf-rag", "add", "test-prune", "ATTN001", "BERT002"])
            first = _invoke(["pdf-rag", "index", "test-prune", "--no-embed"])
            assert first.exit_code == 0, first.output
            removed = _invoke(["pdf-rag", "remove", "test-prune", "ATTN001"], json_output=True)
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "test-prune"], json_output=True)
        assert removed.exit_code == 0, removed.output
        data = json.loads(removed.stdout)["data"]
        assert data["pruned_index_items"] == 1
        assert data["index_refresh_required"] is False
        payload = json.loads(result.stdout)
        keys = [row["item_key"] for row in payload["data"]["results"]]
        assert "ATTN001" not in keys
        idx = RagIndex(tmp_path / "test-prune" / "pdf_rag.sqlite")
        try:
            chunk_keys = {chunk["item_key"] for chunk in idx.get_all_chunks()}
            assert "ATTN001" not in chunk_keys
            assert "BERT002" in chunk_keys
            assert idx.get_meta("pdf_hash:ATTN001") is None
            assert idx.get_meta("content_hash:ATTN001") is None
        finally:
            idx.close()

    def test_remove_warns_when_index_prune_fails(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "prune-warn"])
            _invoke(["pdf-rag", "add", "prune-warn", "ATTN001"])
            _invoke(["pdf-rag", "index", "prune-warn", "--no-embed"])
            with patch(
                "zotero_cli.cli.commands.pdf_rag.operations.prune_pdf_rag_items",
                side_effect=OSError("index busy"),
            ):
                removed = _invoke(["pdf-rag", "remove", "prune-warn", "ATTN001"], json_output=True)
        payload = json.loads(removed.stdout)
        assert payload["ok"] is True
        assert payload["data"]["index_refresh_required"] is True
        assert "could not prune the pdf_rag index" in removed.stderr

    def test_index_prunes_stale_manifest_entries_without_remove(self, tmp_path):
        from zotero_cli.pdf_rag import store as pdf_rag_module

        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "test-stale"])
            _invoke(["pdf-rag", "add", "test-stale", "ATTN001", "BERT002"])
            _invoke(["pdf-rag", "index", "test-stale", "--no-embed"])
            stored = pdf_rag_module.load_pdf_rag("test-stale")
            stored.items = [item for item in stored.items if item.key != "ATTN001"]
            pdf_rag_module.save_pdf_rag(stored)
            result = _invoke(["pdf-rag", "index", "test-stale", "--no-embed"], json_output=True)
        payload = json.loads(result.stdout)
        assert result.exit_code == 0, result.output
        assert payload["data"]["pruned"] == 1
        idx = RagIndex(tmp_path / "test-stale" / "pdf_rag.sqlite")
        try:
            assert "ATTN001" not in {chunk["item_key"] for chunk in idx.get_all_chunks()}
        finally:
            idx.close()

    def test_empty_pdf_rag_index_prunes_stale_chunks(self, tmp_path):
        from zotero_cli.pdf_rag import store as pdf_rag_module

        for force in (False, True):
            with isolate_pdf_rag_backend(tmp_path):
                name = f"empty-prune-{int(force)}"
                _invoke(["pdf-rag", "new", name])
                _invoke(["pdf-rag", "add", name, "ATTN001"])
                _invoke(["pdf-rag", "index", name, "--no-embed"])
                stored = pdf_rag_module.load_pdf_rag(name)
                stored.items = []
                pdf_rag_module.save_pdf_rag(stored)
                args = ["pdf-rag", "index", name, "--no-embed", "--json"]
                if force:
                    args.insert(-1, "--force")
                result = _invoke(args)
                payload = json.loads(result.stdout)
                assert result.exit_code == 0, result.output
                assert payload["data"]["items"] == 0
                assert payload["data"]["pruned"] == 1
                assert payload["data"]["chunks"] == 0
                idx = RagIndex(tmp_path / name / "pdf_rag.sqlite")
                try:
                    assert idx.get_all_chunks() == []
                finally:
                    idx.close()
                assert not (tmp_path / "_qdrant").exists()
                _invoke(["pdf-rag", "delete", name, "--yes"])
                _invoke(["pdf-rag", "new", name])


class TestPdfRagIndexDryRun:
    def test_index_dry_run_has_no_side_effects(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "dry-idx"])
            _invoke(["pdf-rag", "add", "dry-idx", "ATTN001"])
            with patch("zotero_cli.cli.commands.pdf_rag.indexing.MinerUParseCache") as cache_cls:
                result = _invoke(["pdf-rag", "index", "dry-idx", "--no-embed", "--dry-run"], json_output=True)
            cache_cls.assert_not_called()
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["would_index"] == 1
        assert not (tmp_path / "dry-idx" / "pdf_rag.sqlite").exists()
        assert not (tmp_path / "_qdrant").exists()

    def test_reindex_dry_run_reports_force(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "dry-re"])
            _invoke(["pdf-rag", "add", "dry-re", "ATTN001"])
            result = _invoke(["pdf-rag", "reindex", "dry-re", "--no-embed", "--dry-run"], json_output=True)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["force"] is True


class TestPdfRagForceFailurePreservesIndex:
    def test_force_mineru_failure_does_not_clear_working_index(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "keep-idx"])
            _invoke(["pdf-rag", "add", "keep-idx", "ATTN001"])
            first = _invoke(["pdf-rag", "index", "keep-idx", "--no-embed"])
            assert first.exit_code == 0, first.output
            with patch("zotero_cli.cli.commands.pdf_rag.indexing.MinerUParseCache") as cache_cls:
                cache_cls.return_value.ensure_many.side_effect = MinerUError("token not configured")
                failed = _invoke(["pdf-rag", "index", "keep-idx", "--force"], json_output=True)
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "keep-idx"], json_output=True)
        assert failed.exit_code == 3, failed.output
        assert json.loads(failed.stdout)["error"]["code"] == "configuration_error"
        payload = json.loads(result.stdout)
        assert payload["data"]["results"], "force rebuild failure must preserve the existing index"
        assert payload["data"]["results"][0]["item_key"] == "ATTN001"


class TestPdfRagCandidateIndexReplacement:
    def test_force_build_failure_preserves_available_index(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "candidate-idx"])
            _invoke(["pdf-rag", "add", "candidate-idx", "ATTN001"])
            first = _invoke(["pdf-rag", "index", "candidate-idx", "--no-embed"])
            assert first.exit_code == 0, first.output
            with patch(
                "zotero_cli.pdf_rag.index.RagIndex.insert_chunk_no_commit",
                side_effect=sqlite3.Error("candidate write failed"),
            ):
                failed = _invoke(["pdf-rag", "index", "candidate-idx", "--force", "--no-embed"])
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "candidate-idx"], json_output=True)
        assert failed.exit_code != 0
        payload = json.loads(result.stdout)
        assert payload["data"]["results"], "candidate build failure must preserve the available index"
        assert payload["data"]["results"][0]["item_key"] == "ATTN001"
        assert not list((tmp_path / "candidate-idx").glob("*.candidate"))


class TestPdfRagEmbedBehaviour:
    def test_embed_all_fail_returns_typed_error(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "emb-fail"])
            _invoke(["pdf-rag", "add", "emb-fail", "ATTN001"])
            _invoke(["pdf-rag", "index", "emb-fail", "--no-embed"])
            with patch("zotero_cli.cli.commands.pdf_rag.embedding.embed_texts", return_value=None):
                result = _invoke(
                    ["pdf-rag", "embed", "emb-fail", "--max-retries", "0", "--retry-sleep", "0"],
                    json_output=True,
                    env={
                        "ZOT_EMBEDDING_URL": "https://example.invalid/v1",
                        "ZOT_EMBEDDING_KEY": "fake",
                        "ZOT_EMBEDDING_MODEL": "bge-m3",
                    },
                )
        assert result.exit_code == 5, result.output
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "network_error"

    def test_embed_dry_run_does_not_call_provider(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "emb-dry"])
            _invoke(["pdf-rag", "add", "emb-dry", "ATTN001"])
            _invoke(["pdf-rag", "index", "emb-dry", "--no-embed"])
            with patch("zotero_cli.cli.commands.pdf_rag.embedding.embed_texts", side_effect=AssertionError("no call")):
                result = _invoke(["pdf-rag", "embed", "emb-dry", "--dry-run"], json_output=True)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["dry_run"] is True
        assert payload["data"]["index_exists"] is True

    def test_reindex_no_embed_skips_provider(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "re-noembed"])
            _invoke(["pdf-rag", "add", "re-noembed", "ATTN001"])
            with patch("zotero_cli.cli.commands.pdf_rag.indexing.embed_texts", side_effect=AssertionError("no embed")):
                result = _invoke(["pdf-rag", "reindex", "re-noembed", "--no-embed"], json_output=True)
        assert result.exit_code == 0, result.output
        assert "BM25" in result.output


class TestPdfRagQueryMetadata:
    def test_query_reports_mode_and_degradation(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "q-meta"])
            _invoke(["pdf-rag", "add", "q-meta", "ATTN001"])
            _invoke(["pdf-rag", "index", "q-meta", "--no-embed"])
            result = _invoke(
                ["pdf-rag", "query", "attention", "--pdf-rag", "q-meta", "--mode", "semantic"],
                json_output=True,
            )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["meta"]["mode"] == "semantic"
        assert payload["meta"]["requested_mode"] == "semantic"
        assert payload["meta"]["degraded"] is True
        assert payload["meta"]["semantic_available"] is False
        assert payload["data"]["results"] == []

    def test_query_top_k_must_be_positive(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "q-topk"])
            _invoke(["pdf-rag", "add", "q-topk", "ATTN001"])
            _invoke(["pdf-rag", "index", "q-topk", "--no-embed"])
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "q-topk", "--top-k", "0"])
        assert result.exit_code != 0


class TestPdfRagDeleteVectorCleanup:
    def test_delete_drops_collection_and_prunes_empty_store(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "vec-del"])
            _invoke(["pdf-rag", "add", "vec-del", "ATTN001"])
            _invoke(["pdf-rag", "index", "vec-del", "--no-embed"])
            assert (tmp_path / "_qdrant").exists()
            result = _invoke(["pdf-rag", "delete", "vec-del", "--yes"], json_output=True)
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "vec-del").exists()
        assert not (tmp_path / "_qdrant").exists()


class TestPdfRagImportCounts:
    def test_import_with_no_matches_warns_with_counts(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "imp-empty"])
            result = _invoke(["pdf-rag", "import", "imp-empty", "--search", "zzzzqqqxxx999"], json_output=True)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["data"]["resolved"] == 0
        assert payload["data"]["imported"] == 0
        assert "Warning" in result.output

    def test_import_success_envelope_has_no_would_add(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "imp-clean"])
            dry = _invoke(["pdf-rag", "import", "imp-clean", "--search", "attention", "--dry-run"], json_output=True)
            result = _invoke(["pdf-rag", "import", "imp-clean", "--search", "attention"], json_output=True)
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert "would_add" not in payload["data"]
        dry_payload = json.loads(dry.stdout)
        assert dry_payload["dry_run"] is True
        assert dry_payload["data"]["would_add"]


class TestPdfRagQueryReadSafety:
    def test_query_does_not_recreate_missing_vector_store(self, tmp_path):
        import shutil

        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "q-nostore"])
            _invoke(["pdf-rag", "add", "q-nostore", "ATTN001"])
            _invoke(["pdf-rag", "index", "q-nostore", "--no-embed"])
            assert (tmp_path / "_qdrant").exists()
            shutil.rmtree(tmp_path / "_qdrant")
            result = _invoke(["pdf-rag", "query", "attention", "--pdf-rag", "q-nostore"], json_output=True)
            created = (tmp_path / "_qdrant").exists()
        assert result.exit_code == 0, result.output
        assert created is False, "read-only query must not create the Qdrant store"
        payload = json.loads(result.stdout)
        assert payload["data"]["results"]

    def test_semantic_query_without_store_reports_degradation(self, tmp_path):
        import shutil

        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "q-nostore2"])
            _invoke(["pdf-rag", "add", "q-nostore2", "ATTN001"])
            _invoke(["pdf-rag", "index", "q-nostore2", "--no-embed"])
            shutil.rmtree(tmp_path / "_qdrant")
            result = _invoke(
                ["pdf-rag", "query", "attention", "--pdf-rag", "q-nostore2", "--mode", "semantic"],
                json_output=True,
            )
            created = (tmp_path / "_qdrant").exists()
        payload = json.loads(result.stdout)
        assert result.exit_code == 0, result.output
        assert created is False
        assert payload["meta"]["degraded"] is True
        assert any("no vectors" in note for note in payload["meta"]["degradation"])


class TestPdfRagQueryRerankDegradation:
    def test_rerank_failure_is_reported_in_meta(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "rr-meta"])
            _invoke(["pdf-rag", "add", "rr-meta", "ATTN001"])
            _invoke(["pdf-rag", "index", "rr-meta", "--no-embed"])
            with patch("zotero_cli.cli.commands.pdf_rag.query.rerank_chunks", return_value=None):
                result = _invoke(
                    ["pdf-rag", "query", "attention", "--pdf-rag", "rr-meta", "--rerank"],
                    json_output=True,
                    env={
                        "ZOT_RERANK_PROVIDER": "gitee",
                        "ZOT_RERANK_URL": "https://example.invalid/v1/rerank",
                        "ZOT_RERANK_KEY": "fake",
                        "ZOT_RERANK_MODEL": "fake-reranker",
                    },
                )
        payload = json.loads(result.stdout)
        assert result.exit_code == 0, result.output
        assert payload["meta"]["rerank_requested"] is True
        assert payload["meta"]["rerank_applied"] is False
        assert payload["meta"]["degraded"] is True
        assert any("reranker" in note for note in payload["meta"]["degradation"])


class TestPdfRagEmbedBounds:
    def _indexed_pdf_rag(self, name):
        _invoke(["pdf-rag", "new", name])
        _invoke(["pdf-rag", "add", name, "ATTN001", "BERT002"])
        _invoke(["pdf-rag", "index", name, "--no-embed"])

    _EMBED_ENV = {
        "ZOT_EMBEDDING_URL": "https://example.invalid/v1",
        "ZOT_EMBEDDING_KEY": "fake",
        "ZOT_EMBEDDING_MODEL": "bge-m3",
    }

    def test_permanent_provider_failure_fast_fails(self, tmp_path):
        import time

        with isolate_pdf_rag_backend(tmp_path):
            self._indexed_pdf_rag("emb-fast")
            calls = {"count": 0}

            def failing(*_args: object, **_kwargs: object) -> None:
                calls["count"] += 1
                return None

            with patch("zotero_cli.cli.commands.pdf_rag.embedding.embed_texts", side_effect=failing):
                started = time.monotonic()
                result = _invoke(
                    ["pdf-rag", "embed", "emb-fast", "--max-retries", "9", "--retry-sleep", "0", "--limit", "10"],
                    json_output=True,
                    env=self._EMBED_ENV,
                )
                duration = time.monotonic() - started
        assert result.exit_code == 5, result.output
        assert duration < 5.0, f"permanent failure retried too long: {duration:.1f}s"
        assert calls["count"] <= 20, f"too many provider calls: {calls['count']}"
        assert json.loads(result.stdout)["error"]["code"] == "network_error"

    def test_global_deadline_bounds_retrying(self, tmp_path):
        import time

        with isolate_pdf_rag_backend(tmp_path):
            self._indexed_pdf_rag("emb-deadline")

            def slow_failure(*_args: object, **_kwargs: object) -> None:
                time.sleep(0.15)
                return None

            with patch("zotero_cli.cli.commands.pdf_rag.embedding.embed_texts", side_effect=slow_failure):
                started = time.monotonic()
                result = _invoke(
                    [
                        "pdf-rag",
                        "embed",
                        "emb-deadline",
                        "--max-retries",
                        "50",
                        "--retry-sleep",
                        "0.2",
                        "--deadline-seconds",
                        "0.4",
                        "--limit",
                        "10",
                    ],
                    json_output=True,
                    env=self._EMBED_ENV,
                )
                duration = time.monotonic() - started
        assert result.exit_code == 5, result.output
        assert duration < 8.0, f"deadline did not bound retrying: {duration:.1f}s"
        combined = result.stdout + result.stderr
        assert "deadline" in combined.lower()


class TestPdfRagIndexStatsPathEscaping:
    def test_stats_read_handles_special_chars_in_path(self, tmp_path):
        from zotero_cli.cli.commands.pdf_rag.indexing import _read_index_stats

        index_path = tmp_path / "legacy ?name" / "pdf_rag.sqlite"
        index_path.parent.mkdir()
        idx = RagIndex(index_path)
        try:
            idx.insert_chunk("KEY1", "metadata", "sentinel text", 2)
            idx.commit()
        finally:
            idx.close()
        stats = _read_index_stats(index_path)
        assert stats["index_exists"] is True
        assert stats["chunks"] == 1


class TestPdfRagDeleteVectorCleanupWithoutIndex:
    def test_delete_drops_collection_even_when_index_file_is_missing(self, tmp_path):
        from zotero_cli.pdf_rag.vector_store import QdrantVectorStore

        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "vec-noindex"])
            store = QdrantVectorStore(tmp_path / "_qdrant", "pdf_rag_vec-noindex")
            try:
                store.upsert([1], [[0.1, 0.2, 0.3]], [{"item_key": "ATTN001", "source": "metadata"}])
            finally:
                store.close()
            assert (tmp_path / "_qdrant").exists()
            assert not (tmp_path / "vec-noindex" / "pdf_rag.sqlite").exists()
            result = _invoke(["pdf-rag", "delete", "vec-noindex", "--yes"], json_output=True)
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "vec-noindex").exists()
        assert not (tmp_path / "_qdrant").exists()

    def test_delete_leaves_foreign_directory_untouched(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "vec-foreign"])
            foreign = tmp_path / "_qdrant"
            foreign.mkdir()
            user_file = foreign / "user-notes.txt"
            user_file.write_text("keep me", encoding="utf-8")
            result = _invoke(["pdf-rag", "delete", "vec-foreign", "--yes"], json_output=True)
            kept = user_file.exists()
        assert result.exit_code == 0, result.output
        assert kept is True, "pdf_rag delete must not prune an unowned directory"

    def test_delete_warns_but_proceeds_when_vector_cleanup_fails(self, tmp_path):
        with isolate_pdf_rag_backend(tmp_path):
            _invoke(["pdf-rag", "new", "vec-warn"])
            with patch(
                "zotero_cli.cli.commands.pdf_rag.operations.drop_pdf_rag_vector_collection",
                side_effect=RuntimeError("store locked"),
            ):
                result = _invoke(["pdf-rag", "delete", "vec-warn", "--yes"], json_output=True)
            exists = (tmp_path / "vec-warn").exists()
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["data"]["deleted"] is True
        assert exists is False
        assert "could not remove vector data" in result.stderr
