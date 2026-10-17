# AGENTS.md

This file provides guidance to coding agents working with code in this repository.

## Project

`zotero-cli` (binary: `zot`) is a Zotero CLI built for AI agent use. It combines **direct local SQLite reads** with **Zotero Web API writes**. The `zot` CLI is the single entry point.

The CLI follows an agent-native contract enforced by the Click tree, `zot schema`,
`formatter.py`, `exit_codes.py`, and the agent-interface tests:

- stable JSON envelope
- typed exit codes
- `zot schema` introspection
- `--dry-run`
- `--idempotency-key`
- NDJSON streaming

## Common Commands

Uses `uv` as the package manager. `uv.lock` is authoritative.

```bash
# Install dev environment
uv sync --dev

# Lint / type-check / test
uv run ruff check src tests
uv run python -m mypy src/zotero_cli
uv run pytest -q

# Run a single test / file / node
uv run pytest tests/unit/test_reader.py -v
uv run pytest tests/unit/test_reader.py::test_name -v
uv run pytest -k "search and not rag" -v

# Run the CLI from source
uv run zot --help
uv run zot search "foo"
uv run zot schema

# Build artifacts
uv build
```

The package supports Python 3.10–3.13. Lint/type-check/tests run locally via `uv`; no CI workflow is checked in.

## Architecture

### Read/write split

This is the central design constraint.

- **Reads** go through `core/reader.py` and open `zotero.sqlite` directly from the local Zotero data directory.
- **Writes** go through `core/writer.py` and use `pyzotero` against the Zotero Web API.

Never write to `zotero.sqlite` directly. That would bypass Zotero's sync model and can corrupt sync state.

`reader.py` uses two read paths. When `<db>.sqlite-wal` exists and is non-empty it copies the DB plus WAL to a temp file and replays it, because `immutable=1` on the live file returns a stale snapshot while Zotero Desktop is running (recently synced attachments look missing and PDF paths look broken). Keep that branch when touching connection setup; a plain `immutable=1` reconnect is a regression.

### CLI shape

- `src/zotero_cli/cli.py` is the Click root group.
- `src/zotero_cli/commands/*.py` are self-contained commands or command groups.
- `src/zotero_cli/formatter.py` implements the dual output contract:
  - Rich / human-readable output for TTY use
  - JSON envelope when piped or when `--json` is enabled
- `src/zotero_cli/exit_codes.py` defines typed exit behavior. Errors should go through `emit_error(...)`.
- `zot schema` reflects the Click tree for agent consumption.

When adding a command, register it in `cli.py` and place it in the correct safety tier set. Otherwise help output and schema reporting drift.

### Core subsystems

- `src/zotero_cli/core/reader.py`: SQLite read layer (WAL-aware, see above).
- `src/zotero_cli/core/writer.py`: Web API write layer (notes, tags, item fields, Extra short-note merge).
- `src/zotero_cli/core/mineru.py`: MinerU-only cloud parsing and the canonical shared parse package cache under `.zot/state/mineru/`.
- `src/zotero_cli/core/ai_client.py`, `note_analysis.py`, `note_renderer.py`, `note_templates.py`, `note_figures.py`, `note_text.py`: the `ai_analyze` pipeline — OpenAI-compatible chat with a multi-model fallback chain, item classification / note generation / short-note keywords, multimodal figure input, inline-styled HTML rendering, and CJK/Latin plus super/subscript normalisation. Prompt templates live under `tools/ainote/templates/`.
- `src/zotero_cli/core/rag.py`, `rag_index.py`, `rerank.py`: MinerU structured-JSON chunking, SQLite FTS5 (BM25), reciprocal rank fusion, Gitee embedding/rerank helpers.
- `src/zotero_cli/core/semantic_search/vector_store.py`: local Qdrant vector store.
- `src/zotero_cli/core/providers/gitee.py`: Gitee AI embedding / rerank provider.
- `src/zotero_cli/core/workspace.py`: repo-local workspaces under `.workspace/`.
- `src/zotero_cli/core/idempotency.py`: retry-safe mutation support.
- `src/zotero_cli/core/semantic_scholar.py`: preprint-to-published lookups for `update-status`.
- `src/zotero_cli/core/version_check.py`: version notice logic.

### ai_analyze specifics

- Figures: `note_figures.py` rewrites `![](images/<sha>.jpg)` in MinerU Markdown to `【图 N】` markers, filters assets by size, caps the request at `_MAX_FIGURES` (12), and returns data URLs for the chat request. `ai_client.py` attaches them as `image_url` parts and falls back to the next model when a provider rejects image content.
- Model chain: `model` followed by `fallback_models` (TOML array or comma-separated string). Local `base_url` values (`127.0.0.1` / `localhost`) disable HTTP proxy env handling.
- Text: `note_text.py` flattens super/subscripts and inserts a half-width space between CJK and Latin before HTML escaping in `note_renderer.py`. The note title is escaped but not re-spaced, because `NOTE_TITLE_PREFIX` matching depends on it.
- Dedupe: `note_analysis._drop_duplicate_parts` drops a repeated copy of the same document (re-imported items often carry the PDF twice) while preserving main-text-before-appendix order.
- Templates: `note_templates.template_path` probes `tools/ainote/templates/`, then `tools/ainote/`, then legacy `tools/templates/`.

### Tools and bundled skill

- `tools/paperrag/`: maintained zsh entrypoints `run-rag-workspace.zsh` (inventory, incremental index, embedding backfill) and `run-rag-evidence-search.zsh`, plus `zotero-workflow-common.zsh` and `workspace-inventory.py`. Usage is documented in `tools/paperrag/Instructions.md`; run artifacts go to `log/`.
- `tools/paper2zotero/`: `zotero_inbox.py` (PaperCrossref inbox import) and `backfill_titles.py` (library-wide translated-title backfill), each with tests beside it.
- `tools/ainote/templates/`: `ai_analyze` prompt templates (`classify-item`, `research-article`, `review-article`, `book`, `short-note`).
- `tests/integration/test_tool_scripts.py` resolves the zsh scripts from `tools/paperrag/` when present and falls back to `tools/`.

Do not reintroduce the old flat `tools/*.zsh` layout; the scripts now live in `tools/paperrag/`.

### Agent Contract and Skill

- `zot schema` is the authoritative machine-readable CLI surface.
- `tests/cli/test_contract.py`, `test_contract_mutations.py`, and `test_contract_streaming.py` are the regression guardrails for envelope shape, exit codes, dry-run behavior, streaming, and safety tiers.
- `skill/zotero-cli/` is the bundled agent skill and should stay aligned with real CLI behavior.
- No `docs/` tree or MkDocs config is currently checked in. Do not treat missing docs paths as source of truth unless docs are reintroduced in a future change.

If the CLI surface changes, update schema-visible behavior, tests, and the bundled skill together. If docs are later restored, keep them in sync too.

## Config and Profiles

Config lives at:

- `.zot/config.toml`

The code supports profile-based configuration and a default profile selector.

Resolution order is:

1. CLI flag
2. environment variable
3. active profile
4. defaults

Relevant env vars include:

- `ZOT_DATA_DIR`
- `ZOT_LIBRARY_ID`
- `ZOT_API_KEY`
- `ZOT_MINERU_TOKEN`
- `ZOT_MINERU_MODEL_VERSION`
- `ZOT_PROFILE`

Relevant sections:

- `[ai_notes]`: `api_key`, `base_url`, `model`, `fallback_models`, `reasoning_effort`, `api_mode`, `chat_token_param`, `temperature`, `max_tokens`, `max_extracted_chars`.
- `[pdf]`: `mineru_token`, `mineru_model_version`.
- `[embedding]`, `[rerank]`, `[semantic_search]`: Gitee providers and hybrid retrieval weights.

Zotero data dir auto-detects when not configured, but this checkout now treats repo-local `.zot/config.toml` as the primary persistent source of truth.

`.zot/config.example.toml` is a template: it must stay credential-free (no real `api_key` or `mineru_token` values) and free of machine-specific absolute paths. Use `/Users/your-name/Zotero` and `.../Profiles/<profile>/prefs.js` as the placeholders.

Current local config is expected at:

- `/Users/liuchzzyy/CodeSpace/zotero-cli/.zot/config.toml`
- active default profile `macbook`
- real local database at `/Users/liuchzzyy/Zotero/zotero.sqlite`
- working Web API credentials for writes

## Persistent Local State

Never delete these when cleaning up logs:

- `.workspace/<name>/`: workspace manifest and SQLite RAG index
- `.workspace/_qdrant/`: local Qdrant vectors
- `.zot/state/mineru/`: shared content-addressed MinerU packages
- `log/`: per-run artifacts (`run.log`, `progress.jsonl`, `inventory.json`), safe to prune

## Conventions

- Type hints are required. `mypy` is enforced on `src/zotero_cli`.
- Ruff target is Python 3.10 with line length 120.
- Keep changes surgical. Do not refactor unrelated areas.
- Preserve the current license metadata (`MIT`) unless explicitly asked to change it.
- Do not run `git commit` or `git push` without explicit user instruction.

## Designing New Commands

When adding or refactoring a CLI command, preserve the agent-native surface:

- dual output contract
- typed exit codes
- dry-run conventions
- idempotency behavior
- schema visibility

Every top-level command must be placed in exactly one safety bucket in `src/zotero_cli/cli.py`:

- read
- write
- destructive

This affects both `zot --help` grouping and `zot schema` output consumed by agents.

## Validation

Before closing substantial changes, run the smallest relevant checks first, then broader ones as needed:

1. Targeted `pytest` for touched behavior
2. `uv run python -m mypy src/zotero_cli`
3. `uv run ruff check src tests`
4. `uv run pytest -q`

For this repository on this machine, the environment has already been initialized successfully with:

```bash
uv sync --dev
uv run zot --help
uv run pytest -q
```

and the full test suite passed during setup.
