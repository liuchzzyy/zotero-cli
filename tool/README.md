# Bundled tool

Scripts in this directory are maintained workflow helpers that live *outside*
the `zotero_cli` package. They are **not** `zot` subcommands: `zot schema`
never lists them and they add nothing to the CLI contract. Each script either
drives `uv run zot ...` as a subprocess (`paperrag/`) or imports the package
from this checkout (`ainote/`, `paper2zotero/`), and all of them obey the same
invariant as the CLI: the local SQLite database is read-only and every
Zotero-library mutation goes through the Zotero Web API. Tools may create local
state (workspace indexes, `log/` run directories, checkpoints) as plain files;
those live under the git-ignored `.zot/state/` or `.workspace/` trees.

They run on the repository's single supported interpreter, CPython 3.12. Run them from the repository root, e.g.
`uv run python tool/paper2zotero/translate_abstracts.py --collection "00 | 收件箱"`
or `tool/paperrag/run-rag-workspace.zsh --help`. `uv run pytest -q` collects
their tests too because `[tool.pytest.ini_options] testpaths = ["tests", "tool"]`;
they carry the `tool` marker (`uv run pytest -q -m tool`). The repository suite
is expected fully green (0 failed, 0 skipped).

## `tool/ainote/`

Batch runners for `zot ai-note`. Prompt templates live in `src/zotero_cli/ai_notes/templates/`. `batch_analyze.py`
requires `--collection` (name or key) and walks it, including subcollections
unless `--no-recursive`. It skips items already tagged `ai/noted` or
`ai/uncertain` (unless `--force`) and items marked done in the checkpoint, then
calls the same `analyze_item` pipeline as the CLI and checkpoints per-item status
to `.zot/state/batch_ainote_<collection>.json` so an interrupted run resumes.
The modes are strictly separated: `--dry-run` previews through the same pipeline
with no Zotero writes, no checkpoint writes and no LLM calls (it still parses
PDFs via MinerU, which may be a cloud call when the parse is not cached), while
`--apply` writes and checkpoints.
`scheduled_analyze.py` is the conservative scheduler entrypoint (macOS/Linux):
`--collection KEY --output-dir PATH` requires exactly one of `--apply` or
`--dry-run`, and processes direct members only. It reconciles live Web API
membership/tags with the local inventory; `ai/noted`, `ai/uncertain`, completed
checkpoints and existing AI Notes suppress repeat analysis. Unsynced/missing
local PDFs are deferred. The inventory-only dry run makes no MinerU/LLM/library
writes, but saves a local run report. Apply records `in_progress` before each
analysis, verifies exact-key Web API readback, and refuses blind retries after
an uncertain write. A verified AI Note with failed short-note generation is
recorded as `degraded`, not re-analyzed; a prior `in_progress` entry needing
reconciliation is reported under `blocked`. Item-level errors stay in the
`failed` array while later items continue; a completed inventory exits 0 even
when `failed` is nonempty, but initialization errors still exit nonzero. Failed
checkpoint items run after fresh candidates, so they do not repeatedly consume
the budget ahead of unseen papers. A per-run time budget defers remaining
candidates to later runs: exit 0 does not mean every item was analyzed. An
advisory lock prevents overlapping scheduler runs; empty/all-skipped runs
emit no stdout. Reports live in the specified output directory, and the
collection checkpoint remains under `.zot/state/`.

Because `zot ai-note` now fails closed on input it cannot fit (structured text
over `max_extracted_chars`, or more main figures than the token budget allows),
only an explicitly marked input-budget failure is treated as permanent: after
`MAX_ATTEMPTS` (3) the item is parked as `unanalyzable`, listed in the run's
`blocked` array instead of `failed`, and skipped by later runs — otherwise one
oversized paper would be retried on every nightly run. Network/provider
failures remain retryable across scheduled runs and visible in `failed`;
an uncertain write is never parked, since the note may already exist.
`--retry-parked` clears parked checkpoints so they are attempted again
after you fix the cause (raise the cap, split the PDF).

`src/zotero_cli/ai_notes/templates/` holds the prompts `zot ai-note` loads at
runtime (`classify-item`, `research-article`, `review-article`, `book`,
`short-note`). They ship inside the `zotero_cli` package, so changing them is
a library change rather than a tool-script change.

## `tool/paper2zotero/`

Chinese-metadata and DOI-intake helpers: `zotero_inbox.py` (import screened
PaperCrossref rows into the inbox collection), `backfill_titles.py`
(library-wide `translated-title` backfill), `translate_abstracts.py`
(collection/item `abstractNote` translation), `batch_translate_metadata.py`
(per-collection Chinese title + abstract sync), and the shared `zh_text.py`
formatting rules. Writes only go through the Zotero Web API and are verified by
readback; resume checkpoints live under the git-ignored
`.zot/state/paper2zotero/` directory. Optional local abstract overrides for
publisher-blocked items come from a JSON file named by
`ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS`, not from committed source data. Machine-
specific defaults (Hermes profile dir, PaperCrossref producer dir, inbox target)
are overridable without editing code: `ZOT_PAPER2ZOTERO_HERMES_HOME`,
`ZOT_PAPER2ZOTERO_INBOX_DIR`, `ZOT_PAPER2ZOTERO_INBOX_KEY`,
`ZOT_PAPER2ZOTERO_INBOX_NAME`. Required configuration, per-script behavior and
test commands: [paper2zotero/README.md](paper2zotero/README.md).

## `tool/paperrag/`

Logged end-to-end workspace RAG entrypoints: `run-rag-workspace.zsh`
(inventory -> incremental index -> embedding backfill) and
`run-rag-evidence-search.zsh` (query), both built on
`zotero-workflow-common.zsh` (repo discovery, safe `log/` run directories, run
logging) plus the Python inventory helper `workspace_inventory.py`. Default
workspace/collection targets can be changed without editing the scripts with
`PAPERRAG_WORKSPACE` / `PAPERRAG_COLLECTIONS`. Generated run artifacts go to a
unique `log/` run directory
(`<workflow>-<timestamp>-<pid>-<random>`); a directory that already existed is
kept on success instead of being deleted. Persistent workspaces, indexes and
vectors live in `.workspace/`, and MinerU parse packages in
`.zot/state/mineru/`. Usage and examples:
[paperrag/Instructions.md](paperrag/Instructions.md).

## Rules of thumb

- Never write to `zotero.sqlite`; Zotero-library mutations go through `zot` or
  the Web API writer.
- Run scripts from the repository root so repo-local `.zot/config.toml` and
  `.workspace/` state resolve consistently.
- Keep the zsh entrypoints executable (`755`);
  `tests/integration/test_tool_scripts.py` checks the mode and runs `zsh -n`.
- Tool tests are offline: they mock the Web API, Hermes, and model endpoints.
  Never point them at a real library or live service, and do not add skip
  markers.
