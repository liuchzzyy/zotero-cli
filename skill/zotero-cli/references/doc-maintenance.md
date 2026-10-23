# Documentation maintenance for this repository

Use for documentation changes in this checkout. Resolve the repository root from the working directory and Git state, then read `AGENTS.md`, the affected implementation, tests and current diff. Do not derive behavior from old prose alone.

## Documentation map

| File | Language | Purpose |
| --- | --- | --- |
| `README.md` | Chinese | Installation, configuration, commands, behavior and limits |
| `AGENTS.md` | Chinese | Coding conventions, architecture and invariants |
| `skill/zotero-cli/SKILL.md` | English | Agent operations and recovery |
| `skill/zotero-cli/references/*.md` | English | Detailed verification, PDF routing and formatting procedures |

`tests/README.md` and tool-specific READMEs are not present. Inventory actual files before naming documentation targets. Preserve existing deletions; do not restore retired plans merely because an older instruction mentions them. Prompt templates under `src/zotero_cli/ai_notes/templates/` are executable model inputs: change them only when the task requires a prompt behavior change.

## Procedure

1. Inspect `git status --short --branch`, `git log -1 --oneline`, `git diff --stat` and the affected code diff. Preserve user edits and deletions; do not commit or push without an explicit request.
2. Check commands with `uv run zot --help`, `uv run zot COMMAND --help` and `uv run zot schema COMMAND ...`. Include the full command path, for example `schema zotero collection reorganize`, not `schema collection reorganize`. Read environment-variable handling and `.zot/config.example.toml` without exposing local credentials.
3. Read `src/zotero_cli/cli/app/safety.py` for safety classification. The `zotero collection` parent is read-tier; create/move/rename are write, delete/reorganize are destructive. Nested config and PDF RAG mutations have their own entries. Help and schema derive from this map.
4. Update README, coding guidance, bundled Skill and affected references as appropriate. Include behavior, failure recovery and observed limits. Do not duplicate the entire module map in every document.
5. Verify local Markdown links, heading anchors, commands and source references. Prefer file links plus function names over fragile source line numbers; refresh a retained line anchor after code moves. When documenting source behavior, distinguish the base revision from working-tree changes.
6. Format affected Markdown with `uv run ruff format <paths>`, then run `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` and `git diff --check`. Run required repository tests with `uv run pytest -q`; report only observed results. A previously verified unchanged tree does not need repeated checks without a reason.
7. Review the final documentation diff and report edited files, verification and outstanding limits. Keep repository changes uncommitted for manual review. External Hermes profiles and shared installed Skills are separate destinations requiring an explicit request.

## Contracts to keep consistent

- **Read local, write remote.** SQLite reads use the Reader's WAL-aware snapshot. Web API writes can remain invisible locally until Zotero Desktop syncs. Verify remote results through API readback when authorized.
- **Destructive replacement.** `collection reorganize` replaces every old membership of listed items, including collections outside the plan; it does not delete items or old collections. It needs `--yes` or interactive confirmation, and its preview warns about the full scope. `--no-interaction` is not confirmation.
- **Validation boundaries.** Unknown search/list `--type` is `validation_error`; valid types without matches return empty results. `storage:` and `attachments:` paths stay inside their respective roots, including after symlink resolution. Explicit absolute/file URL linked attachments remain supported.
- **AI evidence is not scientific validation.** `parse_text_packet()` checks structure, required fields, known attachment/kind/page source labels, valid figure numbers and packet budget. It does not compare or reject numeric/text content. `source_excerpt` gives the model original text for checking; it proves neither a numerical value nor a sample/condition mapping.
- **AI Note input budgets.** Full analysis consumes structured `content_list.json`, dedupes documents across the whole item and uses `max_extracted_chars` plus per-route context/request budgets. Only short-note input uses the 220,000-character helper. All valid main images are required; SI has no fixed count cap. Image/request thresholds are application preflight values, not provider guarantees.
- **Partial writes.** Creating a note before a failed tag, or updating Extra before a failed `ai/keywords` tag, means earlier work already landed. Preserve `committed=True`, retain idempotency reservations and require reconciliation; do not cache such a result as success or imply that `ai/no_keywords` was written. Definite rejections release the relevant reservation only when no write may have landed.
- **MinerU request/cache limits.** Missing tokens use `MinerUTokenMissingError`, classified by type; all-cache-hit reads need no token. Confirmed-live lock owners are waited on, dead owners reclaimed, unknown liveness reclaimed only when stale. Archive GETs use `stream=True`, bounded reads, response closure and per-origin authentication checks, including retry/redirect paths. Batch POST is not retried automatically.
- **Publication is not a transaction.** MinerU removes an old destination before replacement: publication failure can lose the old cache; delayed property reads have no snapshot guarantee. PDF RAG candidate-index publication has different behavior. Polling sleeps do not establish a strict total deadline.
- **Batch error shapes stay stable.** `_write_error_fields()` deduplicates internal construction. `apply_publication_updates()` intentionally keeps its three-field error object without `committed`.
- **Dry runs have command-specific scope.** AI Note dry-run skips note-model calls and Zotero writes but can invoke MinerU and write its parse cache. Do not claim that every dry-run has zero local/network side effects.

## Common pitfalls

- `.zot/config.example.toml` is tracked; local `.zot/config.toml` is ignored. Never copy real keys/tokens into the example or show their values. Preserve user edits; report credential problems without silently reverting them.
- CI is `.github/workflows/ci.yml`: Python 3.12, lint, format, ty, tests and wheel/sdist build. The scheduled/manual best-effort dependency audit is not a PR gate. The suite is expected green with no skipped-test baseline; do not change tests merely to hide a failure.
- `delete` is permanent Web API deletion. `trash restore` restores an already-trashed item and cannot undo permanent deletion. There is no `trash empty` command.
- Envelope top level is closed: `ok`, `data`/`error`, `meta`, optional `dry_run` and `next`; `ok` is true, false or `"partial"`. Do not flatten per-item error contracts into a newly invented global shape.
- Local fixture/mocked transport checks do not prove live provider availability, model quality, remote write success or multi-process stress behavior. Keep historical sample measurements explicitly historical and state when they were not re-run.
