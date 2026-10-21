"""RAG 流水线的 embedding 与 rerank provider 实现。"""

from zotero_cli.workspace.rag.embeddings import EmbeddingProvider
from zotero_cli.workspace.rag.providers.gitee import GiteeEmbeddingProvider, GiteeRerankerProvider

__all__ = ["EmbeddingProvider", "GiteeEmbeddingProvider", "GiteeRerankerProvider"]
