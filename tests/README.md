# Test architecture

The suite is organized by behavior rather than implementation history:

- `unit/`: isolated core logic, models, readers, writers, formatters, and providers.
- `cli/`: Click commands, JSON envelopes, exit codes, dry-run behavior, and mutation guards.
- `integration/`: multi-module PDF, workspace RAG, and tool-script workflows.
- `fixtures/`: the deterministic Zotero database and PDF inputs.
- `support.py`: shared CLI harness, isolated environment, fixture paths, and JSON parsing.
- `conftest.py`: global service isolation and directory-derived pytest markers.

```bash
uv run pytest -q
uv run pytest -q -m unit
uv run pytest -q -m cli
uv run pytest -q -m integration
```

Tests must not inherit real Zotero credentials, embedding keys, or rerank keys. Use `tests.support.invoke_cli` for command tests and explicitly pass only the fake values required by the behavior under test.
