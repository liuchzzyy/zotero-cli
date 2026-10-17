# Documentation maintenance for this repository

Consult when asked to update `README.md`, `AGENTS.md`, or the bundled skill in
`/Users/liuchzzyy/CodeSpace/zotero-cli`.

## Shape of the docs

| File | Language | Audience |
| --- | --- | --- |
| `README.md` | Chinese | the user; installing, configuring, everyday commands |
| `AGENTS.md` | English | coding agents; architecture, invariants, validation order |
| `skill/zotero-cli/SKILL.md` | English | the bundled agent skill, must match real CLI behavior |

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
   `src/zotero_cli/cli.py` defines `_READ_COMMANDS`, `_WRITE_COMMANDS`,
   `_DESTRUCTIVE_COMMANDS`. `collection` is read-tier — listing it among write
   commands is the recurring documentation error, and the tiers drive both
   `zot --help` grouping and `zot schema`.
5. **Diff only the doc files when done, and state what you did not run.**  
   A Markdown-only change needs no full suite; say "not run, because no code
   path changed" rather than implying it passed. If claiming verification, name
   the actual command.

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
- **Directory migrations leave stale paths everywhere.** The `tools/*.zsh` →
  `tools/paperrag/` and `tools/templates/` → `tools/ainote/templates/` moves
  left old paths in docs, tests, and skill files. After any such move, grep the
  old path across the repo and the skill before declaring the update finished.
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
