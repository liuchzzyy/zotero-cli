"""Reciprocal rank fusion for hybrid RAG retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence


def reciprocal_rank_fusion(*rankings: list[tuple[int, float, dict]], k: int = 60) -> list[tuple[int, float, dict]]:
    """Fuse rankings with equal weights."""
    return weighted_reciprocal_rank_fusion(rankings, weights=None, k=k)


def weighted_reciprocal_rank_fusion(
    rankings: Sequence[list[tuple[int, float, dict]]],
    *,
    weights: Sequence[float] | None = None,
    k: int = 60,
) -> list[tuple[int, float, dict]]:
    """Fuse rankings into one descending score list with weighted reciprocal rank fusion.

    `k` must be a positive integer and `weights` (when given) must have exactly
    one finite non-negative entry per ranking; anything else is a caller error
    rather than silently dropping rankings.
    """
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError(f"weights must have one entry per ranking ({len(rankings)}), got {len(weights)}")
    for weight in weights:
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight):
            raise ValueError("weights must be finite numbers")
        if weight < 0:
            raise ValueError("weights must be non-negative")
    scores: dict[int, float] = {}
    chunk_map: dict[int, dict] = {}
    for ranking, weight in zip(rankings, weights):
        for rank, (chunk_id, _score, chunk) in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight * (1.0 / (k + rank + 1))
            chunk_map[chunk_id] = chunk
    results = [(cid, score, chunk_map[cid]) for cid, score in scores.items()]
    results.sort(key=lambda x: x[1], reverse=True)
    return results
