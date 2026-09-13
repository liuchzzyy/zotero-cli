# Zotero workflow tools for macOS

The maintained workflow entrypoints are native zsh scripts:

- `tools/run-rag-workspace.zsh`: inventory, incremental indexing, and embedding backfill.
- `tools/run-rag-evidence-search.zsh`: query an existing RAG workspace.
- `tools/zotero-workflow-common.zsh`: shared repository discovery, safe log paths, and run logging.
- `tools/workspace-inventory.py`: deterministic inventory helper used by the workspace wrapper.

Run them from the repository root on the current MacBook:

```bash
cd /Users/liuchzzyy/python-code/zotero-cli
```

All generated run artifacts live under `log/`. Each kept run contains:

- `run.log`: human-readable summary and child output.
- `progress.jsonl`: structured start/finish events.
- `inventory.json`: workspace/PDF/index/vector counts for workspace runs.
- `logs/*.log`: output from inventory, indexing, embedding, or query child commands.

Successful runs remove their temporary log directory unless `--keep-log` or `--keep-inventory` is supplied. Failed runs remain for diagnosis.

## Workspace RAG incremental update

Persistent data that must not be removed by routine log cleanup:

- `.workspace/<name>/`: workspace and SQLite RAG index.
- `.workspace/_qdrant/`: local Qdrant vectors.
- `.zot/state/mineru/`: shared canonical MinerU packages (`document.md`, `content_list.json`, manifest, and raw assets).

Inspect the full interface:

```bash
tools/run-rag-workspace.zsh --help
```

For the current `00_收件箱` workspace:

```bash
tools/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections 00_收件箱 \
  --dry-run \
  --keep-log
```

Run the actual incremental update after reviewing the dry-run inventory:

```bash
tools/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections 00_收件箱 \
  --keep-log
```

Only backfill missing embeddings:

```bash
tools/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --embed-only \
  --keep-log
```

Build only the FTS5 index and defer embeddings:

```bash
tools/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections 00_收件箱 \
  --no-embed \
  --keep-log
```

Use `--force-rebuild` only when the existing index must be discarded and rebuilt. Normal operation is incremental.

## Evidence search

```bash
tools/run-rag-evidence-search.zsh \
  --workspace 00_收件箱 \
  --question 'anionic redox mechanism' \
  --mode auto \
  --top-k 8 \
  --rerank-top-n 50 \
  --json \
  --keep-log
```

If the online reranker is unavailable, use `--no-rerank`. Restrict results with `--pdf-kind main` or `--pdf-kind supplementary` when needed.

## Validation

```bash
zsh -n tools/zotero-workflow-common.zsh
zsh -n tools/run-rag-workspace.zsh
zsh -n tools/run-rag-evidence-search.zsh
uv run python tools/workspace-inventory.py --help
uv run pytest -q tests/integration/test_tool_scripts.py
```
