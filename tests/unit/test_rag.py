"""RAG 引擎、FTS5 词项索引与 Gitee 向量化测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.config import EmbeddingConfig
from zotero_cli.workspace.rag import (
    build_metadata_chunk,
    chunk_mineru_content,
    chunk_text,
    clean_html,
    embed_texts,
    filter_ranked_results_by_pdf_kind,
    get_pdf_kind_from_source,
    infer_pdf_kind,
    reciprocal_rank_fusion,
    tokenize,
    weighted_reciprocal_rank_fusion,
)
from zotero_cli.workspace.rag.index import RagIndex, _fts_query


class TestRagIndex:
    def test_create_index(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            assert (tmp_path / "test.idx.sqlite").exists()
        finally:
            idx.close()

    def test_insert_and_get_chunks(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.insert_chunk("ABC123", "metadata", "Title: Test Paper\nAbstract: about attention")
            idx.insert_chunk("ABC123", "pdf", "[Test Paper > Introduction] We study attention...")
            chunks = idx.get_all_chunks()
            assert len(chunks) == 2
            assert chunks[0]["item_key"] == "ABC123"
            assert chunks[0]["source"] == "metadata"
        finally:
            idx.close()

    def test_search_bm25_returns_matching_chunk(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            c1 = idx.insert_chunk("A", "pdf", "attention mechanism in transformers")
            c2 = idx.insert_chunk("B", "pdf", "convolutional neural network for images")
            results = idx.search_bm25("attention mechanism")
            ids = [cid for cid, _score, _chunk in results]
            assert c1 in ids
            assert c2 not in ids
            assert results[0][0] == c1
            assert results[0][1] > 0
        finally:
            idx.close()

    def test_search_bm25_no_match(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.insert_chunk("A", "pdf", "attention mechanism")
            assert idx.search_bm25("zzzzqqqxxx999") == []
        finally:
            idx.close()

    def test_set_and_get_meta(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.set_meta("chunk_count", "42")
            idx.set_meta("has_embeddings", "false")
            assert idx.get_meta("chunk_count") == "42"
            assert idx.get_meta("has_embeddings") == "false"
            assert idx.get_meta("nonexistent") is None
        finally:
            idx.close()

    def test_delete_meta(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.set_meta("pipeline:item", "mineru-json-v1")
            idx.delete_meta("pipeline:item")
            assert idx.get_meta("pipeline:item") is None
        finally:
            idx.close()

    def test_clear_index(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.insert_chunk("ABC123", "metadata", "test")
            idx.clear()
            assert len(idx.get_all_chunks()) == 0
            assert idx.search_bm25("test") == []
        finally:
            idx.close()

    def test_get_indexed_keys(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            idx.insert_chunk("ABC123", "metadata", "text a")
            idx.insert_chunk("DEF456", "metadata", "text b")
            idx.insert_chunk("ABC123", "pdf", "text c")
            assert idx.get_indexed_keys() == {"ABC123", "DEF456"}
        finally:
            idx.close()

    def test_delete_chunks_for_item(self, tmp_path):
        idx = RagIndex(tmp_path / "test.idx.sqlite")
        try:
            c1 = idx.insert_chunk("ABC123", "metadata", "keep me")
            idx.insert_chunk("ABC123", "pdf", "drop me")
            idx.insert_chunk("DEF456", "metadata", "other item")
            deleted = idx.delete_chunks_for_item("ABC123")
            assert c1 in deleted
            assert idx.get_indexed_keys() == {"DEF456"}
        finally:
            idx.close()


class TestFtsQuery:
    def test_multi_term_or_query(self):
        assert _fts_query("attention mechanism") == '"attention" OR "mechanism"'

    def test_empty_query(self):
        assert _fts_query("!!!") == ""

    def test_quotes_are_escaped(self):
        assert _fts_query('say "hi"') == '"say" OR "hi"'


class TestTokenizer:
    def test_basic(self):
        assert tokenize("Hello World") == ["hello", "world"]

    def test_punctuation(self):
        assert tokenize("attention-based, model.") == ["attention-based", "model"]

    def test_empty(self):
        assert tokenize("") == []

    def test_numbers(self):
        assert tokenize("GPT-4 has 1.7T params") == ["gpt-4", "has", "1.7t", "params"]


class TestChunking:
    def test_mineru_json_preserves_section_page_and_block_type(self):
        content = [
            {"type": "header", "text": "Journal banner", "page_idx": 0},
            {"type": "text", "text": "Methods", "text_level": 2, "page_idx": 1},
            {"type": "text", "text": "Electrochemical evidence", "page_idx": 1},
        ]
        chunks = chunk_mineru_content(content, "Paper")
        assert len(chunks) == 2
        assert chunks[0].section == "Methods"
        assert chunks[0].page == 2
        assert chunks[1].block_type == "text"
        assert "Journal banner" not in "\n".join(chunk.content for chunk in chunks)

    def test_short_text_single_chunk(self):
        chunks = chunk_text("Short text.", "Paper Title", max_tokens=500)
        assert len(chunks) == 1
        assert "Short text." in chunks[0]

    def test_heading_split(self):
        text = "## Introduction\nSome intro text here.\n\n## Methods\nSome methods text here."
        chunks = chunk_text(text, "Paper", max_tokens=500)
        assert len(chunks) == 2

    def test_long_section_paragraph_split(self):
        long_para = "word " * 600
        text = f"## Section\n{long_para}"
        chunks = chunk_text(text, "Paper", max_tokens=500)
        assert len(chunks) >= 2

    def test_chunk_prefix(self):
        text = "## Introduction\nSome text here."
        chunks = chunk_text(text, "My Paper", max_tokens=500)
        assert "[My Paper > Introduction]" in chunks[0]

    def test_metadata_chunk(self):
        chunk = build_metadata_chunk(
            title="Attention Is All You Need",
            authors="Vaswani et al.",
            abstract="We propose a new architecture...",
            tags=["transformer", "attention"],
        )
        assert "Attention Is All You Need" in chunk
        assert "Vaswani et al." in chunk
        assert "transformer" in chunk

    def test_infer_pdf_kind_supplementary(self):
        text = "Electronic Supplementary Material (ESI). Figure S1. Extra synthesis details."
        assert infer_pdf_kind(text, "paper.pdf") == "supplementary"

    def test_infer_pdf_kind_main(self):
        text = "Introduction\nZinc-ion batteries have emerged as a promising candidate."
        assert infer_pdf_kind(text, "paper.pdf") == "main"

    def test_get_pdf_kind_from_source(self):
        assert get_pdf_kind_from_source("pdf:main:ABC:file.pdf") == "main"
        assert get_pdf_kind_from_source("pdf:supplementary:XYZ:file.pdf") == "supplementary"
        assert get_pdf_kind_from_source("note:ABC") is None

    def test_filter_ranked_results_by_pdf_kind(self):
        results = [
            (1, 0.9, {"source": "pdf:main:A:file.pdf", "item_key": "A", "content": "main"}),
            (2, 0.8, {"source": "pdf:supplementary:B:file.pdf", "item_key": "A", "content": "supp"}),
            (3, 0.7, {"source": "note:N1", "item_key": "A", "content": "note"}),
        ]
        filtered = filter_ranked_results_by_pdf_kind(results, "supplementary")
        assert len(filtered) == 1
        assert filtered[0][2]["source"].startswith("pdf:supplementary:")

    def test_abbreviation_heading_normalized_to_first_supplementary_label(self):
        text = "# Abbreviation\n\nZinc hydroxide sulfate: ZHS\n\nFigure S1. A supplementary figure."
        chunks = chunk_text(text, "Paper | Kind: supplementary", max_tokens=500)
        assert any("> Figure S1]" in chunk for chunk in chunks)

    def test_supplementary_figures_split_into_separate_sections(self):
        text = (
            "# Abbreviation\n\n"
            "Zinc hydroxide sulfate: ZHS\n\n"
            "Figure S1. First figure description.\n\n"
            "Figure S2. Second figure description."
        )
        chunks = chunk_text(text, "Paper | Kind: supplementary", max_tokens=500)
        joined = "\n".join(chunks)
        assert "> Figure S1]" in joined
        assert "> Figure S2]" in joined


class TestEmbedding:
    def _gitee_response(self, embeddings):
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"data": [{"embedding": e} for e in embeddings]}
        return resp

    def test_embed_texts_not_configured(self):
        config = EmbeddingConfig(url="", api_key="", model="")
        result = embed_texts(["hello"], config)
        assert result is None

    def test_embed_texts_gitee_api_call(self):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="bge-m3")
        resp = self._gitee_response([[0.1, 0.2, 0.3]])
        with patch("zotero_cli.workspace.rag.providers.gitee.requests.post", return_value=resp) as mock_post:
            result = embed_texts(["hello world"], config)

        assert result == [[0.1, 0.2, 0.3]]
        args, kwargs = mock_post.call_args
        assert args[0] == "https://ai.gitee.com/v1/embeddings"
        assert kwargs["json"]["model"] == "bge-m3"
        assert kwargs["json"]["input"] == ["hello world"]
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer key"
        assert headers["X-Failover-Enabled"] == "true"

    def test_embed_texts_surfaces_provider_error(self, capsys):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="bge-m3")
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            side_effect=RuntimeError("boom"),
        ):
            result = embed_texts(["hello"], config)
        assert result is None
        captured = capsys.readouterr()
        assert "WARN" in captured.err
        assert "gitee" in captured.err
        assert "boom" in captured.err

    def test_embed_texts_silent_when_not_configured(self, capsys):
        config = EmbeddingConfig(url="", api_key="", model="")
        result = embed_texts(["hello"], config)
        assert result is None
        captured = capsys.readouterr()
        assert captured.err == ""


class TestRRF:
    def test_reciprocal_rank_fusion(self):
        ranking1 = [(1, 0.9, {"id": 1}), (2, 0.8, {"id": 2}), (3, 0.7, {"id": 3})]
        ranking2 = [(3, 0.95, {"id": 3}), (1, 0.85, {"id": 1}), (2, 0.5, {"id": 2})]
        fused = reciprocal_rank_fusion(ranking1, ranking2)
        ids = [cid for cid, _, _ in fused]
        assert set(ids) == {1, 2, 3}
        assert ids[0] in (1, 3)

    def test_weighted_reciprocal_rank_fusion_semantic_dominant(self):
        bm25 = [(1, 9.0, {"id": 1}), (2, 8.0, {"id": 2})]
        semantic = [(2, 0.9, {"id": 2}), (1, 0.8, {"id": 1})]
        fused = weighted_reciprocal_rank_fusion([bm25, semantic], weights=[0.2, 0.8], k=60)
        ids = [cid for cid, _, _ in fused]
        assert set(ids) == {1, 2}
        assert ids[0] == 2


class TestMetadataCleaning:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("&#65;", "A"),
            ("&#x41;", "A"),
            ("&#1114112;", "&#1114112;"),
            ("&#x110000;", "&#x110000;"),
            ("&#xD800;", "&#xD800;"),
            ("&#0;", "&#0;"),
            ("&#999999999999999999999;", "&#999999999999999999999;"),
        ],
    )
    def test_numeric_entities_never_crash(self, raw, expected):
        assert clean_html(raw) == expected

    def test_well_formed_entities_still_decode(self):
        assert clean_html("&amp; &lt; &gt; &quot; &apos;") == "& < > \" '"


class TestEmbeddingInputType:
    @pytest.fixture(autouse=True)
    def _clear_input_type_rejection_cache(self):
        from zotero_cli.workspace.rag.providers.gitee import _reset_input_type_cache

        _reset_input_type_cache()
        yield
        _reset_input_type_cache()

    def _response(self, embeddings, *, ok=True, status_code=200, text=""):
        resp = MagicMock()
        resp.ok = ok
        resp.status_code = status_code
        resp.text = text
        resp.json.return_value = {"data": [{"embedding": vector} for vector in embeddings]}
        return resp

    def test_query_input_type_is_sent_to_the_provider(self):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="bge-m3")
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            return_value=self._response([[1.0, 2.0]]),
        ) as mock_post:
            assert embed_texts(["query text"], config, input_type="query") == [[1.0, 2.0]]
        payload = mock_post.call_args.kwargs["json"]
        assert payload["input_type"] == "query"
        assert payload["input"] == ["query text"]

    def test_document_input_type_keeps_request_shape(self):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="bge-m3")
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            return_value=self._response([[1.0]]),
        ) as mock_post:
            assert embed_texts(["document text"], config) == [[1.0]]
        payload = mock_post.call_args.kwargs["json"]
        assert "input_type" not in payload
        assert payload["input"] == ["document text"]

    def test_query_input_type_falls_back_when_provider_rejects_field(self):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="bge-m3")
        rejected = MagicMock(ok=False, status_code=400, text="unknown field input_type")
        accepted = self._response([[0.5]])
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            side_effect=[rejected, accepted],
        ) as mock_post:
            assert embed_texts(["query text"], config, input_type="query") == [[0.5]]
        assert mock_post.call_count == 2
        assert "input_type" not in mock_post.call_args_list[1].kwargs["json"]

    def test_unsupported_provider_is_not_silently_sent_to_gitee(self, capsys):
        config = EmbeddingConfig(url="https://example.invalid/v1", api_key="key", model="model", provider="other")
        with patch("zotero_cli.workspace.rag.providers.gitee.requests.post") as mock_post:
            assert embed_texts(["text"], config) is None
        mock_post.assert_not_called()
        assert "Unsupported embedding provider" in capsys.readouterr().err

    def test_invalid_input_type_is_rejected(self):
        from zotero_cli.workspace.rag.providers.gitee import GiteeEmbeddingProvider

        with pytest.raises(ValueError, match="input_type"):
            GiteeEmbeddingProvider(api_key="key").embed(["text"], input_type="other")

    def test_input_type_rejection_is_remembered_across_calls(self):
        config = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="strict-model")
        rejected = MagicMock(ok=False, status_code=400, text="unknown field input_type")
        accepted = self._response([[0.5]])
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            side_effect=[rejected, accepted, accepted],
        ) as mock_post:
            assert embed_texts(["q1"], config, input_type="query") == [[0.5]]
            assert embed_texts(["q2"], config, input_type="query") == [[0.5]]

        assert mock_post.call_count == 3
        assert mock_post.call_args_list[0].kwargs["json"]["input_type"] == "query"
        assert "input_type" not in mock_post.call_args_list[1].kwargs["json"]
        assert "input_type" not in mock_post.call_args_list[2].kwargs["json"]

    def test_input_type_rejection_cache_is_scoped_to_endpoint_and_model(self):
        rejected = MagicMock(ok=False, status_code=422, text="")
        accepted = self._response([[1.0]])
        strict = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="strict-model-a")
        lenient = EmbeddingConfig(url="https://ai.gitee.com/v1", api_key="key", model="lenient-model-b")
        with patch(
            "zotero_cli.workspace.rag.providers.gitee.requests.post",
            side_effect=[rejected, accepted, accepted],
        ) as mock_post:
            assert embed_texts(["q"], strict, input_type="query") == [[1.0]]
            assert embed_texts(["q"], lenient, input_type="query") == [[1.0]]

        assert mock_post.call_count == 3
        assert mock_post.call_args_list[2].kwargs["json"]["input_type"] == "query"


class TestRRFValidation:
    def test_weights_must_match_rankings(self):
        ranking = [(1, 0.9, {"id": 1})]
        with pytest.raises(ValueError, match="one entry per ranking"):
            weighted_reciprocal_rank_fusion([ranking], weights=[1.0, 2.0], k=60)
        with pytest.raises(ValueError, match="one entry per ranking"):
            weighted_reciprocal_rank_fusion([ranking, ranking], weights=[1.0], k=60)

    @pytest.mark.parametrize("k", [0, -1, -60, True, 1.5])
    def test_k_must_be_a_positive_integer(self, k):
        with pytest.raises(ValueError, match="k must be a positive integer"):
            weighted_reciprocal_rank_fusion([[]], k=k)

    @pytest.mark.parametrize("weight", [float("nan"), float("inf"), -1.0, True, "1"])
    def test_weights_must_be_finite_non_negative_numbers(self, weight):
        with pytest.raises(ValueError):
            weighted_reciprocal_rank_fusion([[]], weights=[weight], k=60)
