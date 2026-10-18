# AGENTS.md

This file provides guidance to coding agents working with code in this repository.

## Project

`zotero-cli` (binary: `zot`) is a Zotero CLI built for AI agent use. It combines **direct local SQLite reads** with **Zotero Web API writes**. The `zot` CLI is the single entry point; everything under `tools/` is a separate batch helper, not a subcommand.

The CLI follows an agent-native contract enforced by the Click tree, `zot schema`,
the output layer (`src/zotero_cli/output/`),
typed exit codes (`src/zotero_cli/output/exit_codes.py`), and the agent-interface tests:

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
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'
uv run pytest -q -rf
uv run pytest --cov --cov-report=term-missing

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

The package supports Python 3.10–3.13. Lint/type-check/tests run locally via `uv`; no CI workflow is checked in. `testpaths` covers `tests` and `tools`, so the `tools/**/test_*.py` tests run with the rest. The suite is expected fully green: 0 failed, 0 skipped.

## Directory map

```
src/zotero_cli/
├── cli/         click root, global flags, safety tiers, command registration
├── commands/    one package per feature area: items, notes, attachments,
│                collections, workspace, system
├── core/        domain logic: zotero, notes, mineru, rag, plus
│                workspace_store.py, platform_paths.py, version_check.py
├── output/      envelope, renderers, streaming, exit codes
├── config/      paths, models, loaders, zotero resolution; public API in
│                __init__.py
└── models.py    shared dataclasses
tests/           unit/, cli/, integration/, fixtures/ + shared harness
tools/           non-CLI batch helpers: ainote, paper2zotero, paperrag
skill/zotero-cli/  bundled agent skill
```

## Architecture

### Read/write split

This is the central design constraint.

- **Reads** go through `src/zotero_cli/core/zotero/reader.py` and open `zotero.sqlite` directly from the local Zotero data directory. SQL-free citation formatting (BibTeX / CSL-JSON / RIS) lives in `core/zotero/citations.py` as `CitationExportMixin`, which `ZoteroReader` composes. Read scoping is part of the API: item queries exclude `deletedItems`, `get_notes` / `get_attachments` return empty when the parent item is trashed and filter trashed children, and `get_pdf_attachment` / `get_pdf_attachments` inherit that filter.
- **Writes** go through `src/zotero_cli/core/zotero/writer.py` and use `pyzotero` against the Zotero Web API. Web API error translation lives in `core/zotero/writer_errors.py` and the Extra short-note merge in `core/zotero/short_note_merge.py`.

Never write to `zotero.sqlite` directly. That would bypass Zotero's sync model and can corrupt sync state.

`reader.py` uses two read paths. When `<db>.sqlite-wal` exists and is non-empty it copies the DB plus WAL to a temp file and replays it, because `immutable=1` on the live file returns a stale snapshot while Zotero Desktop is running (recently synced attachments look missing and PDF paths look broken). Keep that branch when touching connection setup; a plain `immutable=1` reconnect is a regression.

### CLI shape

- `src/zotero_cli/cli/` is the Click package: `root.py` defines the root group (`main`, the `zot = "zotero_cli.cli:main"` console entry); `__main__.py` supports `python -m zotero_cli.cli`; `registry.py` registers every command in observable order; `global_flags.py` hoists `--json` / `--no-json` from anywhere in argv; `tiered_group.py` renders `zot --help` grouped by safety tier; `safety.py` is the single source of truth for read/write/destructive tiers and is consumed by both `tiered_group` and `zot schema`.
- `src/zotero_cli/commands/` holds one package per feature area: `items/` (add, cite, delete, duplicates, export, listing, open, read, recent, relate, search, stats, summarize, summarize_all, tag, trash, update, update_status), `notes/` (note, ai_analyze), `attachments/` (attach, pdf), `collections/` (`commands.py` plus the reorganize plan), `workspace/` (`commands.py`, indexing, embedding, query), and `system/` (config split into `commands.py` / `profiles.py` / `cache.py`, plus completions and schema). Command implementations live in each package's `commands.py`; larger areas split cohesive helpers into sibling modules.
- `src/zotero_cli/output/` implements the dual output contract: Rich / human-readable output for TTY use, JSON envelope when piped or when `--json` is enabled. Commands import envelope builders from `output/envelope.py`, renderers from `output/renderers.py`, streaming helpers from `output/streaming.py`, and typed exit behavior from `output/exit_codes.py`; `output/exit_codes.py` also defines `emit_error(...)`, which errors should go through.
- `zot schema` reflects the Click tree for agent consumption: commands, params, and each command's `safety_tier` (resolved by `cli/safety.py:safety_tier_for_path`).

When adding a command, place it under the matching `commands/<area>/` package, register it in `cli/registry.py` (preserve the observable order), and classify it in `cli/safety.py:SAFETY_TIERS`. Nested subcommands that mutate the library need their own tuple entry (e.g. `("collection", "delete")`, `("trash", "restore")`). Otherwise help output and schema reporting drift.

### Core subsystems

- `src/zotero_cli/core/zotero/`: `reader.py` SQLite read layer (WAL-aware and trash-scoped, see above); `citations.py` citation export mixin; `writer.py` Web API write layer (notes, tags, item fields); `writer_errors.py` typed write errors; `short_note_merge.py` Extra short-note merge; `attachments.py` attachment path resolver; `metadata.py` DOI→Zotero metadata via Crossref (`resolve_doi`); `publication_status.py` Semantic Scholar preprint-to-published lookups for `update-status`; `idempotency.py` retry-safe mutation cache/reservations.
- `src/zotero_cli/core/mineru/`: `__init__.py` exposes the package's public API (`MinerUClient`, `MinerUParseCache`, parse results/errors, helpers); `client.py` upload/poll/download flow; `cache.py` content-addressed parse packages under `.zot/state/mineru/`; `models.py` shared types/errors/constants; `utils.py` helpers.
- `src/zotero_cli/core/notes/`: the `ai_analyze` pipeline — `client.py` OpenAI-compatible chat with a multi-model fallback chain; `pipeline.py` per-item orchestration; `classification.py` item classification and shared JSON extraction; `documents.py` parsed-document payload helpers and duplicate-part dropping; `figures.py` multimodal figure input; `renderer.py` inline-styled HTML rendering; `templates.py` template lookup; `text.py` CJK/Latin plus super/subscript normalisation; `short_note.py` keyword short-note generation/validation; `constants.py` / `errors.py` shared definitions. Prompt templates live under `tools/ainote/templates/`.
- `src/zotero_cli/core/rag/`: `__init__.py` exposes the documented public API; `chunking.py` MinerU structured-JSON and plain-text chunking; `metadata.py` HTML cleaning, tokenisation, and PDF-kind inference; `index.py` SQLite FTS5 (BM25) term index; `fusion.py` reciprocal rank fusion; `embeddings.py` embedding providers; `rerank.py` rerank pipeline; `vector_store.py` local Qdrant store; `providers/gitee.py` Gitee AI embedding/rerank provider.
- `src/zotero_cli/core/workspace_store.py`: repo-local workspaces and manifests under `.workspace/`; `platform_paths.py`: cross-platform path helpers; `version_check.py`: PyPI update notice.
- `src/zotero_cli/config/`: documented import surface in `__init__.py`; implementations in `paths.py` (repo/state paths), `models.py` (typed config), `loaders.py` (TOML + env overrides), `zotero.py` (data dir / credential resolution).

### ai_analyze specifics

- Figures: `core/notes/figures.py` rewrites `![](images/<sha>.jpg)` in MinerU Markdown to `【图 N】` markers, filters assets by size, caps the request at `MAX_FIGURES` (12, `core/notes/constants.py`), and returns data URLs for the chat request. `core/notes/client.py` attaches them as `image_url` parts and falls back to the next model when a provider rejects image content.
- Model chain: `AiClient.get_model_chain()` in `core/notes/client.py` — `model` followed by `fallback_models` (TOML array or comma-separated string). Local `base_url` values (`127.0.0.1` / `localhost`) disable HTTP proxy env handling.
- Text: `core/notes/text.prepare()` flattens super/subscripts and inserts a half-width space between CJK and Latin (`pad_cjk_latin(plain_super_subscripts(text))`); `core/notes/renderer.py` applies it before HTML escaping. The note title is escaped but not re-spaced, because `NOTE_TITLE_PREFIX` matching depends on it.
- Dedupe: `core/notes/documents._drop_duplicate_parts` drops a repeated copy of the same document (re-imported items often carry the PDF twice) while preserving main-text-before-appendix order.
- Tags/constants: `ai/noted`, `ai/uncertain`, `ai/keywords`, `ai/no_keywords` and `NOTE_TITLE_PREFIX` live in `core/notes/constants.py`.
- Templates: `core/notes/templates.template_path` probes `tools/ainote/templates/` in a checkout and falls back to the packaged `zotero_cli/templates/` that hatchling force-includes into the wheel — an installed CLI has no `tools/`, so keep the packaged candidate when editing the lookup.

### Tools and bundled skill

- Tools under `tools/` are **not** `zot` subcommands: `zot schema` never lists them. See `tools/README.md` for the directory overview.
- `tools/paperrag/`: maintained zsh entrypoints `run-rag-workspace.zsh` (inventory, incremental index, embedding backfill) and `run-rag-evidence-search.zsh`, plus `zotero-workflow-common.zsh` and `workspace_inventory.py`. Usage is documented in `tools/paperrag/Instructions.md`; run artifacts go to `log/`.
- `tools/paper2zotero/`: `zotero_inbox.py` (PaperCrossref inbox import, writes the Chinese title into Extra and the Chinese abstract into `abstractNote`), `backfill_titles.py` (library-wide translated-title backfill), `translate_abstracts.py` (collection and item abstract translation backfill), `batch_translate_metadata.py` (per-collection Chinese title + abstract sync), and `zh_text.py` (flattening super/subscripts plus CJK/Latin spacing for those fields). Writes go through the Zotero Web API; see `tools/paper2zotero/README.md`.
- `tools/ainote/`: `batch_analyze.py` runs the `ai_analyze` pipeline over a collection; `templates/` holds the `ai_analyze` prompt templates (`classify-item`, `research-article`, `review-article`, `book`, `short-note`), force-included into the wheel by `pyproject.toml`.
- Tool tests are offline: `tests/integration/test_tool_scripts.py` handles the zsh entrypoints (mode + `zsh -n` + dry runs), and `tests/integration/test_inventory_edges.py` loads `workspace_inventory.py` by path.

Do not reintroduce the old flat `tools/*.zsh` layout; the scripts now live in `tools/paperrag/`.

### Agent Contract and Skill

- `zot schema` is the authoritative machine-readable CLI surface.
- `tests/cli/test_contract.py`, `test_contract_mutations.py`, and `test_contract_streaming.py` are the regression guardrails for envelope shape, exit codes, dry-run behavior, streaming, and safety tiers.
- `skill/zotero-cli/` is the bundled agent skill and should stay aligned with real CLI behavior.
- No `docs/` tree or MkDocs config is currently checked in. Do not treat missing docs paths as source of truth.

If the CLI surface changes, update schema-visible behavior, the contract tests, `AGENTS.md`, and the bundled skill together.

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
- `ZOT_FORMAT`
- `ZOT_PREFS_JS_PATH`
- `ZOT_CROSSREF_MAILTO`
- `ZOT_AI_API_KEY`
- `ZOT_AI_BASE_URL`
- `ZOT_AI_MODEL`
- `ZOT_AI_FALLBACK_MODELS`
- `ZOT_AI_REASONING_EFFORT`
- `ZOT_AI_TEMPERATURE`
- `ZOT_AI_MAX_TOKENS`
- `ZOT_AI_MAX_EXTRACTED_CHARS`

`load_ai_note_config` applies the `ZOT_AI_*` variables on top of the file (`env > file > defaults`, same shape as `load_pdf_config`). `ZOT_AI_FALLBACK_MODELS` is comma-separated. Pass `apply_env_overrides=False` in a test that needs the raw file values. Embedding/rerank providers read `ZOT_EMBEDDING_*` / `ZOT_RERANK_*` variables over their file sections.

Relevant sections:

- `[ai_notes]`: `api_key`, `base_url`, `model`, `fallback_models`, `reasoning_effort`, `api_mode`, `chat_token_param`, `temperature`, `max_tokens`, `max_extracted_chars`.
- `[pdf]`: `mineru_token`, `mineru_model_version`.
- `[embedding]`, `[rerank]`, `[semantic_search]`: Gitee providers and hybrid retrieval weights.

Zotero data dir auto-detects when not configured, but this checkout treats repo-local `.zot/config.toml` as the primary persistent source of truth.

`.zot/config.example.toml` is a template: it must stay credential-free (no real `api_key` or `mineru_token` values), free of machine-specific absolute paths, and use the neutral profile name `main` (`[profile.main]`, README shows the same). Use `/Users/your-name/Zotero` and `.../Profiles/<profile>/prefs.js` as the path placeholders.

Current local config is expected at:

- `/Users/liuchzzyy/CodeSpace/zotero-cli/.zot/config.toml`
- active default profile `macbook` — intentionally different from the template's `main`; this machine's live profile was not renamed
- real local database at `/Users/liuchzzyy/Zotero/zotero.sqlite`
- working Web API credentials for writes

## Persistent Local State

Never delete these when cleaning up logs:

- `.workspace/<name>/`: workspace manifest and SQLite RAG index
- `.workspace/_qdrant/`: local Qdrant vectors
- `.zot/state/mineru/`: shared content-addressed MinerU packages
- `log/`: per-run artifacts (`run.log`, `progress.jsonl`, `inventory.json`), safe to prune

## Conventions

- Type hints are required. `mypy` is enforced on `src/zotero_cli` and `tools` (test modules excluded).
- Ruff target is Python 3.10 with line length 120.
- Keep changes surgical. Do not refactor unrelated areas.
- Format only the files you touch (`uv run ruff format <paths>`); never run a repo-wide formatter over unrelated files.
- Preserve the current license metadata (`MIT`) unless explicitly asked to change it.
- Do not run `git commit` or `git push` without explicit user instruction.

## Designing New Commands

When adding or refactoring a CLI command, preserve the agent-native surface:

- dual output contract
- typed exit codes
- dry-run conventions
- idempotency behavior
- schema visibility

Every top-level command must be placed in exactly one safety bucket in `src/zotero_cli/cli/safety.py` (`SAFETY_TIERS`, with `READ_COMMANDS` / `WRITE_COMMANDS` / `DESTRUCTIVE_COMMANDS` derived from it):

- read
- write
- destructive

This affects both `zot --help` grouping and `zot schema` output consumed by agents.

## Validation

Before closing substantial changes, run the smallest relevant checks first, then broader ones as needed:

1. Targeted `pytest` for touched behavior
2. `uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'`
3. `uv run ruff check .` and `uv run ruff format --check .`
4. `uv run pytest -q -rf` — the suite must be fully green: 0 failed, 0 skipped. There are no skip markers.
5. `uv build` when packaging, templates, or package data change.

For this repository on this machine, the environment has already been initialized successfully with:

```bash
uv sync --dev
uv run zot --help
uv run pytest -q -rf
```

and the full test suite is expected fully green (0 failed, 0 skipped).
