# Test architecture

The suite is organized by behavior rather than implementation history:

- `unit/`: isolated core logic, models, readers, writers, renderers, and providers. The reader tests also pin library scoping: items in `deletedItems`, and notes or attachments whose parent item is trashed, never appear in read results.
- `cli/`: Click commands, JSON envelopes, exit codes, dry-run behavior, and mutation guards.
- `integration/`: multi-module PDF, workspace RAG, and tool-script workflows. Tool tests live in `tools/**/test_*.py` and are collected by the same run.
- `fixtures/`: the deterministic Zotero database and PDF inputs.
- `support.py`: shared CLI harness, isolated environment, fixture paths, and JSON parsing.
- Repository `conftest.py`: rejects unmocked outbound socket connections for both `tests/` and `tools/`.
- `tests/conftest.py`: clears inherited `ZOT_*` settings and derives pytest markers from test directory names.

```bash
uv run pytest -q -rf
uv run pytest -q -m unit
uv run pytest -q -m cli
uv run pytest -q -m integration
```

The suite is expected fully green: **0 failed, 0 skipped**. There are no skip markers — platform-specific behavior is tested on every platform by patching the platform boundary (for example `tests/unit/test_platform_paths.py` covers Windows path conversion on macOS and Linux too).

Tests must not inherit real Zotero, MinerU, embedding, or rerank credentials and must never call live services. Use `tests.support.invoke_cli` for command tests and explicitly pass only the fake values required by the behavior under test.

Quality checks include standalone tools, not only the installed package:

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m mypy src/zotero_cli tools --exclude '(^|/)test_[^/]*\.py$'
uv run pytest --cov --cov-report=term-missing
```

Coverage includes production code under `src/` and `tools/`, but excludes test modules. A green offline suite verifies the fixture-driven workflows; it does not certify live Zotero, MinerU or model availability.
