---
name: zotero-cli
description: Use the local `zot` command to search, read, export, and manage the user's Zotero library, inspect PDFs, or operate Zotero workspaces and RAG. Use for Zotero-backed literature tasks and this zotero-cli repository; do not use for generic literature research that does not involve Zotero.
---

# zotero-cli

Use the `zot` command from `/Users/liuchzzyy/CodeSpace/zotero-cli`. Run it as `uv run zot ...` inside the repository unless a working global `zot` executable is already available.

## Invariants

- Reads use the local Zotero SQLite database in read-only mode.
- Writes use the Zotero Web API. Never write directly to `zotero.sqlite`.
- Use `--json` for programmatic results. Empty results and errors use the same JSON envelope contract.
- Treat `zot schema [COMMAND ...]` as the authoritative command and safety interface.
- A real write, delete, collection change, note/tag update, or metadata update requires the user's request. Use `--dry-run` when intent or scope is not concrete.
- Use an idempotency key when a write may be retried after an uncertain network outcome, where supported. Do not equate safe retry of one attempted write with deduplicating against existing library records.
- For duplicate cleanup, prefer DOI matches. Do not remove title-similar items when their non-empty DOIs conflict. For intake, follow the user's explicit deduplication policy: if they say import each DOI directly, do not silently search or skip existing library records.

## Current MacBook configuration

- Repository: `/Users/liuchzzyy/CodeSpace/zotero-cli`
- Config: `.zot/config.toml`
- Zotero data: `/Users/liuchzzyy/Zotero`
- Python package: `zotero_cli`
- CLI command: `zot`

Do not print API keys or service tokens. The actual config and `.zot/state/` are Git-ignored.

`[ai_notes]` accepts `ZOT_AI_*` env overrides (`ZOT_AI_API_KEY`, `ZOT_AI_BASE_URL`, `ZOT_AI_MODEL`, `ZOT_AI_FALLBACK_MODELS` as a comma list, `ZOT_AI_REASONING_EFFORT`, `ZOT_AI_TEMPERATURE`, `ZOT_AI_MAX_TOKENS`, `ZOT_AI_MAX_EXTRACTED_CHARS`) with `env > file > defaults` precedence. Use them to point `ai_analyze` at another endpoint for one run instead of editing the tracked config; pass `apply_env_overrides=False` when a test needs the raw file value.

## Route by intent

| Intent | Command |
| --- | --- |
| Search metadata | `zot --json search "query"` |
| List or read items | `zot --json list`; `zot --json read ITEMKEY` |
| Recent items or statistics | `zot --json recent`; `zot --json stats` |
| Export citation data | `zot export ITEMKEY --format bibtex` |
| Format a citation | `zot cite ITEMKEY --style apa` |
| Enumerate PDFs under one parent item | `zot --json pdf ITEMKEY --list-attachments` (keys, names, availability; no cloud parse) |
| Read one selected PDF | `zot --json pdf ITEMKEY --attachment ATTACHMENTKEY` (MinerU cloud or cache) |
| Read MinerU structured JSON | `zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --structured` |
| Inspect PDF structure | `zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --outline` |
| Read one PDF section | `zot --json pdf ITEMKEY --attachment ATTACHMENTKEY --section N` |
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
zot ai_analyze ITEMKEY --dry-run
```

After the user authorizes the actual mutation, remove `--dry-run`. For retryable single-item mutations, add `--idempotency-key UNIQUE_KEY` where supported. Zotero Web API changes require Zotero Desktop sync before they appear in the local SQLite database.

`zot ai_analyze ITEMKEY --dry-run` does not write to Zotero and therefore does not require Zotero Web API write credentials. A non-dry-run analysis still requires a library ID and an API key with write access.

## AI note analysis (ai_analyze)

The `zot ai_analyze` pipeline reads an item's local PDF, parses it via MinerU, classifies the document into a template (`research_article`, `review_article`, `book`), generates a structured JSON sections payload via LLM, renders it to an inline-styled HTML note in Zotero, tags the item `ai/noted`, and generates a `short-note: ...` summary in Extra with tag `ai/keywords`.

Prompt templates live under `tools/ainote/templates/` in a checkout, and the same files ship inside the wheel as `zotero_cli/templates/`: `research-article.md`, `review-article.md`, `book.md`, `classify-item.md`, `short-note.md`. `note_templates.py` inspects `tools/ainote/templates`, `tools/ainote`, `tools/templates`, then the packaged directory, so `uv tool install` / pipx installs resolve prompts too.

```bash
zot ai_analyze ITEMKEY --dry-run          # Preview classification and formatted prompt; no LLM call, no writes
zot ai_analyze ITEMKEY                    # Full note generation and short-note writeback
zot ai_analyze ITEMKEY --force            # Re-analyze even if already tagged ai/noted
zot ai_analyze ITEMKEY --no-short-note    # Generate note only without Extra short-note or keyword tags
zot ai_analyze ITEMKEY --no-tag           # Write the note but do not apply the ai/noted tag
zot ai_analyze ITEMKEY --short-note-only  # Backfill Extra short-note from existing AI note without re-analyzing
zot ai_analyze ITEMKEY --progress-lines   # One progress record per line, for logs rather than a TTY
```

### Pre-flight, then verify through the Web API

`--dry-run` is the cheap pre-flight: it reports `paper_type`, `template`,
`pdf_count` and the extracted `chars`, so a misclassified document or a
truncated PDF parse surfaces before spending a MinerU parse plus an LLM run.
Read the returned `paper_type`; do not infer the article type from the title.

`ai_analyze` has no `--attachment` option — it consumes *every* available PDF of
the item, so `pdf_count` is something to audit, not merely read. Triage the
attachments first: an empty record (`available: false`, empty storage dir) is
noise that should be deleted from the library, while a supplementary file that
shares the main article's filename silently joins the prompt. See
`references/ai-note-verification.md` for the triage procedure, the MinerU cache
layout, and the number spot-check recipe.

A finished run reports `note_key`, `sections` and `short_note`, but those are
self-reported. Re-read the item from the Web API, because `ai_analyze` writes
there and the local SQLite view stays stale until Zotero Desktop syncs — right
after a successful run `zot --json read ITEMKEY` still prints `tags: []` and
`notes: []`, which looks exactly like a silent no-op. Confirm through pyzotero:

```python
from zotero_cli.config import load_config
from pyzotero import zotero

cfg = load_config()
# AppConfig exposes library_id and api_key only. It has no library_type
# attribute (AttributeError), so pass the library type explicitly.
z = zotero.Zotero(str(cfg.library_id), "user", cfg.api_key)
item = z.item(ITEMKEY)["data"]
print([t["tag"] for t in item["tags"]], repr(item["extra"]))
notes = [k for k in z.children(ITEMKEY) if k["data"]["itemType"] == "note"]
```

Check the note body, not its length: strip the inline-styled HTML, count the
`h1`/`h2` headings, and treat a heading immediately followed by another heading
as an empty section — excluding the leading title `h1` followed by the first
`h2`, which is normal layout. Pre-existing tags on the item are not evidence of
this run; report only what the API shows for the note key, tag set,
`short-note:` line and section count, and tell the user to sync Zotero Desktop
before expecting to see any of it there.

### Output formatting (no super/subscripts, CJK/Latin spacing)

Notes and short-notes are Chinese prose with embedded English terms, formulas and
units, and the user requires two things of every generated note. **No
super/subscripts anywhere**: flatten formulas, charges and units to `MnO2`,
`Zn2+`, `Ti3C2Tx`, `mAh g^-1`, `Wh kg^-1`, keeping a caret only on a negative
exponent so `g^-1` cannot read as `g-1`. And **one half-width space at every
CJK↔Latin boundary** (`层状 δ-MnO2 与 5 mg cm^-2 负载`), never inside a unit or
chemical formula.

Both are enforced in code, restated in the prompt templates, and covered by
tests — a prompt-only change is not a fix, because prompt wording is
probabilistic. `note_text.prepare()` is the whole pipeline
(`pad_cjk_latin(plain_super_subscripts(text))`) and the renderer applies it to
every text/heading/list/citation/table cell plus the validated short-note; it
superseded the standalone `text_spacing` module, so look there first when a
symbol from an older note is gone. When the output looks wrong, fix the
normaliser and the templates, then re-run; do not hand-edit the note in Zotero. Pin structural conventions in the templates too, not only text: top-level sections must be `level: 2`, because the renderer draws that level as the coloured section banner and a model emitting `level: 3` silently drops it while the note still looks intact.
Details — character classes, the flattening table, why CJK punctuation is
excluded, the length-budget-before-padding rule, and how to update tests that
encode the old format — are in `references/note-text-formatting.md`.

### Invariants & pitfalls for AI note workflows

- **Never write empty sections:** If the model returns an empty sections list (`{"sections": []}`) after retries, fail with `NoteAnalysisError` instead of writing an empty `<h1>` note and tagging `ai/noted`. An empty note falsely marks the item as analyzed.
- **Defensive renderer extraction:** The renderer must tolerate LLM structural variations without raising unhandled exceptions. In `_bullet_list`, handle `items` elements that are bare strings as well as dicts (`item.get("text", "") if isinstance(item, dict) else item`). In `citations`, accept strings as well as dicts. Safely coerce heading `level` with a fallback default to prevent `TypeError` on `null` or `ValueError` on non-integer strings.
- **Tolerant JSON extraction:** Long-form model responses frequently contain unescaped control characters (such as literal newlines or tabs) inside JSON strings; decode candidate strings with `json.loads(..., strict=False)`. Prioritize fenced ` ```json ` blocks over naive bracket scanning when trailing explanatory text contains brackets.
- **Graceful classification fallback:** Wrap classification JSON parsing in try-except; if the model fails to return valid JSON during `_classify`, log progress and fall back to `"uncertain"` so the item receives the `ai/uncertain` tag instead of crashing the run.
- **Clean HTML before backfilling:** In `--short-note-only` mode, Zotero note content is rich HTML with inline CSS. Pass `clean_html(note_text)` before formatting the prompt template to save tokens and prevent CSS noise from corrupting keyword extraction.
- **Normalize short-note punctuation before validation:** LLMs commonly emit half-width colons (`疑问:`) or trailing/leading pipes. Strip outer pipes, collapse internal newlines to spaces, and normalize `疑问:` to `疑问：` before rejecting the string, preventing false `ai/no_keywords` tag degradations.
- **Supplementary-only fallback:** When an item has only one PDF attachment and its name or content causes `infer_pdf_kind` to categorize it as `supplementary`, fall back to treating it as main text instead of raising an empty Markdown error.
- **Bypass loopback proxy for local AI endpoints:** When pointing `base_url` to a local loopback (`127.0.0.1` or `localhost`, such as local `cli-proxy-api`), instantiate OpenAI's HTTP client with `httpx.Client(trust_env=False)` because macOS system proxy settings divert loopback traffic to outbound proxies (e.g. port 1082) and cause immediate `Connection error` drops.
- **Multi-model fallback chains with reasoning effort:** Configure `fallback_models` (e.g. `model = "deepseek-flash"`, `fallback_models = ["gemini-3.8-flash-high", "gpt-5.6-sol"]`) so transient model outages, 429s, or 5xx errors automatically fail over to secondary providers without aborting the batch. When passing `reasoning_effort="max"`, trap unsupported-parameter exceptions and retry on the same model without the parameter to maintain compatibility across mixed reasoning and non-reasoning backends.
- **Spot-check quoted numbers against the parsed source:** a note reporting a capacity, impedance or barrier is asserting a fact about the paper, so verify it against the MinerU Markdown (`.zot/state/mineru/<sha256-of-pdf>/vlm/document.md`), not against the rendered note. Normalize before searching: MinerU emits formulas, units and decimals as LaTeX with per-character spaces, so `60.61` is stored as `6 0 . 6 1` and a plain substring search returns zero hits that read like a fabrication. Treat a total miss under both the plain and the whitespace-tolerant form as a mismatch warranting a re-read of the sentence, and always keep each number attached to its rate, cycle count or sample condition. Do not stop at "mismatch": MinerU glues adjacent numbers with `\lor` where the PDF said "and", so the input itself is damaged and the model may repair it into a plausible-looking decimal. Whether such a value is real cannot be settled from MinerU Markdown at all — its spaced digits make a genuine number unfindable. Cross-check the text layer Zotero already extracted, `~/Zotero/storage/<ATTACHMENTKEY>/.zotero-ft-cache`, which keeps `201.578` and `vs.` intact where MinerU renders `2 0 1 . 5 7 8` and `\lor`. A paper may also state one quantity at two precisions in different sections (`201.6` in Results, `201.578` in Discussion), so matching either counts. Never report a number as fabricated on a single-source miss; a wrong accusation is a credibility cost that outlasts the note.
- **`--force` appends a note, it does not replace one:** a re-run writes a new note and leaves earlier ones live, so an item accumulates notes; the tool never trashes the old one, and a client-side delete is not something to assume from a version bump. Before re-running, list the item's notes with `z.children(ITEMKEY)` and tell the user how many already exist and which key you are keeping. Deleting notes is destructive — ask, do not tidy silently.
- **Figures must be attached, not merely referenced:** MinerU leaves a bare `![](images/<sha>.jpg)` where each plot belongs, so a prompt built from `document.md` alone makes the model grade captions it never saw. Attach the files from the parse's `raw/images/` as multimodal parts, main-text panels before SI, and read `--dry-run`'s `figures` sent against `figure_refs` found. Wiring, caps, the text-only-model fallback and the truncation retry are in `references/ai-note-verification.md`.

## Automated DOI intake into a collection

1. Inspect `zot schema add`, `zot schema collection move`, and `zot --json collection list` to identify the target by its current key and name. Preview the actual input with `--dry-run`; validate DOI and selection fields before the first write. If the user wants this as a Zotero tool, put the implementation and tests in the Zotero repository's `tools/` tree, keep the producer's job separate, and point the downstream scheduler wrapper at the moved script. Check Git status in both repositories before committing; preserve unrelated edits.
2. Use the Zotero Web API for DOI writes and include resolved metadata; do not silently create bare records when resolution fails. When direct import without deduplication is requested, create each selected row even if the DOI already exists elsewhere. If the upstream CSV already contains a Chinese translation, reuse its `title_zh` rather than calling a model again; reject a selected row with a missing or multiline translation before any write. Put a single `translated-title: <中文标题>` line into the new item's Zotero `extra` field at creation, preserving any other Extra lines, while keeping the original English `title` and DOI unchanged. Specify the destination collection on creation where supported. Read back each returned item key and verify the exact DOI, collection, and Extra line before recording success; Zotero Desktop's local SQLite view can lag until sync.
3. For multi-item runs, record verified progress per row so a partial failure can resume without repeating rows already confirmed by *that run*. This checkpoint is not a library-wide deduplication policy. If the remote write's result is uncertain, surface it instead of blindly retrying and risking another record. Keep the input CSV on any failure. If the user explicitly requests cleanup, retain the selected DOI/key manifest until every item has been read back, then delete **only the processed CSV** (including a valid zero-candidate run); never delete it after a partial failure. After the run, independently read the remote destination collection and compare its added keys/DOIs with the manifest—Zotero Desktop's unsynced SQLite view cannot verify a fresh Web API write. Test success, failure, resume, and dry-run paths against fixtures without mutating the real library.

## Full-library translated-title backfill

1. **Inventory before writing.** Read the *remote* user library with the Web API (e.g. the configured `ZoteroWriter(...)._zot.everything(_zot.top(limit=100))`), not just a local `zot list` page. Count items whose nonempty title has no case-insensitive `translated-title:` line in `data.extra`, broken down by item type; skip untitled notes. Report the scope and keep existing translations untouched. Zotero Desktop's SQLite view can lag behind Web API writes, so it cannot establish the complete live missing set.
2. **Stage translations separately.** Batch only missing `key`/`title` pairs into a JSON-only model call; match every response back by exact key and original title, require a nonempty Chinese translation, and retry malformed batches only a bounded number of times. For Zotero titles containing JATS markup or embedded newlines, prepare a readable model input while retaining the exact original Zotero title for identity and write verification; do not turn a subscript such as `MoSe<sub>2</sub>` into a misleading formula. Persist a complete, validated translation plan before the first library mutation. A successful first batch is not proof that the full inventory has been staged.
3. **Apply conservatively.** Re-read each target key immediately before updating. If an existing `translated-title:` appeared meanwhile, leave it alone; otherwise append one `translated-title: <中文标题>` line to its current Extra, preserving unrelated lines and the original title/DOI. Use the Zotero Web API writer, then read back that exact key and compare title, DOI (when present), and the Extra line. Checkpoint verified successes per key so a partial failure can resume without replacing existing translations. Do not treat an uncertain write response as permission to issue the same write blindly.
4. **Verify completion.** Compare the preflight missing-key manifest against verified keys and a fresh remote inventory; report updated, newly-already-translated, unresolved, and untitled counts separately. If a background run is still active or stopped partway, report only confirmed progress and retain its checkpoint; never claim that all titles were translated based on a launch or a small probe.

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
zot workspace index topic --no-embed
zot workspace embed topic
zot --json workspace query "question" --workspace topic
```

The `zot pdf` command parses through MinerU cloud; its Markdown feeds `ai_analyze` and `content_list.json` feeds RAG, with SHA-256-addressed cache under `.zot/state/mineru/`. For a parent item with multiple PDFs, enumerate attachment keys first; plain `zot pdf ITEMKEY` refuses ambiguity, and all parsing options accept `--attachment ATTACHMENTKEY`. For local-only reading, use `ZoteroReader.get_pdf_attachments()` to resolve paths by attachment key, inspect the first page to classify main article versus SI even when filenames match, then open locally with a PDF reader such as PyMuPDF. Never send all attachments to MinerU just to discover which PDFs exist. Reuse an existing complete index when possible, and reindex when PDFs, notes, or item metadata change.

For logged MacBook workflows, use `tools/paperrag/run-rag-workspace.zsh` and `tools/paperrag/run-rag-evidence-search.zsh`. Read `tools/paperrag/Instructions.md` before running a full collection inventory, forced rebuild, or long embedding backfill.

### Invariants & pitfalls for PaperRAG workflows

- **Explicit profile forwarding:** When targeting a specific Zotero profile, pass `--profile NAME` to `tools/paperrag/run-rag-workspace.zsh` or `run-rag-evidence-search.zsh`; the wrapper scripts forward it to both `workspace-inventory.py` and downstream `zot` subcommands, avoiding fallback to default library credentials.
- **Defensive vector store inspection during inventory:** In `workspace-inventory.py`, wrap Qdrant collection count in try-except. If the vector collection does not exist yet or experiences concurrent lock contention, log a warning and fall back to `vector_count = 0` rather than crashing the preflight inventory and failing the entire workflow.
- **Log cleanup lifecycle:** Successful workflow runs remove their temporary directory under `log/` automatically (and prune `log/` itself when empty). Always pass `--keep-log` or `--keep-inventory` when running automated checks or diagnostic runs that assert on `inventory.json` or run outputs.
- **Script permissions & path migration:** Tool entrypoints live under `tools/paperrag/` (not `tools/`). Scripts must retain POSIX executable bits (`chmod +x`), which `tests/integration/test_tool_scripts.py` strictly verifies with `os.access(..., os.X_OK)`. Ensure integration tests resolve `TOOLS_DIR` to `tools/paperrag`.
- **Executable bits now survive Git; keep the 644/755 split.** This checkout used to carry `core.fileMode=false` while most tracked files were mode 700 on disk, so `chmod +x` never reached Git and a clone dropped the bit on the `.zsh` entrypoints. That is fixed: every file is 644, directories and the three shell entrypoints are 755, and `core.fileMode=true` records future changes. Keep new scripts at 755 (`chmod +x` then verify with `git ls-tree HEAD tools/paperrag/` showing `100755`); do not leave ordinary files mode 700.

### Collection renames in repository workflows

1. Run `uv run zot --json collection list` to establish the current name and key from the local library. Identify the renamed collection by its stable key when known; do not infer its new name from an old workspace label. Avoid opening the full `.zot/config.toml` just to discover collections, because it contains credentials.
2. Search repository defaults and usage examples for the old collection name. Change the source-collection default and matching examples, quoting names containing spaces or shell metacharacters. Keep an existing local workspace name and its `.workspace/` state unchanged unless the user also requests a workspace migration; a workspace label is not a Zotero collection name.
3. For a script-default change, first run a failing integration test against an isolated copy of the test SQLite fixture with the collection renamed; invoke the script in `--dry-run` mode without `--collections`, and assert that the inventory names and nonzero item count match the renamed collection. Then update the default, rerun the focused test, check script syntax and run the full test suite. Never edit the live Zotero SQLite database for this verification.

## Repository documentation maintenance

`README.md` (Chinese, user-facing) and `AGENTS.md` (English, agent-facing) are
the only docs; no `docs/` tree exists. Derive every documented command, flag,
config key and env var from the running CLI (`uv run zot --help`,
`uv run zot COMMAND --help`, `grep -rho 'ZOT_[A-Z_]*' src/zotero_cli`), never
from the existing prose, and read `git diff` for the refactor the docs must
describe. Docs live in `/Users/liuchzzyy/CodeSpace/zotero-cli/` — an
`@file:zotero-cli/README.md` hint resolves against the shell CWD and can
falsely report the file missing.

- `.zot/config.example.toml` is tracked by Git: keep it credential-free and
  machine-neutral, and surface any real `api_key`, `mineru_token`, or absolute
  home path found there instead of shipping or silently reverting it. Path
  placeholders are `/Users/your-name/Zotero` and
  `.../Profiles/<profile>/prefs.js`.
- Safety tiers exist only in `src/zotero_cli/cli.py`
  (`_READ_COMMANDS`/`_WRITE_COMMANDS`/`_DESTRUCTIVE_COMMANDS`); `collection` is
  read-tier, and misfiling it among write commands is a recurring error.
- A surface change must also reach `AGENTS.md`, the repo-bundled
  `skill/zotero-cli/SKILL.md`, and this profile's copy; grep old `tools/...`
  paths after any directory migration.

Full checklist and reporting rules: `references/doc-maintenance.md`.

## Troubleshooting and cleanup

- Run `zot config show` to verify the local database path without exposing the full API key.
- Exit 2 means authentication failure; exit 3 means invalid input or missing configuration; exit 4 means not found; exit 5 means network/rate-limit failure; exit 6 indicates a conflict such as detected duplicates.
- When a command surface is unclear, run `zot schema COMMAND ...` instead of guessing flags.
- Local reads open the database as `?mode=ro&immutable=1`, which **ignores the WAL**. While Zotero Desktop is running, freshly synced items and attachments live in `zotero.sqlite-wal` and are therefore invisible: an attachment reports `available: false` and a stored PDF path resolves to nothing, which reads exactly like broken library data. Check `ls -l ~/Zotero/zotero.sqlite-wal` before believing a missing-file report; if it is non-empty, read from a temp copy of `zotero.sqlite` together with its `-wal` and `-shm` so the WAL is replayed. Do not fall back to a plain `mode=ro` handle — it fails with `database is locked` against a live Zotero. The current `reader.py` performs this copy-and-replay itself whenever `zotero.sqlite-wal` exists and is non-empty, so the workaround is now code rather than a manual step; replacing it with a plain `immutable=1` connect is a regression that resurfaces as phantom missing attachments.
- Before cleanup, classify paths as disposable caches (`.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `__pycache__/`, build artifacts, and temporary logs) or persistent research state (`.workspace/`, `.zot/state/`, and Zotero data). Remove only the former unless the user explicitly authorizes deleting indexes or parse packages; those directories contain reusable RAG and MinerU results.
