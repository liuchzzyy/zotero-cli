---
name: zotero-cli-agent
description: "Use when user mentions papers, references, citations, Zotero, literature, bibliography, workspaces, or needs to search/read/export documents in zotero. Uses zot CLI for all operations including workspace-based RAG."
---

# Zotero CLI Agents Skill

**`zot`** — all-in-one Zotero CLI: CRUD, search, PDF, export, workspace-based RAG. Local SQLite for reads, Zotero API for writes.

**Always use `--json` when processing results programmatically.** (Auto-enabled when stdout is not a TTY; can be placed before or after the subcommand.)

For exhaustive flags / types / safety tier of any command, run `zot schema <cmd>` (e.g. `zot schema add`) — that's the canonical machine-readable surface.

## Workflow Wrapper Rules

For repository workflows, prefer the maintained PowerShell wrappers and the canonical runbook in `tools/Instructions.md` instead of hand-splicing long commands.

- Run wrappers from `F:\ChengL1u\10_资源库\代码\zotero-cli-agent`.
- `tools\*.ps1` wrappers default to opening a new PowerShell window; keep that window visible.
- Primary progress evidence is the wrapper output plus `run.log`, `progress.jsonl`, and workflow summary files in the run directory.
- Use `-RunInCurrentWindow` only for debugging, CI, or when a parent wrapper already opened the visible window.
- Do not treat optional process/file checks as the primary progress source.
- Keep generated workflow artifacts under `log\...`; do not scatter temporary files in the repo root, `tools\`, `tmp\`, or ad-hoc `.workspace\...` directories.

## Routing Rules

| User Intent | Command | Why |
|-------------|---------|-----|
| Search by title/author/tag | `zot --json search "transformer"` | Fast metadata match |
| Read/view a paper | `zot --json read KEY` | Direct lookup |
| Export citation | `zot export KEY` | Local data |
| Formatted citation to clipboard | `zot cite KEY --style apa` | APA/Nature/Vancouver |
| Batch import DOIs/URLs | `zot add --from-file file.txt` | One per line |
| Add/delete/tag/note | `zot ...` | All write ops |
| Update item metadata | `zot update KEY --title/--field` | Web API write |
| Upload attachment | `zot attach KEY --file paper.pdf` | Web API write |
| Check preprint pub status | `zot update-status --limit 20` | Semantic Scholar API |
| Find duplicates | `zot --json duplicates` | Local SQLite |
| Recently added items | `zot --json recent --days 7` | Local SQLite |
| Trash management | `zot --json trash list` | Local SQLite |
| PDF full text extraction | `zot --json pdf KEY` | Local file access |
| PDF outline extraction | `zot --json pdf --outline KEY` | Local file access + section parsing |
| PDF section extraction | `zot --json pdf --section SECID KEY` | Local file access + section parsing |
| Library stats | `zot --json stats` | Local aggregation |
| Open PDF/URL | `zot open KEY` or `zot open --url KEY` | System open |
| Group library access | `zot --library group:123 search "query"` | All commands |
| Organize papers by topic | `zot workspace new llm-safety` | Local workspace, no API needed |
| Bulk import to workspace | `zot workspace import name --collection/--tag/--search` | From collection, tag, or search |
| Search within workspace | `zot workspace search "query" --workspace name` | Fast metadata match |
| Export workspace for AI | `zot workspace export name` | Markdown/JSON/BibTeX |
| Deep content search (RAG) | `zot workspace query "question" --workspace name` | FTS5 keyword + Qdrant semantic (Gitee) |

**Rule of thumb**: Use `zot search` for quick metadata lookups. Use `zot workspace query` for deep content search over a curated set of papers (indexes metadata + PDF fulltext).

---

## zot — Zotero CLI (Core Tool)

### Search & Browse

```bash
zot --json search "transformer attention"
zot --json search "BERT" --collection "NLP"
zot --json list --collection "Machine Learning" --limit 10
zot --json read ITEMKEY
zot --json relate ITEMKEY
```

### Notes & Tags

```bash
zot --json note ITEMKEY
zot note ITEMKEY --add "Key finding: ..."
zot --json tag ITEMKEY
zot tag ITEMKEY --add "important"
zot tag ITEMKEY --remove "to-read"
```

### Citation Export

```bash
zot export ITEMKEY                    # BibTeX
zot export ITEMKEY --format csl-json  # CSL-JSON
zot export ITEMKEY --format ris       # RIS
zot export ITEMKEY --format json      # Raw JSON

# Formatted citation (copies to clipboard)
zot cite ITEMKEY                      # APA (default)
zot cite ITEMKEY --style nature       # Nature
zot cite ITEMKEY --style vancouver    # Vancouver
```

### Item Management (Write Ops)

```bash
zot add --doi "10.1038/s41586-023-06139-9"
zot add --url "https://arxiv.org/abs/2301.00001"
zot add --from-file dois.txt              # Batch import (one DOI/URL per line)
zot add --pdf paper.pdf                   # Add from local PDF (auto-extract DOI)
zot --no-interaction delete ITEMKEY
zot update ITEMKEY --title "New Title"
zot update ITEMKEY --field volume=42 --field pages=1-10
zot attach ITEMKEY --file supplement.pdf
```

Batch metadata write-back format:

```json
{"key":"ABC123","fields":{"title":"Clean title","abstractNote":"Clean abstract"}}
```

**Agent-safety flags on every mutating command:**

```bash
# Preview without writing — confirms intent before commit, no Zotero API call
zot add --doi "10.1038/..." --dry-run
zot delete ITEMKEY --dry-run
zot update ITEMKEY --field volume=42 --dry-run

# Idempotency — replay the same write safely after a network blip
zot add --doi "10.1038/..." --idempotency-key abc-123
zot update ITEMKEY --title "X" --idempotency-key abc-124
zot attach ITEMKEY --file x.pdf --idempotency-key abc-125
zot delete ITEMKEY --yes --idempotency-key abc-126
```

> Use `--dry-run` before any unfamiliar write, and pass a unique `--idempotency-key` whenever you might retry on failure; inspect the live agent-native contract with `zot schema`.

### Collections

```bash
zot --json collection list
zot --json collection items COLLECTIONKEY
zot collection create "New Project"
zot collection move ITEMKEY COLLECTIONKEY                 # add target membership
zot collection move ITEMKEY DESTKEY --from SOURCEKEY      # true move from source to destination
zot collection rename COLLECTIONKEY "New Name"
zot collection delete COLLECTIONKEY
```

### Duplicates, Recent & Trash

```bash
zot --json duplicates                # Find duplicates (DOI + title matching)
zot --json duplicates --by title     # Title-only matching
zot --json recent --days 7           # Recently added items
zot --json recent --modified         # Recently modified items
zot --json trash list                # View trashed items
zot trash restore ITEMKEY            # Restore from trash
```

### PDF & Summarization

```bash
zot --json pdf ITEMKEY
zot --json pdf --outline ITEMKEY        # PDF outline (headings)
zot --json pdf --section SECID ITEMKEY  # Extract specific section by ID
zot pdf ITEMKEY --annotations           # Extract PDF annotations
zot --json summarize ITEMKEY
zot summarize-all
```

### Utilities

```bash
zot --json stats                     # Library statistics
zot open ITEMKEY                     # Open PDF in system viewer
zot open --url ITEMKEY               # Open URL/DOI in browser
```

### Group Library

```bash
zot --library group:12345 search "query"    # Search in group library
zot --library group:12345 list              # List group library items
```

### Workspaces (Topic-Based Paper Organization + RAG)

Workspaces are local collections of paper references for organizing research by topic. Each workspace stores item keys in `.workspace/<name>/workspace.toml` inside the repository — no Zotero API needed.

```bash
# Create and manage workspaces
zot workspace new llm-safety --description "LLM alignment and safety papers"
zot workspace add llm-safety KEY1 KEY2 KEY3
zot workspace remove llm-safety KEY1
zot workspace list                         # List all workspaces
zot --json workspace list                  # JSON output
zot workspace show llm-safety              # Show items with full metadata
zot workspace delete llm-safety --yes

# Bulk import from collection, tag, or search
zot workspace import llm-safety --collection "Alignment"
zot workspace import llm-safety --tag "safety"
zot workspace import llm-safety --search "RLHF"

# Search within workspace (metadata substring match)
zot workspace search "reward" --workspace llm-safety
zot --json workspace search "attention" --workspace llm-safety

# Export for AI consumption
zot workspace export llm-safety                       # Markdown (default)
zot workspace export llm-safety --format json         # JSON
zot workspace export llm-safety --format bibtex       # BibTeX

# Build RAG index (SQLite FTS5 + Qdrant vectors over metadata + PDF text)
zot workspace index llm-safety                 # Incremental index
zot workspace index llm-safety --force         # Full rebuild
zot workspace reindex llm-safety               # Force full rebuild (alias)
zot workspace index llm-safety --no-embed      # FTS5 only; backfill embeddings later
zot workspace embed llm-safety                 # Backfill Gitee embeddings into Qdrant
zot workspace embed llm-safety --limit 100

# Query workspace with natural language
zot workspace query "reward hacking" --workspace llm-safety
zot workspace query "RLHF methods" --workspace llm-safety --top-k 10
zot --json workspace query "attention" --workspace llm-safety

# Retrieval modes (auto selects hybrid if vectors available)
zot workspace query "query" --workspace name --mode bm25      # FTS5 keyword only
zot workspace query "query" --workspace name --mode semantic  # Qdrant vector only
zot workspace query "query" --workspace name --mode hybrid    # FTS5 + vector weighted RRF
zot workspace query "query" --workspace name --rerank         # Rerank fused candidates (Gitee)
zot workspace query "query" --workspace name --pdf-kind main  # Only main-paper PDF chunks
```

The format of chunks is:

```json
[title > heading] chunk text...
```

Full example:

```
{
    "rank": 1,
    "score": 0.0154,
    "item_key": "B6TZ6TQX",
    "source": "pdf",
    "content": "[现代电路理论与设计 > 5.2 跨导电容滤波器的分析与设计] 跨导放大器是一种输入信号是电压、输出信号是电流的放大器。由跨导放大器组成的集成运算放大器称为跨导运算放大器（Operational Transconductance Amplifier, OTA）。由跨导运算放大器和电容构成的滤波器称为 跨导电容滤波器。跨导电容滤波器以有源RC滤波器为基础，以跨导运算放大器作为有源器件，利用跨导运算放大器的电导特性，将跨导运算放大器作为电阻元件使用，以实现有源滤波器的全集成实现。由于跨导运算放大器的跨导值 $g_{\\mathrm{m}}$ 可以通过改变跨导运算放大器的偏置电流很容易 地加以改变，所以跨导电容滤波器可以比较容易地实现对滤波器频率特性的调整。与MOSFET-C滤波器相比，跨导电容滤波器最适宜于高速应用。另外，它可以应用于开环结构，所以不需要考虑其稳定性，而这两个特点正是普通运算放大器所不具有的。\n\n跨导电容滤波器的缺点是：由于跨导运算放大器往往工作在开环状态，为了保持电路\n\n的线性，所加的输入信号必须非常小，因此，分布电容对电路工作频率的影响比较大。另外，由于跨导电容滤波器对跨导运"
  }
```

> Never build workspace RAG index with `--force` unless you know what you're doing. It takes time to build the full index from scratch.
> Before building the index, ask the user if they want to build right now or later, and explain that it may take a while. Or Let the user build the index in their own time, and just show a warning if they try to query before the index is built.

### Global Flags

| Flag | Purpose |
|------|---------|
| `--json` | JSON output (ALWAYS use for programmatic processing) |
| `--limit N` | Limit results (default: 50) |
| `--detail minimal` | Only key/title/authors/year — saves tokens |
| `--detail full` | Include extra fields |
| `--no-interaction` | Suppress prompts (for automation) |
| `--profile NAME` | Use a specific config profile |
| `--verbose` | Verbose/debug output |

---

## Workflow Patterns

### Pattern 1: Find and Read a document(papers,manuals,books)

```bash
# Step 1: Search
zot --json search "single cell RNA sequencing"

# Step 2: Read metadata details
zot --json read K853PGUG

# Step 3: Full PDF text if needed or read section selectively
zot --json pdf K853PGUG               # Get full text extraction
zot --json pdf --outline K853PGUG     # Get section headings and secid
zot --json pdf --section 10 K853PGUG  # Extract section with secid 10 (e.g. Results)
```

> Before fetching the full PDF text, use `wc -m` on the command line to check the character count of the PDF output. If it's very long (>20000), use the `--outline` option to get section headings and IDs, then selectively extract only the relevant sections with `--section` to save tokens.

### Pattern 2: Deep Content Search via Workspace RAG

```bash
# Step 1: Create workspace and add papers
zot workspace new drug-resistance --description "Cancer drug resistance mechanisms"
zot --json search "drug resistance cancer" --limit 20
zot workspace add drug-resistance KEY1 KEY2 KEY3

# Step 2: Build index (metadata + PDF fulltext)
zot workspace index drug-resistance

# Step 3: Query with natural language
zot --json workspace query "mechanisms of acquired resistance" --workspace drug-resistance --top-k 5

# Step 4: If want to read more context of some chunks (for incomplete chunks), get the item key and section from the results, then fetch the full PDF text and extract that section
zot --json pdf --outline ITEMKEY        # Recognize section headings and secid
zot --json pdf --section SECID ITEMKEY  # Extract specific section by ID for more context
```


### Pattern 3: AI-Powered Library Reorganization

```bash
# Step 1: Export all abstracts
zot --json summarize-all > abstracts.json

# Step 2: AI analyzes and generates classification plan
# Step 3: Create collections and move items
zot collection create "Category A"
zot collection move ITEMKEY COLLECTIONKEY
```

### Pattern 4: Workspace RAG 

```bash
# Step 1: Create a topic workspace
zot workspace new protein-folding --description "Protein structure prediction papers"

# Step 2: Add relevant papers
zot --json search "protein folding" --limit 20
zot workspace add protein-folding KEY1 KEY2 KEY3 KEY4

# Step 3: Build RAG index
zot workspace index protein-folding

# Step 4: Query and feed results to your AI agent
zot --json workspace query "AlphaFold architecture" --workspace protein-folding --top-k 5
# Paste JSON output into the agent conversation as context
```

## Important Notes

- **`zot` read operations** work offline with zero config
- **`zot` write operations** need API credentials via `zot config init`
- **`zot update-status`** uses Semantic Scholar API; set `semantic_scholar_api_key` in `.zot/config.toml` for faster rate limits
- **PDF cache** — `zot` caches PDF extractions automatically
- **Item keys** are 8-character alphanumeric strings like `K853PGUG`
- **Group libraries** — use `--library group:<id>` with any command
- **Workspaces** — pure local TOML files, no API needed for basic operations; `workspace index` reads PDFs from Zotero storage
- **Workspace RAG** — PDF text via MinerU, term retrieval via SQLite FTS5, vectors via Qdrant local, embeddings/rerank via Gitee AI (`[embedding]`, `[rerank]` in `.zot/config.toml`)
- **Vector store** — local Qdrant under `.workspace/_qdrant` (`[vector_store]`), no server or account needed

