"""Reciprocal rank fusion for hybrid RAG retrieval."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(*rankings: list[tuple[int, float, dict]], k: int = 60) -> list[tuple[int, float, dict]]:
    return weighted_reciprocal_rank_fusion(rankings, weights=None, k=k)


def weighted_reciprocal_rank_fusion(
    rankings: Sequence[list[tuple[int, float, dict]]],
    *,
    weights: Sequence[float] | None = None,
    k: int = 60,
) -> list[tuple[int, float, dict]]:
    scores: dict[int, float] = {}
    chunk_map: dict[int, dict] = {}
    if weights is None:
        weights = [1.0] * len(rankings)
    for ranking, weight in zip(rankings, weights):
        for rank, (chunk_id, _score, chunk) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * (1.0 / (k + rank + 1))
            chunk_map[chunk_id] = chunk
    results = [(cid, score, chunk_map[cid]) for cid, score in scores.items()]
    results.sort(key=lambda x: x[1], reverse=True)
    return results
