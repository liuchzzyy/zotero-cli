# AGENTS.md

This file provides guidance to coding agents working with code in this repository.

## Project

`zotero-cli` (binary: `zot`) is a Zotero CLI built for AI agent use. It combines **direct local SQLite reads** with **Zotero Web API writes**. The `zot` CLI is the single entry point; everything under `tools/` is a separate batch helper, not a subcommand.

The CLI follows an agent-native contract enforced by the Click tree, `zot schema`,
the output layer (`src/zotero_cli/output/`), typed exit codes
(`src/zotero_cli/output/exit_codes.py`), and the agent-interface tests:

- exactly one stable JSON envelope per invocation in JSON mode
- typed exit codes (0 ok, 1 runtime/internal, 2 auth, 3 validation/config/confirmation, 4 not found, 5 network/rate, 6 conflict)
- `zot schema` introspection
- `--dry-run` on every write/destructive command
- `--yes` confirmation for destructive commands
- `--idempotency-key` for retry-safe mutations
- NDJSON streaming (`--stream`)

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

# Run a single test / file / node / marker
uv run pytest tests/unit/test_reader.py -v
uv run pytest tests/unit/test_reader.py::test_name -v
uv run pytest -k "search and not rag" -v
uv run pytest -q -m tools

# Run the CLI from source
uv run zot --help
uv run zot search "foo"
uv run zot schema

# Build artifacts
uv build
```

The package supports Python 3.10–3.13. CI is checked in at `.github/workflows/ci.yml`: lint, format check, mypy, the full test suite, and wheel/sdist build run on Python 3.10 and 3.13; a weekly/manual best-effort `pip-audit` job covers dependency CVEs (network-dependent, non-blocking). `testpaths` covers `tests` and `tools`, so the `tools/**/test_*.py` tests run with the rest (they carry the `tools` marker). The suite is expected fully green: 0 failed, 0 skipped, with no skip markers.

## Directory map

```
src/zotero_cli/
├── cli/         click root, global flags, safety tiers, error boundary,
│                command registration
├── commands/    one package per feature area: items, notes, attachments,
│                collections, workspace, system
├── core/        domain logic: zotero, notes, mineru, rag, plus
│                workspace_store.py, platform_paths.py, version_check.py
├── output/      envelope, renderers, streaming, exit codes (+ error boundary)
├── config/      paths, models, loaders, zotero resolution; public API in
│                __init__.py
└── models.py    shared dataclasses
tests/           unit/, cli/, integration/, fixtures/ + shared harness
tools/           non-CLI batch helpers: ainote, paper2zotero, paperrag
skill/zotero-cli/  bundled agent skill
.github/workflows/ci.yml  quality matrix + weekly dependency audit
```

## Architecture

### Read/write split

This is the central design constraint.

- **Reads** go through `src/zotero_cli/core/zotero/reader.py` and open `zotero.sqlite` directly from the local Zotero data directory. SQL-free citation formatting (BibTeX / CSL-JSON / RIS) lives in `core/zotero/citations.py` as `CitationExportMixin`, which `ZoteroReader` composes. Read scoping is mixed by design (M-01, intentionally not changed in code): child reads (`get_notes`, `get_attachments`, `get_pdf_attachment(s)`) return empty for a trashed parent and filter trashed children; `search` and `get_recent_items` exclude `deletedItems`; but item-level APIs (`get_item`, `get_collection_items`, `get_related_items`, `get_stats`, `get_arxiv_preprints`, `find_duplicates`) do **not** currently filter trashed items, so callers such as `read`/`export`/`cite`/`summarize` may surface a trashed key. Treat these as known limitations; do not assume a global trash filter when adding read commands.
- **Writes** go through `src/zotero_cli/core/zotero/writer.py` and use `pyzotero` against the Zotero Web API. Web API error translation lives in `core/zotero/writer_errors.py` and the Extra short-note merge in `core/zotero/short_note_merge.py`.

Never write to `zotero.sqlite` directly. That would bypass Zotero's sync model and can corrupt sync state.

`reader.py` uses two read paths. When `<db>.sqlite-wal` exists and is non-empty it copies the DB plus WAL to a temp file and replays it, because `immutable=1` on the live file returns a stale snapshot while Zotero Desktop is running (recently synced attachments look missing and PDF paths look broken). Keep that branch when touching connection setup; a plain `immutable=1` reconnect is a regression.

### Search across userdata schemas

`search` works on both legacy Zotero 6 databases and Zotero 7 userdata v130. Legacy databases keep `fulltextWords`/`fulltextItemWords`; v130 databases keep only `fulltextItems` and store the extracted text in per-attachment `storage/<KEY>/.zotero-ft-cache` files. The reader detects the schema at runtime and:

- uses the legacy word tables when present;
- on v130 scans a **bounded** set of `.zotero-ft-cache` files instead (`_FULLTEXT_CACHE_MAX_FILES = 1000`, 2 MiB per file, 16 MiB total), because loading the whole corpus would be unbounded;
- degrades to metadata-only matching when the tables/caches are absent or unreadable, and never crashes;
- refuses to assume an unsupported userdata schema outside the supported 100–200 range.

The bounded scan is a deliberate tradeoff: it keeps full-text search usable and memory-safe on large v130 libraries, but a hit deep beyond the file/byte budget can be missed. Do not "fix" it by scanning everything without a budget.

### CLI shape

- `src/zotero_cli/cli/` is the Click package: `root.py` defines the root group (`main`, the `zot = "zotero_cli.cli:main"` console entry); `__main__.py` supports `python -m zotero_cli.cli`; `registry.py` registers every command in observable order; `global_flags.py` hoists `--json` / `--no-json` from anywhere in argv; `tiered_group.py` renders `zot --help` grouped by safety tier; `safety.py` is the single source of truth for read/write/destructive tiers and is consumed by both `tiered_group` and `zot schema`.
- `src/zotero_cli/commands/` holds one package per feature area: `items/`, `notes/`, `attachments/`, `collections/` (`commands.py` + reorganize plan), `workspace/` (`commands.py`, indexing, embedding, query), and `system/` (config split into `commands.py` / `profiles.py` / `cache.py`, plus completions and schema). Command implementations live in each package's `commands.py`; larger areas split cohesive helpers into sibling modules.
- `src/zotero_cli/output/` implements the dual output contract: Rich / human-readable output for TTY use, JSON envelope when piped or when `--json` is enabled. Commands import envelope builders from `output/envelope.py`, renderers from `output/renderers.py`, streaming helpers from `output/streaming.py`, and typed exit behavior from `output/exit_codes.py`.
- `output/exit_codes.py` is also the top-level error boundary: Click usage/parameter errors become `validation_error` envelopes (exit 3, even in JSON mode); `FileNotFoundError`/`PermissionError`/TOML type errors become `configuration_error`; `sqlite3.Error`/unexpected exceptions become `runtime_error`/`internal_error` (exit 1). No traceback leaks to stdout/stderr by default; `--verbose` (or `ZOT_DEBUG`) prints the traceback to stderr for debugging.
- `zot schema` reflects the Click tree for agent consumption: commands, params, and each command's `safety_tier` (resolved by `cli/safety.py:safety_tier_for_path`).

### Dry-run, confirmation, and envelopes

- Every write/destructive command and subcommand has `--dry-run` with zero side effects; envelopes mark `dry_run` and human output says so. This includes `workspace new/add/remove/import/index/reindex/embed/delete`, `collection create/move/rename/delete/reorganize`, `config init/profile set/cache clear`, and `update-status` (preview is also its default; `--apply` is the only write path).
- Destructive commands (`delete`, `workspace delete`, `collection delete`, `config cache clear`) require explicit confirmation: `--yes` (or `--dry-run` preview). `--no-interaction` never implies `--yes`; a non-TTY invocation without `--yes` or `--dry-run` fails with `confirmation_required` (exit 3). `trash restore` is a write-tier command that supports `--dry-run` but currently has no `--yes`/interactive confirmation gate.
- JSON mode emits exactly one envelope on stdout per invocation; progress and warnings go to stderr. `--stream` emits NDJSON item envelopes plus a summary for `list`/`recent`/`search`, takes precedence over `--no-json`, and `recent` reports `has_more` in the summary. `export --format json` stays raw JSON in text mode and is enveloped under `--json`.
- `stats`, `summarize`, `summarize-all`, config profile/cache commands, and all workspace/collection commands emit envelopes in JSON mode.
- Use an `--idempotency-key` for retryable mutations: a confirmed success replays the original result, while an in-flight/unknown outcome requires reconciliation. Writer failures are typed and carry a `committed` flag when a mutation may have reached the server; never blindly retry a possibly-committed write.

When adding a command, place it under the matching `commands/<area>/` package, register it in `cli/registry.py` (preserve the observable order), classify it in `cli/safety.py:SAFETY_TIERS`, and implement the dry-run/--yes/envelope contract above. Otherwise help output, schema reporting, and the agent contract drift.

### Core subsystems

- `src/zotero_cli/core/zotero/`: `reader.py` SQLite read layer (WAL-aware, trash-mixed, schema-aware search); `citations.py` citation export mixin; `writer.py` Web API write layer (notes, tags, item fields); `writer_errors.py` typed write errors with `committed` semantics; `short_note_merge.py` Extra short-note merge; `attachments.py` attachment path resolver; `metadata.py` DOI→Zotero metadata via Crossref (`resolve_doi`); `publication_status.py` Semantic Scholar preprint-to-published lookups for `update-status`; `idempotency.py` retry-safe mutation cache/reservations.
- `src/zotero_cli/core/mineru/`: `__init__.py` exposes the public API; `client.py` upload/poll/download flow and provider-response validation; `cache.py` content-addressed parse packages under `.zot/state/mineru/` with cross-process `.locks/` and hidden staging; `models.py` shared types/errors/limits (200 MiB file, 512 MiB download, 200 MiB/member, 1 GiB extracted total, 10,000 entries); `utils.py` helpers. Hashing happens once per PDF; only transient errors are retried (MinerU batch POST is not retried).
- `src/zotero_cli/core/notes/`: the `ai_analyze` pipeline — `client.py` OpenAI-compatible chat with multi-model fallback, request timeout/retry bounds, provider error taxonomy; `pipeline.py` per-item orchestration (dry-run before classifier, dedupe before figure numbering, 220k-char prompt cap); `classification.py` classification + JSON extraction; `documents.py` document payload/dedup helpers; `figures.py` multimodal figures (12 count cap, 20 MiB raw-bytes cap); `renderer.py` inline-styled HTML rendering; `templates.py` template lookup + untrusted-data delimiters; `text.py` CJK/Latin + super/subscript normalisation; `short_note.py` short-note normalisation/validation (500-char cap after padding); `constants.py`/`errors.py`. Prompt templates live under `tools/ainote/templates/`.
- `src/zotero_cli/core/rag/`: `__init__.py` exposes the documented public API; `chunking.py` MinerU structured-JSON and plain-text chunking (no empty chunks; overlap only on the character fallback); `metadata.py` clean_html entity guards, tokenisation, PDF-kind/SI inference; `index.py` SQLite FTS5 (BM25) term index preserving raw scores; `fusion.py` weighted reciprocal rank fusion; `embeddings.py` embedding providers; `rerank.py`; `vector_store.py` local Qdrant store with collection drop/prune helpers; `providers/gitee.py`.
- `src/zotero_cli/core/workspace_store.py`: repo-local workspaces; atomic, JSON-escaped TOML manifests (newlines/tabs/control chars), 200-byte name cap, unreadable manifests warn instead of crashing; delete drops the `ws_<name>` Qdrant collection and prunes empty stores. `platform_paths.py`: cross-platform path helpers; `version_check.py`: PyPI notice (TTY-only, `ZOT_NO_UPDATE_CHECK` disables).
- `src/zotero_cli/config/`: documented import surface in `__init__.py`; `paths.py` (checkout vs installed state roots), `models.py`, `loaders.py` (TOML + env overrides; atomic writes with `.bak`, 0600/0700 permissions; `.zot/config.toml` merge), `zotero.py` (data dir / credential resolution).

### ai_analyze specifics

- Dry-run needs no AI configuration: it still resolves and parses PDFs through MinerU, reports `paper_type` from the item type default (not the classifier), the template, `pdf_count`, extracted chars, figure counts and a prompt preview; it skips the classifier/note model calls and all writes. It previews already-`ai/noted` items instead of returning early.
- Prompt safety: title/authors/journal/date/DOI/full text are wrapped as untrusted data with explicit delimiters; templates state the instruction hierarchy; the whole prompt is bounded at `MAX_PROMPT_CHARS` (220,000).
- Figures: `core/notes/figures.py` rewrites `![](images/<sha>.jpg)` to `【图 N】` markers, filters assets by size, caps the count at `MAX_FIGURES` (12, `core/notes/constants.py`), and caps total raw image bytes at 20 MiB per request. Duplicate document copies are dropped **before** figure marking/numbering. `core/notes/client.py` attaches them as `image_url` parts and falls back to the next model when a provider rejects image content.
- Model chain: `AiClient.get_model_chain()` — `model` followed by `fallback_models` (TOML array or comma-separated string). Local `base_url` values (`127.0.0.1` / `localhost`) disable HTTP proxy env handling. Request bounds are `timeout_seconds` / `max_retries` (env `ZOT_AI_TIMEOUT` / `ZOT_AI_MAX_RETRIES`).
- Text: `core/notes/text.prepare()` flattens super/subscripts and inserts a half-width space between CJK and Latin (`pad_cjk_latin(plain_super_subscripts(text))`); `core/notes/renderer.py` applies it before HTML escaping. The note title is escaped but not re-spaced, because `NOTE_TITLE_PREFIX` matching depends on it.
- Short notes: `core/notes/short_note.py` normalises punctuation and validates the padded text: 500-char cap after CJK/Latin spacing, no newlines/brackets, `疑问：` as the last segment.
- Writes: `analyze_item` runs the classification/LLM calls and writes the note/tags/short-note behind typed errors; `NoteAnalysisError.committed` marks a mutation that may have reached the server so callers do not duplicate notes. `--force` appends a new note, never replaces/deletes old ones.
- Progress: `--progress-lines` emits newline-delimited progress; without it, TTY progress uses carriage-return updates and non-TTY runs stay quiet.
- Templates: `core/notes/templates.template_path` probes `tools/ainote/templates/` in a checkout and falls back to the packaged `zotero_cli/templates/` that hatchling force-includes into the wheel — an installed CLI has no `tools/`, so keep the packaged candidate when editing the lookup.

### Tools and bundled skill

- Tools under `tools/` are **not** `zot` subcommands: `zot schema` never lists them. See `tools/README.md` for the directory overview. Their tests carry the `tools` marker and run in the default suite.
- `tools/paperrag/`: maintained zsh entrypoints `run-rag-workspace.zsh` (inventory, incremental index, embedding backfill) and `run-rag-evidence-search.zsh`, plus `zotero-workflow-common.zsh` and `workspace_inventory.py`. Run directories are uniquely named (`<workflow>-<timestamp>-<pid>-<random>`) and successful runs delete only directories they created (pre-existing dirs are kept); failed runs remain for diagnosis. Usage is documented in `tools/paperrag/Instructions.md`; artifacts go to `log/`.
- `tools/paper2zotero/`: `zotero_inbox.py`, `backfill_titles.py`, `translate_abstracts.py`, `batch_translate_metadata.py`, and the shared `zh_text.py`. Writes go through the Zotero Web API with readback; checkpoints default to the git-ignored `.zot/state/paper2zotero/` (old tool-tree checkpoint files are not auto-migrated), and translation resume is per completed batch with fail-closed handling of corrupt checkpoints. Optional publisher-blocked abstract overrides come from a local JSON file named by `ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS`; no library data is committed in source. See `tools/paper2zotero/README.md`.
- `tools/ainote/`: `batch_analyze.py` runs the `ai_analyze` pipeline over a collection; `--collection` is required, `--dry-run` and `--apply` are mutually exclusive and dry-run has zero side effects (no Zotero writes, no checkpoint writes, no LLM calls; PDFs may still be parsed via MinerU). `templates/` holds the `ai_analyze` prompt templates, force-included into the wheel by `pyproject.toml`.
- Tool tests are offline: `tests/integration/test_tool_scripts.py` handles the zsh entrypoints (mode + `zsh -n` + dry runs), and `tests/integration/test_inventory_edges.py` loads `workspace_inventory.py` by path.

Do not reintroduce the old flat `tools/*.zsh` layout; the scripts now live in `tools/paperrag/`.

### Agent Contract and Skill

- `zot schema` is the authoritative machine-readable CLI surface.
- `tests/cli/test_contract.py`, `test_contract_mutations.py`, and `test_contract_streaming.py` are the regression guardrails for envelope shape, exit codes, dry-run behavior, confirmation, streaming, and safety tiers.
- `skill/zotero-cli/` is the bundled agent skill and should stay aligned with real CLI behavior.
- No `docs/` tree or MkDocs config is checked in. Do not treat missing docs paths as source of truth.

If the CLI surface changes, update schema-visible behavior, the contract tests, `AGENTS.md`, the README, the tools docs, and the bundled skill together.

## Config and Profiles

Config lives at:

- `.zot/config.toml` in a checkout (installed packages use the per-user config search path)

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
- `ZOT_STATE_DIR`
- `ZOT_DEBUG`
- `ZOT_NO_UPDATE_CHECK`
- `ZOT_AI_API_KEY`
- `ZOT_AI_BASE_URL`
- `ZOT_AI_MODEL`
- `ZOT_AI_FALLBACK_MODELS`
- `ZOT_AI_REASONING_EFFORT`
- `ZOT_AI_TEMPERATURE`
- `ZOT_AI_MAX_TOKENS`
- `ZOT_AI_MAX_EXTRACTED_CHARS`
- `ZOT_AI_TIMEOUT`
- `ZOT_AI_MAX_RETRIES`
- `ZOT_EMBEDDING_*`
- `ZOT_RERANK_*`
- `ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS` (tools only)

`load_ai_note_config` applies the `ZOT_AI_*` variables on top of the file (`env > file > defaults`, same shape as `load_pdf_config`). `ZOT_AI_FALLBACK_MODELS` is comma-separated. Pass `apply_env_overrides=False` in a test that needs the raw file values. `ZOT_AI_TIMEOUT` / `ZOT_AI_MAX_RETRIES` bound each model request. Embedding/rerank providers read `ZOT_EMBEDDING_*` / `ZOT_RERANK_*` over their file sections.

Relevant sections:

- `[ai_notes]`: `api_key`, `base_url`, `model`, `fallback_models`, `reasoning_effort`, `temperature`, `max_tokens`, `max_extracted_chars`, `timeout_seconds`, `max_retries`.
- `[pdf]`: `mineru_token`, `mineru_model_version`.
- `[embedding]`, `[rerank]`, `[semantic_search]`: Gitee providers and hybrid retrieval weights.

Config write behavior: `config init` / `config profile set` merge and preserve all existing sections + the active profile, write atomically (temp file + fsync + replace), keep a `.bak` backup, and set file 0600 / created dirs 0700. `config show` honors `--profile` / `ZOT_PROFILE`; `config cache clear` requires `--yes` and supports `--dry-run`; `config profile list/set` and `cache stats/clear` emit JSON envelopes. `ZOT_DATA_DIR` / `ZOT_PREFS_JS_PATH` expand `~`; explicit missing `--config-path`, malformed TOML, and type errors become `configuration_error` (exit 3).

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
- `.zot/state/paper2zotero/`: paper2zotero resume checkpoints (moved out of the tracked tools tree; old files must be migrated manually)
- `log/`: per-run artifacts (`run.log`, `progress.jsonl`, `inventory.json`), safe to prune

State-root resolution: in a source checkout, runtime state stays under the repository's `.zot/state`; installed packages use a deterministic per-user root — `ZOT_STATE_DIR` if set, else `$XDG_STATE_HOME/zotero-cli` (`~/.local/state/zotero-cli`) or `%LOCALAPPDATA%/zotero-cli` on Windows. Idempotency reservations, the version-check cache, MinerU packages, and paper2zotero checkpoints all live under this state root.

## Conventions

- Type hints are required. `mypy` is enforced on `src/zotero_cli` and `tools` (test modules excluded).
- Ruff target is Python 3.10 with line length 120.
- Keep changes surgical. Do not refactor unrelated areas.
- Format only the files you touch (`uv run ruff format <paths>`); never run a repo-wide formatter over unrelated files.
- Preserve the current license metadata (`MIT`) unless explicitly asked to change it.
- Do not run `git commit` or `git push` without explicit user instruction.
- Tests must never call live services, and no skip markers are allowed.

## Designing New Commands

When adding or refactoring a CLI command, preserve the agent-native surface:

- dual output contract (exactly one JSON envelope; progress on stderr)
- typed exit codes
- zero-side-effect `--dry-run` on every write/destructive command, with a `dry_run` marker
- `--yes` confirmation for destructive commands; `--no-interaction` must never imply `--yes`
- idempotency behavior and typed/committed write errors
- NDJSON streaming where result sets can be large
- schema visibility

Every top-level command must be placed in exactly one safety bucket in `src/zotero_cli/cli/safety.py` (`SAFETY_TIERS`, with `READ_COMMANDS` / `WRITE_COMMANDS` / `DESTRUCTIVE_COMMANDS` derived from it):

- read
- write (including `config init`, `config profile set`, and every workspace-mutating subcommand: new/add/remove/import/index/reindex/embed)
- destructive (including `config cache clear` and `workspace delete`; `collection delete` and `delete` remain destructive)

This affects both `zot --help` grouping and `zot schema` output consumed by agents.

## Validation

Before closing substantial changes, run the smallest relevant checks first, then broader ones as needed:

1. Targeted `pytest` for touched behavior (tools: `uv run pytest -q -m tools`)
2. `uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'`
3. `uv run ruff check .` and `uv run ruff format --check .`
4. `uv run pytest -q -rf` — the suite must be fully green: 0 failed, 0 skipped. There are no skip markers.
5. `uv build` when packaging, templates, or package data change.

CI runs the same gates on Python 3.10 and 3.13, plus a best-effort weekly `pip-audit`. For this repository on this machine, the environment has already been initialized successfully with:

```bash
uv sync --dev
uv run zot --help
uv run pytest -q -rf
```

and the full test suite is expected fully green (0 failed, 0 skipped).

## Documentation Map

- `README.md` — Chinese, user-facing install/config/commands.
- `AGENTS.md` — this file: architecture, invariants, validation order.
- `tests/README.md` — test layout, markers, green-suite expectation, CI.
- `tools/README.md`, `tools/paper2zotero/README.md`, `tools/paperrag/Instructions.md` — non-CLI helpers.
- `skill/zotero-cli/SKILL.md` + `references/` — bundled agent skill.
- `.zot/config.example.toml` — credential-free, machine-neutral config template.

When in doubt, re-derive the surface from the running CLI (`zot --help`, `zot COMMAND --help`, `zot schema`) rather than from prose.
