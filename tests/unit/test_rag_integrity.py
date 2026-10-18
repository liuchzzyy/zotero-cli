"""Regression guards for RAG input alignment and bounded chunking."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from qdrant_client.models import Distance, VectorParams

from zotero_cli.config import RerankConfig
from zotero_cli.core.rag import cascade_chunk, chunk_mineru_content, chunk_text, infer_pdf_kind
from zotero_cli.core.rag.providers.gitee import GiteeEmbeddingProvider, _extract_scores
from zotero_cli.core.rag.rerank import rerank_chunks
from zotero_cli.core.rag.vector_store import QdrantVectorStore


def test_embedding_restores_input_order_with_batch_local_indices() -> None:
    responses = []
    for first, second in ((1.0, 2.0), (3.0, 4.0)):
        response = MagicMock(ok=True)
        response.json.return_value = {
            "data": [
                {"index": 1, "embedding": [second]},
                {"index": 0, "embedding": [first]},
            ]
        }
        responses.append(response)
    progress = MagicMock()
    provider = GiteeEmbeddingProvider(api_key="test", batch_size=2)
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", side_effect=responses) as post:
        vectors = provider.embed(["alpha", "beta", "gamma", "delta"], progress)

    assert vectors == [[1.0], [2.0], [3.0], [4.0]]
    assert [call.kwargs["json"]["input"] for call in post.call_args_list] == [["alpha", "beta"], ["gamma", "delta"]]
    assert [call.args for call in progress.call_args_list] == [(2, 4), (4, 4)]


@pytest.mark.parametrize(
    "indices",
    [
        [],
        [0],
        [0, 1, 2],
        [0, 0],
        [-1, 0],
        [0, 2],
        [0, None],
        [0, 1.0],
        [0, True],
        [0, "1"],
    ],
    ids=["empty", "short", "long", "duplicate", "negative", "out-of-range", "null", "float", "bool", "string"],
)
def test_embedding_rejects_invalid_counts_or_indices(indices: list[object]) -> None:
    response = MagicMock(ok=True)
    response.json.return_value = {"data": [{"index": index, "embedding": [1.0]} for index in indices]}
    progress = MagicMock()
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="embedding"):
            GiteeEmbeddingProvider(api_key="test").embed(["alpha", "beta"], progress)
    progress.assert_not_called()


def test_embedding_rejects_partially_missing_indices() -> None:
    response = MagicMock(ok=True)
    response.json.return_value = {"data": [{"index": 0, "embedding": [1.0]}, {"embedding": [2.0]}]}
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="index"):
            GiteeEmbeddingProvider(api_key="test").embed(["alpha", "beta"])


def test_embedding_accepts_complete_unindexed_legacy_response() -> None:
    response = MagicMock(ok=True)
    response.json.return_value = {"data": [{"embedding": [1.0]}, {"embedding": [2.0]}]}
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        assert GiteeEmbeddingProvider(api_key="test").embed(["alpha", "beta"]) == [[1.0], [2.0]]


@pytest.fixture
def vector_store_stub(tmp_path: Path) -> tuple[QdrantVectorStore, MagicMock]:
    client = MagicMock()
    client.collection_exists.return_value = False
    with patch("zotero_cli.core.rag.vector_store.QdrantClient", return_value=client):
        store = QdrantVectorStore(tmp_path / "vectors", "test")
    return store, client


@pytest.mark.parametrize(
    ("ids", "vectors", "payloads"),
    [
        ([1, 2], [[1.0]], None),
        ([1], [[1.0], [2.0]], None),
        ([1], [], None),
        ([], [[1.0]], None),
        ([1, 2], [[1.0], [2.0]], [{}]),
        ([1], [[1.0]], [{}, {}]),
        ([], [], [{}]),
    ],
    ids=["short-vectors", "long-vectors", "missing-vectors", "no-ids", "short-payloads", "long-payloads", "no-points"],
)
def test_upsert_rejects_length_mismatches_before_writes(
    vector_store_stub: tuple[QdrantVectorStore, MagicMock],
    ids: list[int],
    vectors: list[list[float]],
    payloads: list[dict] | None,
) -> None:
    store, client = vector_store_stub
    with pytest.raises(ValueError, match="length"):
        store.upsert(ids, vectors, payloads)
    client.create_collection.assert_not_called()
    client.upsert.assert_not_called()


@pytest.mark.parametrize("vectors", [[[], []], [[1.0], [1.0, 2.0]], [[1.0, 2.0], []]])
def test_upsert_rejects_invalid_dimensions_before_writes(
    vector_store_stub: tuple[QdrantVectorStore, MagicMock], vectors: list[list[float]]
) -> None:
    store, client = vector_store_stub
    with pytest.raises(ValueError, match="dimension"):
        store.upsert([1, 2], vectors)
    client.create_collection.assert_not_called()
    client.upsert.assert_not_called()


def test_upsert_rejects_existing_collection_dimension_before_writes(
    vector_store_stub: tuple[QdrantVectorStore, MagicMock],
) -> None:
    store, client = vector_store_stub
    client.collection_exists.return_value = True
    client.get_collection.return_value.config.params.vectors = VectorParams(size=3, distance=Distance.COSINE)
    with pytest.raises(ValueError, match="dimension"):
        store.upsert([1], [[1.0, 2.0]])
    client.create_collection.assert_not_called()
    client.upsert.assert_not_called()


def test_list_ids_follows_every_scroll_page_even_with_zero_offset(
    vector_store_stub: tuple[QdrantVectorStore, MagicMock],
) -> None:
    store, client = vector_store_stub
    client.collection_exists.return_value = True
    client.scroll.side_effect = [
        ([SimpleNamespace(id=7)], 0),
        ([SimpleNamespace(id=0)], 19),
        ([SimpleNamespace(id=19)], None),
    ]
    assert store.list_ids() == [7, 0, 19]
    assert [call.kwargs.get("offset") for call in client.scroll.call_args_list] == [None, 0, 19]
    assert all(call.kwargs["with_vectors"] is False for call in client.scroll.call_args_list)


@pytest.mark.parametrize(
    "indices",
    [[0, 0], [0, 2], [0, -1], [0, None], [0, True], [0, 1.0], [0, "1"], [0], [0, 1, 1]],
    ids=["duplicate", "out-of-range", "negative", "null", "bool", "float", "string", "incomplete", "extra-duplicate"],
)
def test_reranker_rejects_invalid_indexed_results(indices: list[object]) -> None:
    results = [{"index": index, "relevance_score": 0.5} for index in indices]
    with pytest.raises(RuntimeError):
        _extract_scores({"results": results}, expected_count=2)


def test_reranker_rejects_partially_missing_indices() -> None:
    with pytest.raises(RuntimeError):
        _extract_scores({"results": [{"index": 0, "score": 0.1}, {"score": 0.9}]}, expected_count=2)


def test_invalid_indexed_reranker_keeps_retrieval_ranking(capsys: pytest.CaptureFixture[str]) -> None:
    candidates = [(1, 0.9, {"content": "alpha"}), (2, 0.8, {"content": "beta"})]
    response = MagicMock(ok=True)
    response.json.return_value = {"results": [{"index": 0, "score": 0.1}, {"index": 0, "score": 0.9}]}
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        result = rerank_chunks("query", candidates, RerankConfig(api_key="test"))
    assert result is None
    assert [candidate[0] for candidate in candidates] == [1, 2]
    assert "Returning retrieval ranking" in capsys.readouterr().err


def test_reranker_still_accepts_fully_unindexed_scores() -> None:
    assert _extract_scores({"results": [{"score": 0.1}, {"score": 0.9}]}, expected_count=2) == [0.1, 0.9]


@pytest.mark.parametrize("overlap", [8, 9])
def test_cascade_chunk_rejects_nonprogressing_overlap_before_character_split(overlap: int) -> None:
    # Guard the dangerous boundary so a regression cannot hang the pytest process.
    with patch("zotero_cli.core.rag.chunking._chunk_by_char", side_effect=AssertionError("unsafe character split")):
        with pytest.raises(ValueError, match="overlap"):
            cascade_chunk("x" * 32, max_chars=8, overlap=overlap)


@pytest.mark.parametrize(
    ("max_chars", "overlap", "message"),
    [(0, 0, "max_chars"), (-1, 0, "max_chars"), (True, 0, "max_chars"), (1.5, 0, "max_chars"), (8, -1, "overlap")],
)
def test_cascade_chunk_validates_parameters_even_for_empty_text(max_chars: int, overlap: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cascade_chunk("", max_chars=max_chars, overlap=overlap)


@pytest.mark.parametrize(("chunker", "content"), [(chunk_text, ""), (chunk_mineru_content, [])])
@pytest.mark.parametrize(
    ("max_tokens", "overlap", "message"),
    [
        (0, 0, "max_tokens"),
        (-1, 0, "max_tokens"),
        (True, 0, "max_tokens"),
        (1.5, 0, "max_tokens"),
        (2, -1, "overlap"),
        (2, 8, "overlap"),
        (2, 9, "overlap"),
        (2, True, "overlap"),
        (2, 0.5, "overlap"),
    ],
)
def test_public_chunkers_validate_parameters_before_reading_content(
    chunker: Callable[..., object], content: object, max_tokens: int, overlap: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        chunker(content, "Paper", max_tokens=max_tokens, overlap=overlap)


def test_cascade_chunk_valid_overlap_makes_progress() -> None:
    assert cascade_chunk("abcdefghij", max_chars=4, overlap=1) == ["abcd", "defg", "ghij", "j"]


def test_cascade_chunk_preserves_english_sentence_spaces_and_size_budget() -> None:
    text = "First sentence. Second sentence! Third sentence? Fourth sentence."
    chunks = cascade_chunk(text, max_chars=36, overlap=0)
    assert "First sentence. Second sentence!" in chunks[0]
    assert "".join(chunks) == text
    assert all(len(chunk) <= 36 for chunk in chunks)


def test_cascade_chunk_does_not_insert_spaces_between_chinese_sentences() -> None:
    assert cascade_chunk("第一句。第二句！第三句。", max_chars=8, overlap=0) == ["第一句。第二句！", "第三句。"]


@pytest.mark.parametrize("section", ["Abstract", "Introduction"])
def test_short_main_article_citing_supporting_information_remains_main(section: str) -> None:
    text = f"# Battery mechanisms\nAuthor One\n{section}: We report stable cycling. See Supporting Information for methods."
    assert infer_pdf_kind(text, "paper.pdf") == "main"


@pytest.mark.parametrize("cover", ["Supporting Information", "# Supplementary Information"])
def test_standalone_supplementary_cover_remains_supplementary_with_main_hints(cover: str) -> None:
    text = f"{cover}\nBattery mechanisms\nDOI: 10.1234/example\nAbstract of the associated article.\nFigure S1"
    assert infer_pdf_kind(text, "paper.pdf") == "supplementary"


@pytest.mark.parametrize(
    "embedding",
    ["oops", [], [1.0, "x"], [True], [float("nan")], [float("inf")], None],
    ids=["string", "empty", "mixed", "bool", "nan", "inf", "null"],
)
def test_embedding_rejects_invalid_vector_values(embedding: object) -> None:
    response = MagicMock(ok=True)
    response.json.return_value = {"data": [{"embedding": embedding}]}
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="embedding"):
            GiteeEmbeddingProvider(api_key="test").embed(["alpha"])


def test_embedding_rejects_inconsistent_vector_dimensions() -> None:
    response = MagicMock(ok=True)
    response.json.return_value = {"data": [{"embedding": [1.0]}, {"embedding": [1.0, 2.0]}]}
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="dimension"):
            GiteeEmbeddingProvider(api_key="test").embed(["alpha", "beta"])


def test_embedding_rejects_non_object_response_shape() -> None:
    response = MagicMock(ok=True)
    response.json.return_value = []
    with patch("zotero_cli.core.rag.providers.gitee.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="shape"):
            GiteeEmbeddingProvider(api_key="test").embed(["alpha"])


def test_cascade_chunk_never_returns_empty_chunks_for_cjk_punctuation() -> None:
    assert cascade_chunk("d。", max_chars=1, overlap=0) == ["d", "。"]
    assert cascade_chunk("第一句。第二句！", max_chars=2, overlap=0) == [
        "第一",
        "句。",
        "第二",
        "句！",
    ]
    assert cascade_chunk("", max_chars=8, overlap=0) == []
    assert all(chunk for chunk in cascade_chunk("中" * 50 + "。", max_chars=10, overlap=0))


def test_sentence_aligned_chunks_do_not_overlap_regardless_of_overlap_setting() -> None:
    text = "First sentence. Second sentence! Third sentence? Fourth sentence."
    assert cascade_chunk(text, max_chars=36, overlap=0) == cascade_chunk(text, max_chars=36, overlap=8)
    assert "".join(cascade_chunk(text, max_chars=36, overlap=8)) == text


def test_character_fallback_still_honours_overlap() -> None:
    assert cascade_chunk("abcdefghij", max_chars=4, overlap=1) == ["abcd", "defg", "ghij", "j"]


def test_chunk_text_long_cjk_section_has_no_empty_chunks() -> None:
    text = "。".join("这是关于二氧化锰电极材料的中文测试句子" for _ in range(60)) + "。"
    chunks = chunk_text(text, "Paper", max_tokens=50, overlap=0)
    assert chunks
    assert all(len(chunk.strip()) > len("[Paper] ") - 1 for chunk in chunks)
