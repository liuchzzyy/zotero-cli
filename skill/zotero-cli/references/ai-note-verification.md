# Verifying an `ai-note` run

A run reports success on its own; the note it writes is a set of claims about a
paper. These checks are what turn "the command exited 0" into "the note is
trustworthy enough to report".

## 1. Triage the attachments before the run

`ai-note` accepts no attachment selector, so whatever PDFs the parent item
owner has all go into the prompt. Decide that input yourself first.

```bash
uv run zot --json zotero pdf list ITEMKEY
```

- `available: false` with an empty storage directory is unavailable input and is skipped. Check Zotero sync, the configured profile and attachment resolution before calling the record broken. Report it; deletion needs a separate user instruction.
- Identical filenames are **not** evidence of duplicate PDFs. Publishers ship the
  main text and the Supporting Information under the same name.
- Classify each file from its own first heading once parsed, rather than from the
  filename: a file whose Markdown opens with `# Supplementary material` is SI.
- When two files might be the same paper twice, compare their long lines rather
  than their sizes. Parse both and use the implementation in `ai_notes/documents.py::_kept_duplicate_indices`: nonempty sets of stripped lines of at least 60 characters, with at least 90% of the shorter candidate’s lines present in a preferred longer/earlier document. This is directional overlap, not Jaccard. Dedupe runs across the whole item; differing filenames or roles do not preserve a duplicate.

MS/SI is now decided per **item**, by `resolve_pdf_kinds()` in `pdf_rag/metadata.py`,
from the figure/table numbering inside `content_list.json` captions. The numbering is
the most reliable signal available: an SI cover repeats the article title and even its
filename, so no per-attachment text cue can separate the pair. The rule order, the
accepted caption forms and the measured accuracy are in `pdf-kind-classification.md`.

Accepted forms — the "S" sits either on the number or on a qualifier word, and MinerU
produces both: `Figure S1`, `Fig. S1`, `Supplementary Figure 1`, `Supplementary Fig. 2-1`,
`Table S1`, `Supplementary Table 1`, against plain `Figure 1`, `Fig. 2`, `Scheme 1`,
`Table 1`. Panel fragments (`a b`, `C`, `(a-1)`) carry no label and are ignored. Rules run
in order: one S-numbered document is the SI; failing that, one document carrying _any_
S-numbered caption (a Nature-style SI numbers figures plainly but tables
"Supplementary Table 1"); then one plainly numbered document with everything else as SI;
then `infer_pdf_kind` (cover line, filename, front matter); then the longer document.

Front matter (`abstract` within 6,000 characters and `introduction` within 12,000) guards caption/text SI decisions in rules 1, 2 and 4 — a
merged article+SI PDF has both — because demoting a real article is the costly error: its
figures can lose required status and compete for the SI context/byte budget. This guard does not guarantee the final role in the unique-main or longest-document fallback.

What to check on a run: `pdf_count`, and that `main_images` matches the valid extracted panels expected from the article. Zero calls for inspection; it can also mean a text-only article, missing extraction, or genuine 1x1 placeholders. Inspect `omitted_images` and its reasons: it can include budget-excluded or unavailable SI images and genuine 1x1 placeholders, including placeholders in main input. Do not infer its count from SI arithmetic alone;
`skipped_images` lists genuine 1x1 placeholders or unavailable SI images. Corrupt or uncompressible required main images fail before a note write. `image_manifest` records the decoded dimensions, lossless encoding, source/sent hashes and bytes.

Known limits of the mechanism: a supplement that numbers its figures plainly _and_ has no
S-numbered table captions cannot be told apart from the article by numbering, and a pair
of scanned old papers with no captions falls through to the filename heuristics. Historical notes report a 194-item cached sample and 111/113 agreement against a labelled subset, followed by corrections to two labels. That corpus and its labels were not revalidated in this documentation pass; those figures are not a current accuracy guarantee. For three or more attachments, early rules can leave multiple main documents.

## 2. Read the MinerU cache directly for spot checks

Parses are cached by PDF content hash, so a spot check costs no cloud call:

```
.zot/state/mineru/<sha256-of-pdf>/vlm/
    document.md        # derived Markdown for spot checks and role heuristics
    content_list.json  # structured evidence for AI Note and RAG
    manifest.json
    raw/…              # images and the upstream list
```

Map an attachment key to its cache directory by hashing the stored PDF
(`shasum -a 256 ~/Zotero/storage/<KEY>/*.pdf`). Grep `document.md` for the
claims you intend to repeat.

## 3. Normalize before searching MinerU Markdown

MinerU renders mathematics as LaTeX, and its OCR emits per-character spacing
inside those spans. The value is present, but not as a contiguous string:

| You expect  | The Markdown actually contains                           |
| ----------- | -------------------------------------------------------- |
| `60.61`     | `$R_{ct} = 6 0 . 6 1 \Omega$`                            |
| `1.6 eV`    | `1 . 6 eV`                                               |
| `5 mg cm-2` | `5 mg cm\u0001<sup>2</sup>` (unit superscript split off) |

So strip separators before comparing, and search both forms before concluding
anything:

```python
flat = re.sub(r"\s+", " ", text)
normalized = re.sub(r"[\s{}\\\^_$]", "", text)  # LaTeX-tolerant
hits = normalized.count("60.61")
loose = re.search(r"6\s*0\s*\.\s*6\s*1", flat)  # whitespace-tolerant
```

A zero hit in the first form alone is a false negative and must not be reported
as a fabricated number. No hit under either normalization still proves only that this derived text search missed the value. Read the original PDF, figure/table, caption and experimental conditions before judging the claim.

### Cross-check the PDF text layer and original PDF

MinerU is a derived artefact. Zotero already extracted the PDF's own text to
the configured attachment storage’s `.zotero-ft-cache`. It is an independent extraction that may preserve digits damaged in MinerU, but can also be absent, stale or wrong. Cross-check it and the original PDF before judging a number:

```python
text = re.sub(r"\s+", " ", open(cache_path, errors="replace").read())
print("201.578" in text, "vs." in text)  # Illustrative search, not a guaranteed hit
```

Two things it catches that MinerU hides: a value present only in a section MinerU
rendered as mangled maths, and the same quantity stated at two precisions in
different sections (a Results figure rounded, a Discussion figure exact). Both are
genuine, and accusing the model of inventing either is a wrong report the user
will remember.

## 4. QC the written note through the Web API

The local SQLite view is stale until Zotero Desktop syncs, so read the note back
with pyzotero (see the SKILL.md snippet for the exact client construction).

- Strip inline-styled HTML before measuring; report plain-text length as a
  coarse signal only.
- Count `h1`/`h2` headings and flag a heading immediately followed by another
  heading as an empty section — except the leading title `h1` followed by the
  first `h2`, which is the normal layout and not a defect.
- Validate the structural check itself on a known-good note before reporting its
  output. A regex over HTML is easy to write so that it spans tags or catches the
  title→first-section boundary, which turns a healthy note into "empty sections";
  when a finding contradicts the note's plain-text length, suspect the checker
  first and re-run it per-section rather than publishing the alarm. The same
  applies to section-body length: a section that legitimately holds only `h3`
  subsections measures zero characters of its own and is not empty.
- Note whether tables and list items are present; a structured note without any
  table usually means the model skipped the mandated fixed tables.
- Confirm the Extra field kept pre-existing lines (`translated-title:`) and
  appended exactly one `short-note:` line.
- Pre-existing tags on the item are not evidence of this run. Report only what
  the API shows for this note key and tag set, and tell the user to sync before
  expecting to see the note in the app.
- Count the notes, not just the new one. `--force` appends rather than replaces,
  so a re-run item holds several live notes; naming only the newest key leaves
  the user with duplicates they did not know about.

## 5. Check what the model could actually see

The note is built from MinerU `content_list.json`, not `document.md`: `image` and
`chart` blocks become `[图 N]` markers **and** payload parts read from `raw/`, so a
chart panel that Markdown never referenced is still attached. The request is
budgeted as two main analysis calls — text evidence first, then evidence packet plus figures. The visual call uses `min(route context, 800k)` minus rendered text,
output reserve and safety margin buys images at a per-image ceiling. **Every
main-text image must fit or the run fails**; SI has no fixed count cap and is prioritised by
`Figure S<n>` references in the main text, and the prompt lists the SI numbers that
were not sent so the model cannot describe those panels.

Read `--dry-run`'s `main_images`, `si_images`, `si_candidates`,
`estimated_tokens_upper_bound`, `request_bytes` and `budget_model` before trusting a
run; dry-run visual counts reserve the maximum evidence-packet length and are estimates, not the actual plan. Compare `estimate` with the provider's reported `usage` afterwards — the
estimate is a conservative bound, not a tokenizer. A route without a verified
context/image budget raises `validation_error` rather than guessing. A
`finish_reason=length` answer is rejected (never written as a note); an
image-capability rejection in the visual stage tries another eligible route with the same figures; it never saves a text-only downgrade. Main-text and supplementary
document copies are deduped across the whole item (longest copy kept, earlier on ties) by original document text before
numbering. `max_extracted_chars` is now a ceiling that fails the run instead of
truncating evidence; its default is 250,000 chars, and `timeout_seconds`
defaults to 300 s per model request, not a total pipeline deadline.
Titles/authors/journal/date/DOI/full text are wrapped as untrusted data by the
prompt templates; only the _short-note_ input is clipped by `fit_text_to_prompt()` using the 220,000-character helper minus the template and reserve, while the analysis
prompt is bounded by `max_extracted_chars` and per-stage 800k estimated budget instead. A typed writer failure with `committed` set
means the note write may already have landed: verify before retrying, and remember
`--force` appends notes instead of replacing them. Definite rejections (`validation_error`/`not_found`/auth) release a reservation only when no write may have landed. A rejected tag after a successful note/Extra update still has `committed=True`; committed/unknown outcomes keep the reservation.
The visual stage requires images: a rejection must not strip figures for a route tried next, and all routes rejecting pictures fails the run. Before repeating any judgement about plots, micrographs or axis
readings, look for details that exist solely in the graphics — a scale bar, an
axis range, an inset label. Commentary that matches the caption wording verbatim
is consistent with captions only.

The files are on disk under the parse’s `raw/images/`. An authorized direct vision probe can test selected figures without writing Zotero, but still sends data to the provider and can incur API usage. A successful probe is evidence only for that route, request and sample.

Two operational notes. There is no figure count cap any more: every main-text
panel is mandatory and the run fails rather than dropping one (except decoded 1x1 placeholders). SI is selected by context and exact serialized request bytes, and a parse-retry resends the _same_ image set with renewed preflight. And a
`finish_reason=length` answer is rejected instead of being saved, so a truncated
JSON note cannot land in the library — read the error rather than re-running.

For a scheduled collection run, the new
fail-closed errors are permanent, not transient: an item whose structured text
exceeds `max_extracted_chars` or whose main figures exceed the token budget can
never succeed on retry. The scheduler counts explicitly marked input-budget failures and parks the
item as `unanalyzable` after three, reports it under `blocked` rather than
`failed`, and skips it afterwards; clear it with `--retry-parked` after fixing
the cause. Network/provider failures and uncertain writes are never parked.

## 6. Partial writes and evidence boundaries

For collection failures, read the complete run log and checkpoint before
resuming. Count generated notes, keyword backfills, failed items and blocked
items separately; a successful HTTP response can still carry invalid model
JSON, while an uncertain note write may already have appended a child note.
Use the stage and error type to choose a focused reproduction: model response,
`parse_text_packet()`, visual request, writer, or checkpoint. Distinguish a
truncated response (`finish_reason=length`) from malformed complete JSON; do
not accept a partial response by loosening parsing. Run focused fixture tests
for a code repair, then inspect remote children, tags and Extra before any
live retry. Green local tests alone do not verify model output or Zotero writes.

`max_tokens = 0` omits that request parameter; the planning `OUTPUT_RESERVE`
is not a provider output limit. If the provider reports a length stop, the
pipeline requests a compact JSON retry once for that stage and fails without
writing if it is still truncated. For fenced JSON, the parser also examines
the raw brace-delimited response when a fence and `{` share a line; unbalanced
braces remain an error, not a recoverable completed note.

`save_analysis_note()` creates a child note before adding `ai/noted`. `save_short_note()` updates Extra before adding `ai/keywords`. A failed later tag does not undo either successful write: errors carry `committed=True`, the command fails and its idempotency reservation remains for reconciliation. Unknown Extra-write outcomes behave similarly. Do not cache these outcomes as success, add `ai/no_keywords` to disguise them or rerun full analysis blindly. A short-note generation failure or definite rejection before Extra is written can degrade the result and attempt `ai/no_keywords`; the CLI does not claim that tag exists unless verified. `--short-note-only` can also encounter partial writes.

The first stage’s `parse_text_packet()` checks JSON structure, required fields, source labels against known attachment/kind/page combinations, valid figure numbers and the packet budget. Malformed JSON or invalid source/figure references get one bounded text-stage retry; the returned evidence is checked again before any write. Oversized packets shorten only program-added `source_excerpt` windows before failing if the model-supplied evidence remains over 120,000 characters. It does not compare or reject numeric values in `claim`, `data` or `conditions`. Program-added `source_excerpt` supports the model’s check; it does not establish numerical truth or sample/condition correspondence. Manual spot checks in this reference are separate from program validation.

Missing MinerU tokens are typed configuration errors (`MinerUTokenMissingError`) in single and batch paths; all-cache-hit reads need no token. PDF parsing still has network/cache effects in `ai-note --dry-run`. MinerU publication is not a transaction: an old destination is deleted before replacement, so a failed publication can lose the old cache. Result properties read files lazily and do not guarantee a consistent snapshot across replacement.

## 7. Shape of the report to the user

1. Bibliographic identification (authors, journal, year, DOI) and which
   attachment was judged main text versus SI.
2. Run facts: template, `pdf_count`, extracted chars, note key, section count.
3. A spot-check table for numbers actually checked: value, source page/figure, sample/test conditions, evidence and uncertainty. Do not claim unchecked numbers are verified.
4. The `short-note` line, including its `疑问：` clause — that clause is where the
   model records what the paper does not resolve, and it is often the most
   useful sentence for the user.
5. Library-hygiene flags found on the way (broken attachment records, SI sharing
   a filename with the main text, missing statistics that weaken the note's own
   scorecard).
6. Whether figures actually reached the model, whenever the note comments on
   plots or images.
