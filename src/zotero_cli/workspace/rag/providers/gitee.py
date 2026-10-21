"""Gitee AI embedding and rerank providers."""

from __future__ import annotations

import math
from collections.abc import Callable
from threading import Lock
from typing import Any

import requests

from zotero_cli.workspace.rag.embeddings import EmbeddingProvider

FAILOVER_HEADER = "X-Failover-Enabled"
_VALID_INPUT_TYPES = {"document", "query"}
# Some OpenAI-compatible deployments reject unknown request fields with 400.
_INPUT_TYPE_REJECTED_STATUS = {400, 422}

# Process-wide negative cache: once a (base_url, model) pair rejects the advisory
# input_type field, later queries skip it instead of paying a double request on
# every call. Keyed by the endpoint/model because rejection is a server property.
_rejected_input_type_endpoints: set[tuple[str, str]] = set()
_rejected_input_type_lock = Lock()


def _input_type_supported(base_url: str, model: str) -> bool:
    with _rejected_input_type_lock:
        return (base_url, model) not in _rejected_input_type_endpoints


def _mark_input_type_unsupported(base_url: str, model: str) -> None:
    with _rejected_input_type_lock:
        _rejected_input_type_endpoints.add((base_url, model))


def _reset_input_type_cache() -> None:
    """Clear the process-wide input_type rejection cache (used by tests)."""
    with _rejected_input_type_lock:
        _rejected_input_type_endpoints.clear()


def _auth_headers(api_key: str) -> dict[str, str]:
    """Unified request headers for every Gitee AI call."""
    return {"Authorization": f"Bearer {api_key}", FAILOVER_HEADER: "true"}


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_embedding_vector(value: Any, *, position: int) -> list[float]:
    if not isinstance(value, list) or not value:
        raise RuntimeError(f"Gitee embedding at index {position} must be a non-empty list of numbers")
    vector: list[float] = []
    for item in value:
        if not _is_finite_number(item):
            raise RuntimeError(f"Gitee embedding at index {position} has a non-finite or non-numeric value")
        vector.append(float(item))
    return vector


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
        if input_type not in _VALID_INPUT_TYPES:
            raise ValueError(f"input_type must be one of {sorted(_VALID_INPUT_TYPES)}, got {input_type!r}")
        if not texts:
            return []
        all_embeddings: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, self.batch_size):
            batch = texts[i : i + self.batch_size]
            all_embeddings.extend(self._embed_batch(batch, input_type=input_type))
            if progress_callback:
                progress_callback(min(i + self.batch_size, total), total)
        return all_embeddings

    def _embed_batch(self, batch: list[str], *, input_type: str = "document") -> list[list[float]]:
        endpoint = (self.base_url, self.model)
        payload: dict[str, Any] = {"model": self.model, "input": batch}
        send_input_type = input_type != "document" and _input_type_supported(*endpoint)
        if send_input_type:
            # Advisory hint for asymmetric models; sent only for non-default
            # input types so document embedding requests stay unchanged.
            payload["input_type"] = input_type
        response = requests.post(
            f"{self.base_url}/embeddings",
            headers=_auth_headers(self.api_key),
            json=payload,
            timeout=self.timeout,
        )
        if send_input_type and not response.ok and response.status_code in _INPUT_TYPE_REJECTED_STATUS:
            # The deployment does not understand the advisory field; remember
            # that for this endpoint and retry the same batch without it instead
            # of failing the whole embedding run (and without paying a second
            # request on every subsequent call).
            _mark_input_type_unsupported(*endpoint)
            retry_payload = {key: value for key, value in payload.items() if key != "input_type"}
            response = requests.post(
                f"{self.base_url}/embeddings",
                headers=_auth_headers(self.api_key),
                json=retry_payload,
                timeout=self.timeout,
            )
        if not response.ok:
            raise RuntimeError(f"Gitee embedding request failed: HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected Gitee embedding response shape")
        embeddings_data = data.get("data") or []
        if not isinstance(embeddings_data, list) or len(embeddings_data) != len(batch):
            raise RuntimeError(f"Gitee embedding response must contain {len(batch)} embeddings")
        if not all(isinstance(item, dict) for item in embeddings_data):
            raise RuntimeError("Unexpected Gitee embedding response shape")
        if any("index" in item for item in embeddings_data):
            indices = [item.get("index") for item in embeddings_data]
            if any(type(index) is not int for index in indices) or set(indices) != set(range(len(batch))):
                raise RuntimeError("Gitee embedding response has invalid, duplicate, or missing index values")
            embeddings_data = sorted(embeddings_data, key=lambda item: item["index"])
        vectors: list[list[float]] = []
        dimension: int | None = None
        for position, item in enumerate(embeddings_data):
            vector = _validate_embedding_vector(item.get("embedding"), position=position)
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise RuntimeError("Gitee embedding response has inconsistent vector dimensions")
            vectors.append(vector)
        return vectors


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
                raise RuntimeError(f"Gitee reranker request failed: HTTP {response.status_code}: {response.text[:500]}")
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
    if any(not math.isfinite(score) for score in scores):
        raise RuntimeError("Gitee reranker response contains non-finite scores")
    return scores


def _find_score_values(data: Any, *, expected_count: int) -> list[float | int] | None:
    if isinstance(data, list):
        if all(_is_finite_number(item) for item in data):
            return data
        if any(isinstance(item, dict) and "index" in item for item in data):
            indexed = _scores_from_indexed_results(data, expected_count=expected_count)
            if indexed is None:
                raise RuntimeError("Gitee reranker response has invalid, duplicate, or missing index values")
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
        if type(index) is not int or score is None:
            return None
        if index < 0 or index >= expected_count or scores[index] is not None:
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
        if score is None:
            return None
        scores.append(score)
    return scores


def _score_from_result_item(item: dict[str, Any]) -> float | int | None:
    for key in ("relevance_score", "score", "similarity", "similarity_score"):
        value = item.get(key)
        if _is_finite_number(value):
            return value
    return None
