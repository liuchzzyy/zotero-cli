# Zotero workflow tools for macOS

The maintained workflow entrypoints are native zsh scripts:

- `tools/paperrag/run-rag-workspace.zsh`: inventory, incremental indexing, and embedding backfill.
- `tools/paperrag/run-rag-evidence-search.zsh`: query an existing RAG workspace.
- `tools/paperrag/zotero-workflow-common.zsh`: shared repository discovery, safe log paths, and run logging.
- `tools/paperrag/workspace_inventory.py`: deterministic inventory helper used by the workspace wrapper.

Run them from the repository root on the current MacBook:

```bash
cd /Users/liuchzzyy/CodeSpace/zotero-cli
```

All generated run artifacts live under `log/`, in a unique run directory named
`<workflow>-<timestamp>-<pid>-<random>` so two runs started in the same second
cannot collide. Each kept run contains:

- `run.log`: human-readable summary and child output.
- `progress.jsonl`: structured start/finish events.
- `inventory.json`: workspace/PDF/index/vector counts for workspace runs.
- `logs/*.log`: output from inventory, indexing, embedding, or query child commands.

Successful runs remove their own run directory unless `--keep-log` or
`--keep-inventory` is supplied. A run directory that already existed before the
run is never deleted on success (it is kept and reported). Failed runs remain for
diagnosis.

## Workspace RAG incremental update

Persistent data that must not be removed by routine log cleanup:

- `.workspace/<name>/`: workspace and SQLite RAG index.
- `.workspace/_qdrant/`: local Qdrant vectors.
- `.zot/state/mineru/`: shared canonical MinerU packages (`manifest.json`, `document.md`, `content_list.json`, and `raw/` assets).

Inspect the full interface:

```bash
tools/paperrag/run-rag-workspace.zsh --help
```

For the existing `00_收件箱` workspace, source items from the renamed Zotero collection `00 | 收件箱` (collection key `AG7NQ5UW`). The workspace name is local and does not need to match the collection name:

```bash
tools/paperrag/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections '00 | 收件箱' \
  --dry-run \
  --keep-log
```

Run the actual incremental update after reviewing the dry-run inventory:

```bash
tools/paperrag/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections '00 | 收件箱' \
  --keep-log
```

Only backfill missing embeddings:

```bash
tools/paperrag/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --embed-only \
  --keep-log
```

Build only the FTS5 index and defer embeddings:

```bash
tools/paperrag/run-rag-workspace.zsh \
  --workspace 00_收件箱 \
  --collections '00 | 收件箱' \
  --no-embed \
  --keep-log
```

Use `--force-rebuild` only when the existing index must be discarded and rebuilt. Normal operation is incremental.

## Evidence search

```bash
tools/paperrag/run-rag-evidence-search.zsh \
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
zsh -n tools/paperrag/zotero-workflow-common.zsh
zsh -n tools/paperrag/run-rag-workspace.zsh
zsh -n tools/paperrag/run-rag-evidence-search.zsh
uv run python tools/paperrag/workspace_inventory.py --help
uv run pytest -q tests/integration/test_tool_scripts.py tests/integration/test_inventory_edges.py
uv run pytest -q -m tools
uv run pytest -q -rf
```

The full repository suite is expected fully green (0 failed, 0 skipped); all
`tools/**` tests carry the `tools` marker and run in the default suite.
