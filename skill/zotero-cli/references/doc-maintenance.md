# Documentation maintenance for this repository

Consult when asked to update `README.md`, `AGENTS.md`, `tests/README.md`, the
tools READMEs, or the bundled skill in `/Users/liuchzzyy/CodeSpace/zotero-cli`.

## Shape of the docs

| File | Language | Audience |
| --- | --- | --- |
| `README.md` | Chinese | the user; installing, configuring, everyday commands |
| `AGENTS.md` | English | coding agents; architecture, invariants, validation order |
| `skill/zotero-cli/SKILL.md` | English | the bundled agent skill, must match real CLI behavior |
| `tests/README.md` | English | test layout, markers, and the green-suite expectation |

No `docs/` tree or MkDocs config is checked in. A missing docs path is not
evidence of the wrong repository — do not "restore" a docs tree.

## Procedure

1. **Resolve the real path first.**  
   Docs live at `/Users/liuchzzyy/CodeSpace/zotero-cli/README.md` and
   `.../AGENTS.md`. An `@file:zotero-cli/README.md` attachment hint resolves
   relative to the shell CWD, so the harness can report "file not found" for a
   file that exists. Locate the repo, then read the doc; never conclude the doc
   is missing from the attachment warning alone.
2. **Re-derive the documented surface from the running CLI.**  
   - `uv run zot --help` → tier grouping plus every subcommand
   - `uv run zot COMMAND --help` → the options you intend to document, one call per command
   - `grep -rho 'ZOT_[A-Z_]*' src/zotero_cli | sort -u` → real env-var list
   - `grep -n '^\[' .zot/config.example.toml` → real config sections
   Existing prose drifts; the Click tree does not.
3. **Read the working-tree diff before writing.**  
   A doc pass usually accompanies an uncommitted refactor. `git diff --stat`
   then per-file `git diff` shows the behavior the docs must now describe;
   `git log` only describes what already shipped.
4. **Check a command's safety bucket at the source.**  
   `src/zotero_cli/cli/safety.py` owns `SAFETY_TIERS`;
   `READ_COMMANDS`/`WRITE_COMMANDS`/`DESTRUCTIVE_COMMANDS` are derived from it,
   and nested mutating subcommands have tuple entries. Current nesting:
   `config init`/`config profile set` = write, `config cache clear` =
   destructive; `pdf_rag new/add/remove/import/index/reindex/embed` = write,
   `pdf_rag delete` = destructive; `collection create/move/rename/reorganize`
   = write and `collection delete`/`delete` = destructive; `trash restore` =
   write. `collection` is read-tier at the top level — listing it among write
   commands is the recurring documentation error, and the tiers drive both
   `zot --help` grouping (`cli/tiered_group.py`) and `zot schema`
   (`safety_tier_for_path`). Register commands in `cli/registry.py` in
   observable order: help/schema order is part of the agent contract.
5. **Diff only the doc files when done, and state what you did not run.**  
   A Markdown-only change does not itself change code, but the repository gate
   is `uv run pytest -q -rf` with 0 failed and 0 skipped. Name the commands you
   actually ran; do not imply a green run you did not observe.

## Pitfalls

- **`.zot/config.example.toml` is tracked by Git.** It gets edited in place
  while tuning a local proxy, so a real `api_key`, `mineru_token`, or an
  absolute home path can land staged for commit. Restore empty credentials and
  the placeholders `/Users/your-name/Zotero` and
  `.../Profiles/<profile>/prefs.js`, and surface it explicitly — neither
  shipping it nor silently reverting it is acceptable, because the user may be
  relying on that edit locally.
- **A CLI surface change fans out to five targets:** `zot schema`-visible
  behavior plus its contract tests, `AGENTS.md`, `README.md`, the repo-bundled
  `skill/zotero-cli/SKILL.md`, and the profile copy at
  `~/.hermes/profiles/scientist/skills/zotero-cli/SKILL.md`. Update them
  together or the next agent reads a stale contract.
- **Renames and package splits leave stale paths everywhere.** Moving a module,
  renaming a command module, splitting a flat module into a package, or
  dropping a compatibility shim leaves old import paths in docs, tests, and
  skill files. After any such change, grep the old name across `src`, `tests`,
  `tools`, `AGENTS.md`, `README.md` and `skill/` before declaring the update
  finished.
- **There is no skip or failure baseline.** The suite is expected fully green
  (0 failed, 0 skipped). A docs update must not describe skipped tests,
  platform-conditional skips, or known failures as normal; if a test is
  skipped, that is a regression to fix in code, not something to document.
- **CI exists.** `.github/workflows/ci.yml` runs lint, format check, `ty check`, the
  full test suite, and wheel/sdist build on Python 3.12 only; a weekly/manual
  best-effort `pip-audit` job is network-dependent and non-blocking. Do not
  document "no CI" or a hard dependency-audit gate. The project supports only
  CPython 3.12 (`>=3.12,<3.13`) with upgraded dependencies (`pyzotero>=1.16,<2`);
  never write 3.10/3.13 matrix or downgrade claims.
- **Delete is permanent; trash restore is different.** `zot zotero delete` is a
  permanent Web API deletion, never a move to the trash; `zot zotero trash list` reads
  the local synced `deletedItems`, and `zot zotero trash restore` restores an
  already-trashed item through a raw `PATCH {"deleted": 0}`. There is no
  `trash empty` command. Docs must say permanent for delete and must not imply
  trash/restore as its consequence or promise immediate local visibility after
  remote writes.
- **Envelope top level is closed.** Only `ok`, `data`/`error`, `meta`, optional
  `dry_run` and optional `next` are official; `ok` is tri-state (true/false
  `"partial"`). `next` is validated and only on success/partial envelopes.
- **Ruff 0.17 formats Markdown code fences too.** `uv run ruff format --check .`
  includes Python fences in `skill/**/references/*.md`; after editing a reference
  run `uv run ruff format <that .md>` (or the CI format gate fails for a
  docs-only change).
- **Name the risk out loud.** When the doc pass exposes a loose credential or a
  destructive-looking behavior outside the requested scope, name the file and
  the exact change and ask. Do not silently fix it, and do not silently ignore
  it.
- **Do not restate the codebase map.** `AGENTS.md` carries the architecture;
  the doc pass records the behavior change and the invariant it protects (e.g.
  the WAL-aware read path), not a second copy of the module list.
