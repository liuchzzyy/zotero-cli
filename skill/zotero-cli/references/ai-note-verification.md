# Verifying an `ai_analyze` run

A run reports success on its own; the note it writes is a set of claims about a
paper. These four passes are what turn "the command exited 0" into "the note is
trustworthy enough to report".

## 1. Triage the attachments before the run

`ai_analyze` accepts no attachment selector, so whatever PDFs the parent item
owner has all go into the prompt. Decide that input yourself first.

```bash
uv run zot --json pdf ITEMKEY --list-attachments
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

`infer_pdf_kind` then routes main text and SI into the prompt in the right order,
and a `pdf_count` of 2 is expected for a normal paper + SI pair.

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
normalized = re.sub(r"[\s{}\\\^_$]", "", text)      # LaTeX-tolerant
hits = normalized.count("60.61")
loose = re.search(r"6\s*0\s*\.\s*6\s*1", flat)        # whitespace-tolerant
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
print("201.578" in text, "vs." in text)   # True True, where MinerU shows `2 0 1 . 5 7 8` / `\lor`
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

Figures are attached to the request as multimodal parts, but the failure mode is
worth knowing: MinerU leaves only `![](images/<sha>.jpg)` where each plot belongs,
so a prompt assembled from `document.md` alone makes the model grade captions it
never saw. Confirm attachment actually happened before repeating any judgement
about plots.

Operational notes: number the markers `【图 N】` in the prompt text so the model can
cite a panel; send main-text panels before SI ones, because a cap consumed by
supplementary pages silently drops the main evidence; cap the count (a dozen
panels is already a large payload); and lower that cap on a parse-failure retry —
a truncated JSON answer is the usual reason a run fails once figures are attached.
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

Two operational notes: cap how many figures go into one request (a dozen panels
is already a large payload), and lower that cap on a parse-failure retry — a
truncated JSON answer is the usual reason a run fails once figures are attached.
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
