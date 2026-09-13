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

```powershell
# Install dev environment
uv sync --dev

# Lint / type-check / test
uv run ruff check src tests
uv run python -m mypy src/zotero_cli
uv run pytest -q

# Run a single test / file / node
uv run pytest tests/test_reader.py -v
uv run pytest tests/test_reader.py::test_name -v
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

- `src/zotero_cli/core/reader.py`: SQLite read layer.
- `src/zotero_cli/core/writer.py`: Web API write layer (notes, tags, item fields, Extra short-note merge).
- `src/zotero_cli/core/pdf_extractor.py`, `pdf_cache.py`, `pdf_errors.py`: PDF extraction (MinerU API / PyMuPDF) and caching.
- `src/zotero_cli/core/ai_client.py`, `note_analysis.py`, `note_renderer.py`, `note_templates.py`: the `ai_analyze` pipeline — OpenAI-compatible chat, item classification / note generation / short-note keywords, inline-styled HTML rendering, prompt templates under `tools/templates/`.
- `src/zotero_cli/core/rag.py`, `rag_index.py`, `rerank.py`: PDF→text conversion, chunking, SQLite FTS5 (BM25), reciprocal rank fusion, Gitee embedding/rerank helpers.
- `src/zotero_cli/core/semantic_search/vector_store.py`: local Qdrant vector store.
- `src/zotero_cli/core/providers/gitee.py`: Gitee AI embedding / rerank provider.
- `src/zotero_cli/core/workspace.py`: repo-local workspaces under `.workspace/`.
- `src/zotero_cli/core/idempotency.py`: retry-safe mutation support.
- `src/zotero_cli/core/semantic_scholar.py`: preprint-to-published lookups for `update-status`.
- `src/zotero_cli/core/version_check.py`: version notice logic.

### Agent Contract and Skill

- `zot schema` is the authoritative machine-readable CLI surface.
- `tests/test_agent_interface.py`, `tests/test_agent_p1.py`, and `tests/test_agent_p2.py` are the regression guardrails for envelope shape, exit codes, dry-run behavior, streaming, and safety tiers.
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
- `ZOT_PROFILE`

Zotero data dir auto-detects when not configured, but this checkout now treats repo-local `.zot/config.toml` as the primary persistent source of truth.

Current local config is expected at:

- `/Users/liuchzzyy/python-code/zotero-cli/.zot/config.toml`
- active default profile `macbook`
- real local database at `/Users/liuchzzyy/Zotero/zotero.sqlite`
- working Web API credentials for writes

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

```powershell
uv sync --dev
uv run zot --help
uv run pytest -q
```

and the full test suite passed during setup.

