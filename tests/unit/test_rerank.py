from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.config import RerankConfig
from zotero_cli.core.providers.gitee import GiteeRerankerProvider, _extract_scores
from zotero_cli.core.rerank import rerank_chunks


def test_rerank_chunks_sorts_selected_candidates():
    candidates = [
        (1, 0.9, {"content": "weak", "item_key": "A", "source": "metadata"}),
        (2, 0.8, {"content": "strong", "item_key": "B", "source": "metadata"}),
        (3, 0.7, {"content": "outside top_n", "item_key": "C", "source": "metadata"}),
    ]

    class FakeProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def score(self, query, documents, progress_callback=None):
            assert query == "battery"
            assert documents == ["weak", "strong"]
            return [0.1, 0.9]

    with patch("zotero_cli.core.rerank.GiteeRerankerProvider", FakeProvider):
        result = rerank_chunks(
            "battery",
            candidates,
            RerankConfig(provider="gitee", url="https://ai.gitee.com/v1/rerank", api_key="k", model="m"),
            top_n=2,
        )

    assert result is not None
    assert [cid for cid, _score, _chunk in result] == [2, 1, 3]
    assert result[0][1] == pytest.approx(0.9)


def test_rerank_chunks_returns_none_when_not_configured():
    result = rerank_chunks(
        "battery",
        [(1, 0.9, {"content": "text"})],
        RerankConfig(provider="gitee", url="", api_key="", model=""),
    )
    assert result is None


def test_rerank_chunks_rejects_unknown_provider():
    with pytest.raises(RuntimeError, match="Unsupported reranker provider"):
        rerank_chunks(
            "battery",
            [(1, 0.9, {"content": "text"})],
            RerankConfig(provider="other", url="http://x", api_key="k", model="model"),
        )


def test_gitee_reranker_extracts_indexed_scores():
    data = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]
    }
    assert _extract_scores(data, expected_count=2) == [0.1, 0.9]


def test_gitee_reranker_posts_with_unified_headers():
    resp = MagicMock()
    resp.ok = True
    resp.json.return_value = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]
    }
    with patch("zotero_cli.core.providers.gitee.requests.post", return_value=resp) as mock_post:
        provider = GiteeRerankerProvider(api_key="k", url="https://ai.gitee.com/v1/rerank")
        scores = provider.score("q", ["a", "b"])

    assert scores == [0.1, 0.9]
    args, kwargs = mock_post.call_args
    assert args[0] == "https://ai.gitee.com/v1/rerank"
    assert kwargs["headers"]["Authorization"] == "Bearer k"
    assert kwargs["headers"]["X-Failover-Enabled"] == "true"
    assert kwargs["json"]["query"] == "q"
    assert kwargs["json"]["documents"] == ["a", "b"]
