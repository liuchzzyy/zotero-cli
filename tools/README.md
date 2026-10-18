# Bundled tools

Scripts in this directory are maintained workflow helpers that live *outside*
the `zotero_cli` package. They are **not** `zot` subcommands: `zot schema`
never lists them and they add nothing to the CLI contract. Each script either
drives `uv run zot ...` as a subprocess (`paperrag/`) or imports the package
from this checkout (`ainote/`, `paper2zotero/`), and all of them obey the same
invariant as the CLI: the local SQLite database is read-only and every mutation
goes through the Zotero Web API.

Run them from the repository root, e.g.
`uv run python tools/paper2zotero/translate_abstracts.py --collection "00 | 收件箱"`
or `tools/paperrag/run-rag-workspace.zsh --help`. `uv run pytest -q` collects
their tests too because `[tool.pytest.ini_options] testpaths = ["tests", "tools"]`;
the repository suite is expected fully green (0 failed, 0 skipped).

## `tools/ainote/`

Batch runner and prompt templates for `zot ai_analyze`. `batch_analyze.py`
walks a collection (including subcollections unless `--no-recursive`), skips
items already tagged `ai/noted`, calls the same `analyze_item` pipeline as the
CLI, and checkpoints per-item status to a JSON state file so an interrupted run
resumes. `templates/` holds the prompts `ai_analyze` loads at runtime
(`classify-item`, `research-article`, `review-article`, `book`,
`short-note`); `pyproject.toml` force-includes that directory into the wheel as
`zotero_cli/templates/`, so moving it changes the build, not just docs.

## `tools/paper2zotero/`

Chinese-metadata and DOI-intake helpers: `zotero_inbox.py` (import screened
PaperCrossref rows into the inbox collection), `backfill_titles.py`
(library-wide `translated-title` backfill), `translate_abstracts.py`
(collection/item `abstractNote` translation), `batch_translate_metadata.py`
(per-collection Chinese title + abstract sync), and the shared `zh_text.py`
formatting rules. Writes only go through the Zotero Web API and are verified by
readback. Required configuration, per-script behavior and test commands:
[paper2zotero/README.md](paper2zotero/README.md).

## `tools/paperrag/`

Logged end-to-end workspace RAG entrypoints: `run-rag-workspace.zsh`
(inventory -> incremental index -> embedding backfill) and
`run-rag-evidence-search.zsh` (query), both built on
`zotero-workflow-common.zsh` (repo discovery, safe `log/` run directories, run
logging) plus the Python inventory helper `workspace_inventory.py`. Generated
run artifacts go to `log/`; persistent workspaces, indexes and vectors live in
`.workspace/`, and MinerU parse packages in `.zot/state/mineru/`. Usage and
examples: [paperrag/Instructions.md](paperrag/Instructions.md).

## Rules of thumb

- Never write to `zotero.sqlite`; mutations go through `zot` or the Web API
  writer.
- Run scripts from the repository root so repo-local `.zot/config.toml` and
  `.workspace/` state resolve consistently.
- Keep the zsh entrypoints executable (`755`);
  `tests/integration/test_tool_scripts.py` checks the mode and runs `zsh -n`.
- Tool tests are offline: they mock the Web API, Hermes, and model endpoints.
  Never point them at a real library or live service, and do not add skip
  markers.
