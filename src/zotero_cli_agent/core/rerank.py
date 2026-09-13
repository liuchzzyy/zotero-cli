from __future__ import annotations

import sys
from collections.abc import Callable

from zotero_cli_agent.config import RerankConfig
from zotero_cli_agent.core.providers.gitee import GiteeRerankerProvider

RankedChunk = tuple[int, float, dict]


def rerank_chunks(
    query: str,
    candidates: list[RankedChunk],
    config: RerankConfig,
    *,
    top_n: int = 50,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[RankedChunk] | None:
    if not candidates or not config.is_configured or top_n <= 0:
        return None

    selected = candidates[:top_n]
    if config.provider != "gitee":
        raise RuntimeError(f"Unsupported reranker provider: {config.provider}")

    provider = GiteeRerankerProvider(
        api_key=config.api_key,
        url=config.url,
        model=config.model,
        batch_size=config.batch_size,
    )
    try:
        scores = provider.score(query, [str(chunk["content"]) for _cid, _score, chunk in selected], progress_callback)
    except Exception as e:
        sys.stderr.write(
            "\r" + (" " * 60) + "\r"
            f"  [WARN] Reranker provider '{config.provider}' failed: "
            f"{type(e).__name__}: {e}. Returning retrieval ranking.\n"
        )
        return None

    reranked = [(cid, score, chunk) for (cid, _old_score, chunk), score in zip(selected, scores)]
    reranked.sort(key=lambda item: item[1], reverse=True)
    return reranked + candidates[top_n:]
