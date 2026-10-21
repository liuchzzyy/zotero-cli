# Verifying an `ai-note` run

A run reports success on its own; the note it writes is a set of claims about a
paper. These four passes are what turn "the command exited 0" into "the note is
trustworthy enough to report".

## 1. Triage the attachments before the run

`ai-note` accepts no attachment selector, so whatever PDFs the parent item
owner has all go into the prompt. Decide that input yourself first.

```bash
uv run zot --json zotero pdf list ITEMKEY
```

- `available: false` with an empty `~/Zotero/storage/<KEY>/` means a broken
  record: it is skipped by the parse and should be reported to the user for
  deletion, not treated as content.
- Identical filenames are **not** evidence of duplicate PDFs. Publishers ship the
  main text and the Supporting Information under the same name.
- Classify each file from its own first heading once parsed, rather than from the
  filename: a file whose Markdown opens with `# Supplementary material` is SI.
- When two files might be the same paper twice, compare their long lines rather
  than their sizes. Parse both and compute set overlap over lines longer than ~40
  characters: near-zero Jaccard means orthogonal content (main text + SI, or two
  different papers); a high overlap means a true duplicate that will double the
  prompt.

MS/SI is now decided per **item**, by `resolve_pdf_kinds()` in `workspace/rag/metadata.py`,
from the figure/table numbering inside `content_list.json` captions. The numbering is
the most reliable signal available: an SI cover repeats the article title and even its
filename, so no per-attachment text cue can separate the pair. The rule order, the
accepted caption forms and the measured accuracy are in `pdf-kind-classification.md`.

Accepted forms — the "S" sits either on the number or on a qualifier word, and MinerU
produces both: `Figure S1`, `Fig. S1`, `Supplementary Figure 1`, `Supplementary Fig. 2-1`,
`Table S1`, `Supplementary Table 1`, against plain `Figure 1`, `Fig. 2`, `Scheme 1`,
`Table 1`. Panel fragments (`a b`, `C`, `(a-1)`) carry no label and are ignored. Rules run
in order: one S-numbered document is the SI; failing that, one document carrying *any*
S-numbered caption (a Nature-style SI numbers figures plainly but tables
"Supplementary Table 1"); then one plainly numbered document with everything else as SI;
then `infer_pdf_kind` (cover line, filename, front matter); then the longer document.

Front matter (`abstract` + `introduction`) always blocks a supplementary verdict — a
merged article+SI PDF has both — because demoting a real article is the costly error: its
figures lose the "required" status and get sampled away by the SI cap.

What to check on a run: `pdf_count`, and that `main_images` is non-zero and roughly the
number of panels you expect in the article. `omitted_images` should be zero except for
SIs bigger than 30 panels (then it equals `si_candidates − si_images`), and
`skipped_images` lists degenerate attachments under 1 kB or over 5 MB that were dropped
with a prompt notice instead of failing the run.

Known limits of the mechanism: a supplement that numbers its figures plainly *and* has no
S-numbered table captions cannot be told apart from the article by numbering, and a pair
of scanned old papers with no captions falls through to the filename heuristics. On the
194 cached two-PDF items here the rule resolves every item to exactly one main text;
cross-checked against cover/filename ground truth, agreement is 111/113 with no
article→SI errors (the two misses were my own labels being wrong).

## 2. Read the MinerU cache directly for spot checks

Parses are cached by PDF content hash, so a spot check costs no cloud call:

```
.zot/state/mineru/<sha256-of-pdf>/vlm/
    document.md        # the Markdown that fed the prompt — search this
    content_list.json  # structured blocks (feeds RAG)
    manifest.json
    raw/…              # images and the upstream list
```

Map an attachment key to its cache directory by hashing the stored PDF
(`shasum -a 256 ~/Zotero/storage/<KEY>/*.pdf`). Grep `document.md` for the
claims you intend to repeat.

## 3. Normalize before searching MinerU Markdown

MinerU renders mathematics as LaTeX, and its OCR emits per-character spacing
inside those spans. The value is present, but not as a contiguous string:

| You expect | The Markdown actually contains |
|---|---|
| `60.61` | `$R_{ct} = 6 0 . 6 1 \Omega$` |
| `1.6 eV` | `1 . 6 eV` |
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
as a fabricated number. A zero hit in *both* forms is real: re-read the sentence
around the claim before deciding whether the note over-reached.

### The PDF text layer is the tie-breaker

MinerU is a derived artefact. Zotero already extracted the PDF's own text to
`~/Zotero/storage/<ATTKEY>/.zotero-ft-cache`, and that layer has no OCR digit
splitting and no LaTeX, so it settles a disagreement instead of leaving you
guessing. Search there before calling any number invented:

```python
text = re.sub(r"\s+", " ", open(cache_path, errors="replace").read())
print("201.578" in text, "vs." in text)  # True True, where MinerU shows `2 0 1 . 5 7 8` / `\lor`
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
`chart` blocks become `【图 N】` markers **and** payload parts read from `raw/`, so a
chart panel that Markdown never referenced is still attached. The request is
budgeted as one preflighted call — `min(route context, 500k)` minus rendered text,
output reserve and safety margin buys images at a per-image ceiling. **Every
main-text image must fit or the run fails**; SI is capped at 30 and prioritised by
`Figure S<n>` references in the main text, and the prompt lists the SI numbers that
were not sent so the model cannot describe those panels.

Read `--dry-run`'s `main_images`, `si_images`, `si_candidates`,
`estimated_tokens_upper_bound`, `request_bytes` and `budget_model` before trusting a
run, and compare `estimate` with the provider's reported `usage` afterwards — the
estimate is a conservative bound, not a tokenizer. A route without a verified
context/image budget raises `validation_error` rather than guessing. A
`finish_reason=length` answer is rejected (never written as a note); a
capability-level image rejection drops all images but keeps every text/table block
and appends an explicit "no images attached" notice. Main-text and supplementary
document copies are deduped (longest copy kept) by original document text before
numbering. `max_extracted_chars` is now a ceiling that fails the run instead of
truncating evidence; its default is 250,000 chars, and `timeout_seconds`
defaults to 300 s (a 51-image, 201 k-char analysis call measured 106 s).
Titles/authors/journal/date/DOI/full text are wrapped as untrusted data by the
prompt templates; only the *short-note* prompt is still char-capped
(220,000 after counting the template plus rendered metadata), while the analysis
prompt is bounded by `max_extracted_chars` and the 500k image budget instead. A typed writer failure with `committed` set
means the note write may already have landed: verify before retrying, and remember
`--force` appends notes instead of replacing them. Definite rejections
(`validation_error`/`not_found`/auth) are non-committed and release the idempotency
reservation; only committed/unknown outcomes keep it reserved.
A text-only model in the fallback chain rejects the image parts, so retry that
model without them instead of aborting; capability is per model, so an image
rejection must not strip figures for a vision model tried next. Before repeating any judgement about plots, micrographs or axis
readings, look for details that exist solely in the graphics — a scale bar, an
axis range, an inset label. Commentary that matches the caption wording verbatim
is consistent with captions only.

The files themselves are on disk under the parse's `raw/images/`, so the vision
path can be probed outside the note pipeline: one direct call with a handful of
figures, asking for figure type, axis ranges and readable annotations, costs
nothing in the library and settles whether images work at all.

Two operational notes. There is no figure count cap any more: every main-text
panel is mandatory and the run fails rather than dropping one, SI is capped at
30, and a parse-retry resends the *same* image set (only an explicit
image-capability rejection drops all images, keeping every text block). And a
`finish_reason=length` answer is rejected instead of being saved, so a truncated
JSON note cannot land in the library — read the error rather than re-running.

For a scheduled collection run (`tool/ainote/scheduled_analyze.py`), the new
fail-closed errors are permanent, not transient: an item whose structured text
exceeds `max_extracted_chars` or whose main figures exceed the token budget can
never succeed on retry. The scheduler counts explicitly marked input-budget failures and parks the
item as `unanalyzable` after three, reports it under `blocked` rather than
`failed`, and skips it afterwards; clear it with `--retry-parked` after fixing
the cause. Network/provider failures and uncertain writes are never parked.

## 6. Shape of the report to the user

1. Bibliographic identification (authors, journal, year, DOI) and which
   attachment was judged main text versus SI.
2. Run facts: template, `pdf_count`, extracted chars, note key, section count.
3. A verified-numbers table: each quoted value, the literal source hit, verdict.
4. The `short-note` line, including its `疑问：` clause — that clause is where the
   model records what the paper does not resolve, and it is often the most
   useful sentence for the user.
5. Library-hygiene flags found on the way (broken attachment records, SI sharing
   a filename with the main text, missing statistics that weaken the note's own
   scorecard).
6. Whether figures actually reached the model, whenever the note comments on
   plots or images.
