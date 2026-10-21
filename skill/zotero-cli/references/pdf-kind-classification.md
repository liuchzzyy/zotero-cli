# Main-text vs SI routing

`ai-note` consumes every PDF of an item and assigns each one a role; the role
decides what the request then *requires*. Every main-text image must fit the
budget or the run fails, while SI images are one candidate pool capped at 30 and
sampled. A wrong role is therefore not a label bug — it converts required
main-text evidence into optional, budget-sampled evidence, silently.

## Current mechanism

Two layers, both string heuristics over the cached `document.md`, both
classifying one attachment at a time.

`workspace/rag/metadata.py::infer_pdf_kind(text, filename)` — first matching rule
wins:

| # | signal | window |
| --- | --- | --- |
| 1 | filename token `si`/`esi`/`supp`/`suppl`/`supplementary`/`supporting` | filename |
| 2 | a line **starting with** an SI cover phrase followed by nothing, an opening bracket/dash, or one of `for`/`of`/`to`/`esi` | first 500 chars |
| 3 | strong main hints `cite this:` / `broader context` / `abstract` / `introduction` | first 2500 chars |
| 4 | `experimental section` | first 2500 chars |
| 5 | SI phrase, `figure s1` or `table s1` | first 1200 chars |
| 6 | weak publisher stamps `received ` / `accepted ` / `doi:` | first 2500 chars |
| 7 | SI phrase, `figure s1` or `table s1` | first 10000 chars |
| 8 | fallback → `main` | — |

Rules 1–2 are suppressed when the opening contains `cite this:`. The split
between strong and weak main hints (3 vs 6) is the load-bearing detail: an SI
cover reprints the parent article's DOI and received/accepted dates, so those
stamps may only decide after every SI cue has been ruled out. Rule 5 exists
between 4 and 6 for the same reason — a phrase like `supporting information`
this early is a cover, whereas the same phrase thousands of characters in is a
main text citing its own SI (see the guardrail below).

`ai_notes/pipeline.py` then overrides: a first-2500-char span containing both
`this pdf file includes:` and `figures s1` forces `supplementary`. That override
exists because a same-named SI can carry the article title and byline, which
trips rule 3 — for some items it is the only thing that saves the pair.

These two layers classify **one attachment at a time** and are now the fallback
inside the set-level classifier below, not the whole mechanism. Reading the
rules alone tells you why a single file was decided; it no longer tells you the
item's roles.

## Signals that look structural but are not: `aside_text` and friends

`content_list.json` block types invite a layout-based classifier, and they do
not support one. Measured over 389 cached parses of 194 items whose two PDFs
both had parses, the block types present were `text`, `chart`, `image`,
`ref_text`, `header`, `page_number`, `equation`, `footer`, `page_footnote`,
`table`, `aside_text` (121 attachments) and one `code`.

`aside_text` is page-margin content, and in practice it is a **publisher
download stamp**: `Downloaded from https://onlinelibrary.wiley.com/…`,
`Published on 06 July 2021. Downloaded by <institution>`, `Downloaded from
pubs.acs.org/…`, plus a vertical page-number column in some Wiley layouts. The
same download stamps both the article PDF and its SI, so its presence does not
discriminate:

- Among 60 parses that are unmistakably main text (article URL stamp or
  `abstract` + `introduction`), 83% carry `aside_text`.
- Among 29 unmistakably SI, 7% do — and those are SI files that inherited the
  parent's download stamp or carry a page-number column.
- Items whose two PDFs both carry `aside_text` are common; one such pair has
  `article-pdf` in one file's stamp and `article-supplement` in the other's.

So a rule "main text has aside_text / SI does not" would be right most of the
time and would fail exactly on the mixed pairs where the role actually matters.
The same caution applies to `header`/`footer`/`page_footnote`, which are far more
common in main text (100% / 98% / 85%) than in SI (55% / 14% / 14%) because SI
often lacks a running head — a correlation, not a rule.

The one genuinely diagnostic string inside `aside_text` is the publisher URL
path: `article-pdf` versus `article-supplement`. It was correct in all 10 files
that carried one, but it is publisher-specific (ACS, Wiley), absent everywhere
else, and therefore at most a tie-breaker for a recognised publisher — never a
general role rule.

Consequence for the note input: `aside_text` stays in `_SKIP_TYPES` alongside
`header`/`footer`/`page_number`/`page_footnote`. It is watermark noise on either
role, so it must neither be fed to the model as evidence nor be used to decide
the role.

When a role signal is *proposed* — by the user or by you — measure it before
agreeing to it, and report the measurement rather than an opinion. Build a
labelled subset from strings the paper cannot fake (see the audit section), then
report the presence rate of the signal on each side plus the counter-examples in
both directions, and say plainly whether the rate is a rule or only a
correlation. A candidate that splits 83% vs 7% still has to explain the 7%, and
those files are usually the mixed pairs where the role matters. State the
mechanism behind the residue (an inherited download stamp, a vertical page-number
column) so the next session can judge a new layout instead of re-measuring this
one.

## The signal that does discriminate: caption numbering

Figure and table caption numbers are typeset, not watermarks: a supplement
numbers `Figure S1`, `Table S1`, the article numbers `Figure 1`, `Table 1`. Read
them from `image_caption` / `chart_caption` / `table_caption` in the same
`content_list.json` the note already consumes, so the test costs no extra I/O.
Over 389 cached parses (4615 numbered caption blocks) the forms are a closed
set — this is why it can be enumerated rather than heuristically guessed:

| form | S-numbered | plain |
| --- | --- | --- |
| `Figure S1` / `Fig. S1` / `FIG. S1` | 2729 | — |
| `Figure 1` / `Fig. 1` | — | 1133 |
| `Supplementary Figure 1` / `Supplementary Fig. 1` | 289 | — |
| `Table S1` | 351 | `Table 1`: 48 |
| `Supplementary Table 1` | 48 | — |
| `Scheme 1` / `Scheme S1` | 1 | 16 |

So `{figure, fig, scheme, table}` × `{S-number, number}` × `{no qualifier,
supplementary, supporting, esi}`, plus `-1`/`-2` sub-numbering. Decide per
attachment by `count(S-numbered) > count(plain) → SI` (figures and tables
separately, so a Nature-style SI can be rescued by its tables when its figures
are unnumbered). Measured on 113 attachments with strong labels: 93.8% correct,
and **zero** errors in the dangerous direction (main text called SI). Applied per
item it separates 35 of 36 previously broken items into exactly one main + one
SI, where the string rules produced 23 items with no main and 13 with two.

Parsing pitfalls, each of which silently produced wrong labels before being
fixed:

- **The `S` has two carriers.** `Figure S1` carries it on the number;
  `Supplementary Fig. 1` carries it on the qualifier word. A regex that only
  reads `figure\s*s\d` misclassifies every Nature-portfolio and Wiley-style SI
  that uses the qualifier form (two measured cases, 14 and 16 figures each).
  Accept `qualifier present OR number starts with s`.
- **Caption text is polluted with panel fragments.** Blocks arrive as bare letters
  (`a`, `a b`, `C`, `g`) and with a panel label glued in front
  (`e Supplementary Figure 5. …`). Use `search`, never a start anchor, and drop
  blocks whose text is only panel letters before counting.
- **A Nature-portfolio SI can number plainly.** One measured Nature
  Sustainability SI has 18 plain `Figure N` captions and only
  `Supplementary Table N` for its tables — within a single file the two signals
  point opposite ways. This is the main residual category; fall back to the
  string rules rather than inventing a rule.
- **About 2% of attachments have no numbered caption at all** (old scans, very
  short SI, SI that is mostly tables). Always keep the existing fallback.

Safety direction matters more than accuracy here: a main text labelled SI has its
required figures silently budget-sampled, while an SI labelled main only fails a
budget check loudly. So keep a hard guard — a document whose front matter carries
`abstract` or `introduction` must not be demoted to SI on captions alone.

## Failure modes that remain

- **The filename regex matches an author's name.** `..._Si Hyoung Oh_...`
  tokenizes to `si`, sending both the main text and the SI to `supplementary`.
  The regex is guarded against substrings of ordinary words (`sodium`, `spin`)
  but not against a given name or initials. Fixing it means anchoring the token
  to the end of the name (`_si`, `-si`, `si.pdf`), not widening the token list.
- **A title-first layout pushes rule 3 out of its own window.** With the title,
  authors and affiliations first, `introduction` can begin past char 2500 (one
  measured case: char 3387); rule 3 misses, rule 5 misses because the SI phrase
  is at char 9073, and rule 7 then matches that phrase — so a genuine main text
  becomes supplementary. Widening the strong-hint window is the real fix; do not
  reorder rules to paper over it.
- **`sm` is not a filename token.** A Science Advances SI named
  `sciadv.xxxx_sm.pdf` is decided by its cover line (rule 2) or, if the cover is
  unrecognised, by rule 5. Adding `sm` to the token set would misfire on names
  containing `sm` as a substring boundary; prefer extending rule 2.

## Guardrails: fixed failures that must not regress

Two rule-level fixes are implemented and covered by
`tests/unit/test_pdf_kind_regression.py`. Re-read that file before touching
`infer_pdf_kind`; each case is a recorded real misclassification, not a
hypothetical.

- **Weak publisher stamps must not outvote a cover.** A DOI at char 247 under
  `Supplementary Materials for <title>` used to return `main`. Keep `doi:`,
  `received `, `accepted ` in `_WEAK_MAIN_PAPER_HINTS`, consulted last.
- **Cover lines are prefix matches, not whole-line equality.** Publishers write
  `Supporting Information for <title>`,
  `Electronic Supplementary Material (ESI) for <journal>`,
  `Supplementary Information: Experimental Details`. Whole-line equality missed
  all of these. The continuation whitelist matters as much as the prefix match:
  without it, prose such as `Supporting Information is available free of charge`
  becomes a cover line.
- **An early SI phrase outranks a weak stamp; a late one does not.** Rule 5's
  1200-char window exists so that an unrecognised cover variant still beats the
  DOI, while a main text that mentions its own SI at char 9000 does not flip.
  Widening rule 5 to rule 6's 2500-char window re-breaks the title-first layout
  failure above.

## Audit the roles before trusting a run

`--dry-run` exposes the outcome without writing: `main_images`, `si_images`,
`si_candidates`. `main_images: 0` on a multi-PDF item is the signature of a
misrouted main text — every figure then comes from the SI pool, sampled at 30 of
the total.

To measure the classifier across a library, take the connection from the reader
rather than opening a fresh one: `ZoteroReader` has already copied the database
and replayed the WAL, whereas `sqlite3.connect(..., mode="ro")` against the live
file fails `database is locked` and `sqlite3.backup()` of a ~100 MB library can
outlast a two-minute budget.

```python
from pathlib import Path
from zotero_cli.config import load_config
from zotero_cli.zotero.access.reader import ZoteroReader

reader = ZoteroReader(Path(load_config().data_dir) / "zotero.sqlite")
conn = reader._connect()
rows = conn.execute(
    """
    SELECT p.key, COUNT(*) FROM itemAttachments ia
    JOIN items p ON p.itemID = ia.parentItemID
    WHERE ia.contentType = 'application/pdf'
    GROUP BY p.itemID HAVING COUNT(*) > 1
    """
).fetchall()
```

Then run the real `infer_pdf_kind` on each cached `document.md` and record **which
rule decided** each attachment, not only the final label: the rule is what tells
you whether a fix is aimed correctly. Import the predicates
(`_SUPPLEMENTARY_COVER_HINTS`, `_COVER_CONTINUATIONS`, `_MAIN_PAPER_HINTS`,
`_WEAK_MAIN_PAPER_HINTS`, `_SUPPLEMENTARY_HINTS`, `_supplementary_filename`) so a
diagnosing script cannot drift from the code it audits. Group by
`(final kind, deciding rule)` and by per-item composition (`main=1,SI=1` /
`main=0` / `main=2`), open the outliers, and confirm against the PDF's first
page. Items without a cached parse are absent from such an audit — report the
coverage (one run: 194 of 609 multi-PDF items) rather than implying the whole
library was measured.

Label ground truth for the audit from strings the paper cannot fake: an article
`article-pdf` stamp or `abstract`+`introduction` for main, a
`Supplementary Materials for` cover, an `article-supplement` stamp, or a `_sm`
filename for SI, and leave everything else `unknown` rather than guessing. Then
check the confusion both ways: candidates the classifier calls `main` whose own
opening names themselves supplementary, and items with no main at all whose
largest attachment carries `abstract`/`introduction`.

### Validate a rule change by counting the flips

Before and after a classifier edit, run the previous rule order and the new one
over the same cached corpus and compare them item by item:

```python
from collections import Counter

Counter((old_kind, infer_pdf_kind(md, fn)) for md, fn in corpus)
```

Two things this catches that a test suite cannot. First, the transition matrix
shows which direction the change moves items: a fix aimed at the `main=0` group
that also moves items into `main=2` is net-neutral despite "24 items fixed".
Second, the flip list is small enough to open every entry's first page — on one
run 5 flips, of which 3 were the intended publisher-cover repairs and 2 were
main texts whose prose merely mentioned Supporting Information, which is what
produced rule 5's window. Reproducing the old grouping inside a throwaway audit
script is fine (the strong/weak split means the current constants no longer
express it); never let that copy live on as a second classifier.

## Set-level classification (implemented)

`workspace/rag/metadata.py::resolve_pdf_kinds(documents)` classifies an item's
attachments **together** and returns exactly one main text per item. It takes
`PdfDocument(identifier, filename, markdown, blocks)`; `pipeline.py` builds those
from the MinerU parse results, so `caption_counts()` reads the same
`content_list.json` the note consumes and the test costs no extra I/O. Rule-level
work above remains the low-risk part; this is the layer that removes the
`main=0` / `main=2` outcomes the string rules produced (23 and 13 items of 194 in
the measured corpus).

Single attachment → `main`, by definition. Otherwise, in order:

1. Exactly one document whose captions are S-dominant, after the front-matter
   guard below → that one is SI, everything else main.
2. Exactly one document carrying *any* S-numbered caption (figures or tables)
   while none is S-dominant → SI. This is what rescues a Nature-style SI whose
   figures are plain but whose tables are `Supplementary Table 1`.
3. Exactly one plainly-numbered document → main, the rest SI. This handles a
   supplement with no captions at all beside a normal article.
4. Exactly one document that `infer_pdf_kind` calls supplementary → SI.
5. Nothing conclusive → the longest document is the article.

The rules are ordered so the **conservative** answer wins: leaving a document as
main costs only images, while demoting a real article loses its figures to the
SI sample. Steps 1, 2 and 4 therefore each require *exactly one* candidate; two
candidates means the evidence is contradictory, not that either is right.

**The front-matter guard is absolute for steps 1, 2 and 4.** A document with
`abstract` in its first 6000 chars *and* `introduction` in its first 12000 is
never demoted to SI on captions or string cues, because a merged article+SI PDF
carries both front matter and S-numbered panels. It still counts as a main
candidate in step 3 and can still be the longest document in step 5.

Dedupe repeated copies across the whole item, not per kind: the same PDF can be
attached twice, and classifying one copy as supplementary must not keep both.
The survivor keeps its own kind.

Measured on 194 items whose two PDFs both had cached parses: 193 resolved to
exactly one main + one SI, one to one main + two SI, and **zero** to no main.
Cross-checked against cover/filename ground truth on 113 labelled attachments,
agreement was 111/113 with no article→SI errors — both misses were the label set
being wrong, not the classifier.

What still cannot be settled by numbering: a supplement that numbers its figures
plainly *and* has no S-numbered table captions is indistinguishable from the
article, and a pair of scanned old papers with no captions falls through to the
filename heuristics. Test these by `--dry-run`'s `main_images`; do not widen a
regex hoping to cover them.
