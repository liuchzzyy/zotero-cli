"""使用配置的 rerank provider 对检索到的 chunk 重排。"""

from __future__ import annotations

import sys
from collections.abc import Callable

from zotero_cli.config import RerankConfig
from zotero_cli.workspace.rag.providers.gitee import GiteeRerankerProvider

RankedChunk = tuple[int, float, dict]


class UnsupportedRerankerProviderError(ValueError):
    """当配置的 reranker provider 没有实现时抛出。

    CLI 调用方应将其转换为带类型的 configuration_error envelope。
    """


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
    provider_name = config.provider.strip().lower()
    if provider_name != "gitee":
        raise UnsupportedRerankerProviderError(
            f"Unsupported reranker provider: {config.provider!r}. Supported providers: 'gitee'."
        )

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

    if len(scores) != len(selected):
        sys.stderr.write(
            "\r" + (" " * 60) + "\r"
            f"  [WARN] Reranker provider '{config.provider}' returned {len(scores)} scores for "
            f"{len(selected)} candidates. Returning retrieval ranking.\n"
        )
        return None

    reranked = [(cid, score, chunk) for (cid, _old_score, chunk), score in zip(selected, scores)]
    reranked.sort(key=lambda item: item[1], reverse=True)
    return reranked + candidates[top_n:]
