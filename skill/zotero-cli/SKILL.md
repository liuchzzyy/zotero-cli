---
name: zotero-cli
description: Use the local `zot` command to search, read, export, and manage the user's Zotero library, inspect PDFs, or operate Zotero workspaces and RAG. Use for Zotero-backed literature tasks and this zotero-cli repository; do not use for generic literature research that does not involve Zotero.
---

# zotero-cli

Use the `zot` command from `/Users/liuchzzyy/python-code/zotero-cli`. Run it as `uv run zot ...` inside the repository unless a working global `zot` executable is already available.

## Invariants

- Reads use the local Zotero SQLite database in read-only mode.
- Writes use the Zotero Web API. Never write directly to `zotero.sqlite`.
- Use `--json` for programmatic results. Empty results and errors use the same JSON envelope contract.
- Treat `zot schema [COMMAND ...]` as the authoritative command and safety interface.
- A real write, delete, collection change, note/tag update, or metadata update requires the user's request. Use `--dry-run` when intent or scope is not concrete.
- Use an idempotency key when a write may be retried after an uncertain network outcome.
- For duplicate cleanup, prefer DOI matches. Do not remove title-similar items when their non-empty DOIs conflict.

## Current MacBook configuration

- Repository: `/Users/liuchzzyy/python-code/zotero-cli`
- Config: `.zot/config.toml`
- Zotero data: `/Users/liuchzzyy/Zotero`
- Python package: `zotero_cli`
- CLI command: `zot`

Do not print API keys or service tokens. The actual config and `.zot/state/` are Git-ignored.

## Route by intent

| Intent | Command |
| --- | --- |
| Search metadata | `zot --json search "query"` |
| List or read items | `zot --json list`; `zot --json read ITEMKEY` |
| Recent items or statistics | `zot --json recent`; `zot --json stats` |
| Export citation data | `zot export ITEMKEY --format bibtex` |
| Format a citation | `zot cite ITEMKEY --style apa` |
| Read PDF text | `zot --json pdf ITEMKEY` |
| Inspect PDF structure | `zot --json pdf ITEMKEY --outline` |
| Read one PDF section | `zot --json pdf ITEMKEY --section N` |
| Find duplicates | `zot --json duplicates --by doi` |
| Inspect trash | `zot --json trash list` |
| Preview a write | `zot ... --dry-run` |
| Inspect command contract | `zot schema COMMAND ...` |

## Writes

```bash
zot add --doi "10.1038/example" --dry-run
zot update ITEMKEY --field volume=42 --dry-run
zot attach ITEMKEY --file paper.pdf --dry-run
zot note ITEMKEY --add "Key finding" --dry-run
zot tag ITEMKEY --add important --dry-run
zot delete ITEMKEY --dry-run
```

After the user authorizes the actual mutation, remove `--dry-run`. For retryable single-item mutations, add `--idempotency-key UNIQUE_KEY` where supported. Zotero Web API changes require Zotero Desktop sync before they appear in the local SQLite database.

## Workspaces and RAG

Basic workspace operations are local:

```bash
zot workspace new topic --description "Scope"
zot workspace add topic ITEMKEY1 ITEMKEY2
zot workspace show topic
zot workspace search "query" --workspace topic
```

Index and query only when the user needs full-text retrieval:

```bash
zot workspace index topic --extractor pymupdf --no-embed
zot workspace embed topic
zot --json workspace query "question" --workspace topic
```

Indexing may extract large PDFs or call configured external services. Reuse an existing complete index when possible. Check `zot workspace show NAME` and the index state before forcing a rebuild.

## Troubleshooting

- Run `zot config show` to verify the local database path without exposing the full API key.
- Exit 2 means authentication failure; exit 3 means invalid input or missing configuration; exit 4 means not found; exit 5 means network/rate-limit failure; exit 6 indicates a conflict such as detected duplicates.
- When a command surface is unclear, run `zot schema COMMAND ...` instead of guessing flags.
