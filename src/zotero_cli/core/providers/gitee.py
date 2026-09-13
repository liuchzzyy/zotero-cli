from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests  # type: ignore[import-untyped]

from zotero_cli.core.embedding_provider import EmbeddingProvider

FAILOVER_HEADER = "X-Failover-Enabled"


def _auth_headers(api_key: str) -> dict[str, str]:
    """Unified request headers for every Gitee AI call."""
    return {"Authorization": f"Bearer {api_key}", FAILOVER_HEADER: "true"}


class GiteeEmbeddingProvider(EmbeddingProvider):
    """Embed text via Gitee AI's OpenAI-compatible /embeddings endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = "https://ai.gitee.com/v1",
        model: str = "bge-m3",
        batch_size: int = 50,
        timeout: float = 120.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = url.rstrip("/")
        self.model = model
        self.batch_size = max(int(batch_size), 1)
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "gitee"

    def embed(
        self,
        texts: list[str],
        progress_callback: Callable[[int, int], None] | None = None,
        *,
        input_type: str = "document",
    ) -> list[list[float]]:
        _ = input_type
        if not texts:
            return []
        all_embeddings: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            all_embeddings.extend(self._embed_batch(batch))
            if progress_callback:
                progress_callback(min(i + self.batch_size, total), total)
        return all_embeddings

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        response = requests.post(
            f"{self.base_url}/embeddings",
            headers=_auth_headers(self.api_key),
            json={"model": self.model, "input": batch},
            timeout=self.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Gitee embedding request failed: HTTP {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        embeddings_data = data.get("data") or []
        return [item["embedding"] for item in embeddings_data]


class GiteeRerankerProvider:
    """Rerank documents against a query via Gitee AI's /rerank endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        url: str = "https://ai.gitee.com/v1/rerank",
        model: str = "bge-reranker-v2-m3",
        batch_size: int = 16,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.url = url
        self.model = model
        self.batch_size = max(int(batch_size), 1)
        self.timeout = timeout

    def score(
        self,
        query: str,
        documents: list[str],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[float]:
        scores: list[float] = []
        total = len(documents)
        for start in range(0, total, self.batch_size):
            batch = documents[start : start + self.batch_size]
            response = requests.post(
                self.url,
                headers=_auth_headers(self.api_key),
                json={"query": query, "documents": batch, "model": self.model},
                timeout=self.timeout,
            )
            if not response.ok:
                raise RuntimeError(
                    f"Gitee reranker request failed: HTTP {response.status_code}: {response.text[:500]}"
                )
            scores.extend(_extract_scores(response.json(), expected_count=len(batch)))
            if progress_callback:
                progress_callback(min(start + self.batch_size, total), total)
        return scores


def _extract_scores(data: Any, *, expected_count: int) -> list[float]:
    values = _find_score_values(data, expected_count=expected_count)
    if values is None:
        raise RuntimeError(f"Unexpected Gitee reranker response shape: {type(data).__name__}")
    scores = [float(value) for value in values]
    if len(scores) != expected_count:
        raise RuntimeError(f"Gitee reranker returned {len(scores)} scores for {expected_count} documents")
    return scores


def _find_score_values(data: Any, *, expected_count: int) -> list[float | int] | None:
    if isinstance(data, list):
        if all(isinstance(item, int | float) for item in data):
            return data
        indexed = _scores_from_indexed_results(data, expected_count=expected_count)
        if indexed is not None:
            return indexed
        sequential = _scores_from_sequential_results(data)
        if sequential is not None:
            return sequential
        return None

    if isinstance(data, dict):
        for key in ("scores", "score", "data", "result", "results", "outputs"):
            value = data.get(key)
            found = _find_score_values(value, expected_count=expected_count)
            if found is not None:
                return found
    return None


def _scores_from_indexed_results(data: list[Any], *, expected_count: int) -> list[float | int] | None:
    scores: list[float | int | None] = [None] * expected_count
    saw_indexed = False
    for item in data:
        if not isinstance(item, dict):
            return None
        index = item.get("index")
        score = _score_from_result_item(item)
        if not isinstance(index, int) or not isinstance(score, int | float):
            return None
        if index < 0 or index >= expected_count:
            return None
        saw_indexed = True
        scores[index] = score
    if not saw_indexed or any(score is None for score in scores):
        return None
    return [score for score in scores if score is not None]


def _scores_from_sequential_results(data: list[Any]) -> list[float | int] | None:
    scores: list[float | int] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        score = _score_from_result_item(item)
        if not isinstance(score, int | float):
            return None
        scores.append(score)
    return scores


def _score_from_result_item(item: dict[str, Any]) -> float | int | None:
    for key in ("relevance_score", "score", "similarity", "similarity_score"):
        value = item.get(key)
        if isinstance(value, int | float):
            return value
    return None
