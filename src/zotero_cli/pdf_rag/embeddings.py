"""RAG 的 embedding provider 与配置化 embedding 入口。"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from zotero_cli.config import EmbeddingConfig


class EmbeddingProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def embed(
        self,
        texts: list[str],
        progress_callback: Callable[[int, int], None] | None = None,
        *,
        input_type: str = "document",
    ) -> list[list[float]]: ...


def embed_texts(
    texts: list[str],
    config: EmbeddingConfig,
    progress_callback: Callable[[int, int], None] | None = None,
    *,
    input_type: str = "document",
) -> list[list[float]] | None:
    if not config.is_configured:
        return None
    provider_name = config.provider.strip().lower()
    if provider_name != "gitee":
        sys.stderr.write(
            f"\r{' ' * 60}\r  [WARN] Unsupported embedding provider '{config.provider}'. Falling back to BM25-only.\n"
        )
        return None
    # 延迟导入，使 provider 实现可以依赖本模块提供的 EmbeddingProvider 约定，
    # 而不会形成循环导入。
    from zotero_cli.pdf_rag.providers.gitee import GiteeEmbeddingProvider

    provider = GiteeEmbeddingProvider(
        api_key=config.api_key,
        url=config.url,
        model=config.model,
        batch_size=config.batch_size,
        timeout=config.timeout_seconds,
    )
    try:
        return provider.embed(texts, progress_callback, input_type=input_type)
    except Exception as error:
        sys.stderr.write(
            f"\r{' ' * 60}\r"
            f"  [WARN] Embedding provider '{config.provider}' failed: "
            f"{type(error).__name__}: {error}. Falling back to BM25-only.\n"
        )
        return None
