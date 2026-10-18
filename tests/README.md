# Test architecture

The suite requires **CPython 3.12** (the only supported interpreter, `>=3.12,<3.13`) and runs offline. The suite is organized by behavior rather than implementation history:

- `unit/`: isolated core logic, models, readers, writers, renderers, providers, config loaders (profile-aware sections, atomic/symlink-safe writes, TOML value coverage), and the sanitizer. Reader tests cover both full-text schemas and the `fulltext_limited` flag; writer tests pin `pyzotero>=1.16,<2` response shapes and attachment upload behavior offline.
- `cli/`: Click commands, JSON envelopes (including tri-state `ok: "partial"` and validated `next` hints), exit codes, dry-run/confirmation behavior, permanent-delete and raw-PATCH trash-restore semantics, schema `--diff`/schema-guard errors, cite/open machine-mode side effects, mutation guards, the top-level error boundary, and streaming.
- `integration/`: multi-module PDF, workspace RAG (prune/empty-workspace cleanup, embed deadline/retry, degraded query, safe vector-store pruning, manifest guards), and tool-script workflows. Tool tests live in `tools/**/test_*.py` and are collected by the same run.
- `fixtures/`: the deterministic Zotero database (v130-shaped) and PDF inputs.
- `support.py`: shared CLI harness, isolated environment, fixture paths, and JSON parsing.
- Repository `conftest.py`: rejects unmocked outbound socket connections for both `tests/` and `tools/`, isolates runtime state, and tags `tools/**` tests with the `tools` marker.
- `tests/conftest.py`: clears inherited `ZOT_*` settings and derives pytest markers from test directory names.

Markers:

- `unit`, `cli`, `integration`: derived from the test directory.
- `tools`: standalone batch-helper tests under `tools/`, collected by default `testpaths` but selectable with `-m tools`.

```bash
uv run pytest -q -rf
uv run pytest -q -m unit
uv run pytest -q -m cli
uv run pytest -q -m integration
uv run pytest -q -m tools
```

The suite is expected fully green: **0 failed, 0 skipped**. There are no skip markers — platform-specific behavior is tested on every platform by patching the platform boundary (for example `tests/unit/test_platform_paths.py` covers Windows path conversion on macOS and Linux too). Subprocess-driven tests set explicit timeouts.

Tests must not inherit real Zotero, MinerU, embedding, or rerank credentials and must never call live services. Use `tests.support.invoke_cli` for command tests and explicitly pass only the fake values required by the behavior under test.

Quality checks include standalone tools, not only the installed package:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'
uv run pytest --cov --cov-report=term-missing
```

Coverage includes production code under `src/` and `tools/`, but excludes test modules. `.github/workflows/ci.yml` runs these gates plus `uv build` on Python 3.12 only; a weekly/manual, network-dependent `pip-audit` job is best-effort and non-blocking. A green offline suite verifies the fixture-driven workflows; it does not certify live Zotero, MinerU or model availability.
