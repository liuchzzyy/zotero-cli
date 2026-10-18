"""Domain logic behind the CLI.

- zotero: local SQLite reads, Web API writes, attachments, metadata, idempotency
- notes: AI note generation pipeline (client, pipeline, figures, rendering, text)
- mineru: MinerU cloud parsing client and content-addressed parse cache
- rag: chunking, embeddings, FTS5 term index, fusion, rerank, and vector store
- workspace_store / platform_paths / version_check: shared support utilities
"""
