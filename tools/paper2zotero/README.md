# paper2zotero tools

Chinese-metadata and DOI-intake helpers for the user's Zotero library. They are
maintained scripts, not `zot` subcommands; they import `zotero_cli` from this
checkout and follow the CLI's central rule: the local `zotero.sqlite` is only
read, and every Zotero mutation goes through the Zotero Web API and is verified
by readback before it is reported as done.

## Requirements

- Run from the repository root with `uv run python tools/paper2zotero/<script>.py`
  on the repository's single supported interpreter, CPython 3.12, so the package
  and the repo-local config resolve.
- `.zot/config.toml` (env overrides win: `env > profile > defaults`) with
  `data_dir`, `prefs_js_path`, and Zotero library credentials
  (`library_id` + `api_key`; `ZOT_LIBRARY_ID` / `ZOT_API_KEY` override the
  file). Relevant env vars: `ZOT_DATA_DIR`, `ZOT_LIBRARY_ID`, `ZOT_API_KEY`,
  `ZOT_PROFILE`; `ZOT_CROSSREF_MAILTO` overrides the Crossref polite-pool
  contact (the tools resolve it through zotero-cli's shared
  `resolve_crossref_mailto`, so both the file value and the env override work).
  `zot config show` prints the effective path without exposing the full key;
  never print keys or tokens.
- Preview modes still read the remote library through the Web API and therefore
  need the configured Zotero API credentials; they never write. Whatever the
  key's write permission, `--apply` is what performs mutations.
- Network access to `api.crossref.org`, OpenAlex, and the translation backend.
  Tests mock all of them; do not run a real import from a test.
- Two translation backends are in use and are not interchangeable:
  - `backfill_titles.py` and `translate_abstracts.py` shell out to the
    `hermes` CLI with `HERMES_HOME` set to the scientist profile by default;
    set `ZOT_PAPER2ZOTERO_HERMES_HOME` to another Hermes profile directory on a
    different machine (the default lives in the `SCIENTIST_HOME` constant).
  - `batch_translate_metadata.py` uses `zotero_cli`'s configured AI client
    (`[ai_notes]` in `.zot/config.toml`, overridable with `ZOT_AI_*`).
- `zotero_inbox.py` reads the producer CSV from the PaperCrossref directory
  (`SCRIPT_DIR`); set `ZOT_PAPER2ZOTERO_INBOX_DIR` when the producer moves, and
  use `ZOT_PAPER2ZOTERO_INBOX_KEY` / `ZOT_PAPER2ZOTERO_INBOX_NAME` if the inbox
  collection is renamed. The constants are documented defaults, not secrets.
- Checkpoints live under the git-ignored repo state dir,
  `.zot/state/paper2zotero/`, never in the tracked tools tree:
  - `backfill_titles_state.json`
  - `translate_abstracts_state.json` (override per run with `--state-file`)
  - `zh_state_<collection_key>.json` (override per run with `--state-file`)

  Migration note: checkpoints previously written next to the scripts in
  `tools/paper2zotero/` are **not** moved automatically. Move the old file(s) into
  `.zot/state/paper2zotero/` once to keep resuming an in-progress run; otherwise
  the tool starts a fresh checkpoint (only re-translation cost, no data loss).

## Scripts

### `zotero_inbox.py` — screened DOI intake

Reads `updates_<YYYY-MM-DD>.csv` (default: yesterday) from the PaperCrossref
directory and imports the rows the producer marked `selected` into the inbox
collection `00 | 收件箱` (key `AG7NQ5UW`). Required CSV columns:
`doi`, `title_zh`, `abstract_zh`, `is_cathode_material`,
`is_characterization_technique`, plus optional `zotero_status`/`zotero_key`
run columns.

For each selected row it resolves metadata through zotero-cli's Crossref mapper,
writes the Chinese title as a `translated-title:` line in Extra and the Chinese
abstract into `abstractNote`, and readback-verifies the exact DOI, inbox
collection, Extra line and abstract. It fails closed if a translation is missing
or the inbox key/name does not match. Re-runs only trust the CSV's own
`zotero_status`/`zotero_key` columns — this is run-resume, not library-wide DOI
deduplication. The processed CSV is deleted only after the whole run succeeds;
a partial failure keeps it.

```bash
uv run python tools/paper2zotero/zotero_inbox.py --dry-run
uv run python tools/paper2zotero/zotero_inbox.py --date 2026-10-17
```

`--dry-run` only reads the CSV and lists candidate DOIs; it does not touch the
Zotero Web API.

### `backfill_titles.py` — library-wide translated-title backfill

Inventories the *remote* user library through the Web API (optionally limited to
`--collection`), selects titled items without a `translated-title:` line,
stages exact `key`/`title` -> Chinese title translations through Hermes, and
optionally writes one Extra line per item. The checkpoint is
`.zot/state/paper2zotero/backfill_titles_state.json`; staging is checkpointed
after every completed batch, so an interrupted run resumes without re-translating
finished batches. It never overwrites an existing `translated-title`, re-reads
each key before writing, verifies title/DOI/Extra by readback, and refuses to
silently rebuild an unreadable checkpoint. Dry-run is the default (it requires
the Zotero API credentials + network for the inventory read, but performs no
writes); `--apply` performs the writes.

```bash
uv run python tools/paper2zotero/backfill_titles.py
uv run python tools/paper2zotero/backfill_titles.py --collection "00 | 收件箱" --apply
```

### `translate_abstracts.py` — abstract translation backfill

Translates English `abstractNote` values to Chinese for `--collection`,
repeatable `--key`, or `--all`. Items without an abstract are skipped, and
already-Chinese abstracts are skipped unless `--force` is passed. `--force`
re-includes already-Chinese abstracts in the translation staging, but the final
write guard still refuses to overwrite a different valid Chinese abstract (it
only reports `already_up_to_date` when the text is identical); the flag cannot
silently replace existing translations.

Translation batches go through Hermes; the resumable checkpoint is
`.zot/state/paper2zotero/translate_abstracts_state.json` (override with
`--state-file`) and is written after every completed batch, so an interrupted
run resumes the remaining batches. An unreadable or malformed checkpoint is
refused (fail closed) instead of being silently rebuilt. Every write is read
back to assert the stored `abstractNote` matches the normalised text.
Dry-run/preview is the default: it inventories candidates and needs the Zotero
API credentials, but performs no model translation and no writes; `--apply`
stages translations and writes them.

```bash
uv run python tools/paper2zotero/translate_abstracts.py --collection "00 | 收件箱"
uv run python tools/paper2zotero/translate_abstracts.py --key ITEMKEY --apply
```

### `batch_translate_metadata.py` — per-collection title + abstract sync

Syncs one collection (given by key or exact/unique name) in two fields:
`abstractNote` is always translated to Chinese, and with `--with-titles` the
title is translated into an Extra `translated-title:` line. Existing
translations and already-Chinese text are skipped, never overwritten. Missing
English abstracts are recovered from Crossref, the local Zotero
`.zotero-ft-cache` PDF text layer, or OpenAlex before translation; recovery
failures are reported on stderr. Items whose publisher blocks automated
retrieval can be supplied locally with `ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS`: a
JSON file outside the repository mapping Zotero item keys to
`[abstract_text, source_label]` pairs (or `{"abstract": ..., "source": ...}`
objects). No library-specific abstract data is committed in the tool source.

Translations run through `zotero_cli`'s `AiClient` (`[ai_notes]`) and are
normalised by `zh_text`. The state file
(`.zot/state/paper2zotero/zh_state_<collection_key>.json`, or `--state-file`)
is checkpointed atomically after every batch; an unreadable checkpoint is
refused instead of being rebuilt. Without `--apply` the script still inventories
the remote library, recovers abstracts over the network, runs the configured AI
translation and stages the state file — it is a staging run, not a free preview.
`--apply` then writes through the Web API and verifies each field by readback.

```bash
uv run python tools/paper2zotero/batch_translate_metadata.py --collection "00 | 收件箱"
uv run python tools/paper2zotero/batch_translate_metadata.py --collection KEY --with-titles --apply
```

### `zh_text.py` — shared formatting rules

No CLI. Applies the repository's Chinese-metadata formatting contract to both
title and abstract before any Zotero write: super/subscripts are flattened
(`MnO2`, `Zn2+`, `mAh g^-1`) and exactly one half-width space is inserted at
every CJK<->Latin boundary, never inside a unit or formula. The character
classes come from `zotero_cli.core.notes.text`, so tool-written metadata and
rendered AI notes cannot drift apart. Import `zh_text` instead of
re-implementing the rules in a new script.

## Safety notes

- Writes go through the Zotero Web API (`ZoteroWriter`); local SQLite is opened
  read-only. Never write `zotero.sqlite` directly.
- `--apply` is always opt-in except for `zotero_inbox.py`, which writes by
  default and takes `--dry-run`; preview before the first real run.
- Every write is re-read from the Web API and compared field by field; an
  uncertain response is surfaced rather than blindly retried.
- Checkpoints are written atomically with unique temp names and live under the
  git-ignored `.zot/state/paper2zotero/`. Keep them to resume; do not commit
  library data or credentials.
- Zotero Desktop must sync before `zot read`/`zot list` show a Web API write.

## Tests

```bash
uv run pytest -q tools/paper2zotero
uv run pytest -q tools/paper2zotero/test_translation_safety.py
uv run pytest -q tools/paper2zotero/test_batch_translate_metadata.py
uv run pytest -q -rf          # full repository suite
```

The tests are offline: pyzotero/HTTP, Hermes subprocesses, and model endpoints
are mocked, and the repository `conftest.py` rejects unmocked socket
connections. They are collected by the default `testpaths`, `mypy` type-checks
the modules (test files excluded), and formatting is done per touched file with
`uv run ruff format <paths>`. The full suite is expected fully green (0 failed,
0 skipped); do not add skip markers or mute a failure.
