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
| `tools/README.md` | English | overview of the non-CLI batch helpers and their tests |
| `tools/paper2zotero/README.md` | English | per-script config, safety and test commands for the metadata tools |

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
   and nested mutating subcommands have tuple entries
   (`("collection", "delete")`, `("trash", "restore")`). `collection` is
   read-tier — listing it among write commands is the recurring documentation
   error, and the tiers drive both `zot --help` grouping
   (`cli/tiered_group.py`) and `zot schema` (`safety_tier_for_path`). Register
   commands in `cli/registry.py` in observable order: help/schema order is part
   of the agent contract.
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
- **A CLI surface change fans out to four targets:** `zot schema`-visible
  behavior plus its contract tests, `AGENTS.md`, the repo-bundled
  `skill/zotero-cli/SKILL.md`, and the profile copy at
  `~/.hermes/profiles/scientist/skills/zotero-cli/SKILL.md`. Update them
  together or the next agent reads a stale contract.
- **Renames and package splits leave stale paths everywhere.** Moving a module,
  renaming a command module, splitting a flat module into a package, or
  dropping a compatibility shim leaves old import paths in docs, tests, and
  skill files. After any such change, grep the old name across `src`, `tests`,
  `tools`, `AGENTS.md`, `README.md` and `skill/` before declaring the update
  finished.
- **Standalone tool modules need importable snake_case names.** The paperrag
  inventory helper and the paper2zotero batch metadata translator were renamed
  to `workspace_inventory.py` and `batch_translate_metadata.py`: a hyphen is
  not importable (tests had to load the file by path), and the old batch name
  described only half of what the script does. Keep `tools/**` module names
  snake_case and update the script references (`*.zsh`, tests, docs) together.
- **There is no skip or failure baseline.** The suite is expected fully green
  (0 failed, 0 skipped). A docs update must not describe skipped tests,
  platform-conditional skips, or known failures as normal; if a test is
  skipped, that is a regression to fix in code, not something to document.
- **`core.fileMode=false` used to hide lost executable bits.** Fixed by
  normalising the working tree (644 files, 755 dirs and the three `.zsh`
  entrypoints) and enabling `core.fileMode=true`, so `chmod +x` on a tracked
  script is now recorded and survives a clone. Keep it that way: new scripts go
  to 755, ordinary files stay 644, and `git ls-tree HEAD tools/paperrag/` must
  show `100755` on the wrappers.
- **Name the risk out loud.** When the doc pass exposes a loose credential or a
  destructive-looking behavior outside the requested scope, name the file and
  the exact change and ask. Do not silently fix it, and do not silently ignore
  it.
- **Do not restate the codebase map.** `AGENTS.md` carries the architecture;
  the doc pass records the behavior change and the invariant it protects (e.g.
  the WAL-aware read path), not a second copy of the module list.
