# Note text formatting (flattened formulas, CJK/Latin spacing)

Notes this pipeline writes are Chinese prose carrying English terms, formulas and
units. Two rules keep them readable, and both are formatting requirements rather
than niceties: formulas are written flat, and script boundaries are spaced.

## Rule 1 — no super/subscripts

The user rejects super/subscripts in notes outright. Flatten every script
character, including when the model or the PDF produced it correctly.

| Written | Flattened |
|---|---|
| `MnO₂` | `MnO2` |
| `Ti₃C₂Tₓ` | `Ti3C2Tx` |
| `Zn²⁺`, `Mn²⁺` | `Zn2+`, `Mn2+` |
| `CO₃²⁻` | `CO32-` |
| `mAh g⁻¹` | `mAh g^-1` |
| `10⁻⁵ S cm⁻¹` | `10^-5 S cm^-1` |
| `m² g⁻¹` | `m2 g^-1` |

Keep a caret only on a **negative** exponent: `g-1` reads as a subtraction, while
`m2` and `Zn2+` do not. Charge signs and positive exponents flatten bare, which is
ordinary chemical notation. `<sup>`/`<sub>` tags and LaTeX `^{}` / `_{}` are
rejected by the same rule, so a model that switches representation has not
satisfied it.

## Rule 2 — CJK/Latin spacing

Between a CJK character and a Latin letter, digit or Greek letter, insert exactly
one half-width space. Never inside a unit or a chemical formula.

```
层状δ-MnO2与5 mg cm^-2负载下保持率100%      ← wrong
层状 δ-MnO2 与 5 mg cm^-2 负载下保持率 100%  ← right
```

Flatten **before** spacing. A surviving `⁺` or `₂` counts as a Latin-run member
in the spacing pattern, so padding first would place the separator against a
character that is about to disappear and leave a doubled or orphaned space.

## Enforce at three levels

A request to fix this formatting means the whole pipeline, not the prompt:

1. **Code** — deterministic normalisation, so it holds whatever the model emits.
2. **Prompt** — templates state the rule with a correct/incorrect pair, so the
   model is aiming at the same target and needs less post-correction.
3. **Tests** — lock the behaviour, including the cases that must NOT change.

Fail the task if only the prompt changed: prompt wording is probabilistic and
the same defect returns on the next run.

## Character classes (deliberately narrow)

- **CJK**: Hiragana/Katakana, CJK Extension A, CJK Unified Ideographs, Hangul
  syllables, CJK Compatibility Ideographs.
- **Latin run**: ASCII alphanumerics, `² ³ ¹`, Greek and Greek Extended (so
  `δ-MnO2`, `α-MnO2` count as Latin-led). The super/subscript block (`⁺ ₂`) also
  belongs to the Latin run, which is why flattening has to run first — see Rule 2.
- **Tail extension on the right side**: `% ‰ ° +`. A charge sign ends the Latin
  run, so `Mg2+嵌入` becomes `Mg2+ 嵌入` and not `Mg2 +嵌入`.
- **Excluded on purpose**: CJK punctuation `，。、：；！？（）「」`. Full-width
  brackets are punctuation, not ideographs, so `（LIB）` and `Fig.3、` stay glued.
  Widening the CJK class to include them inserts spaces where a Chinese reader
  sees an error.

## MinerU splits numbers — rejoin before prompting

MinerU's OCR tokenises LaTeX digits separately, so a capacity or surface area
arrives as `$2 0 1 . 6 \lor 5 . 6 3 . 7 4 4$`. This is an input defect, not
something to leave for the model: asked to read damaged input, the model repairs
the garble into a plausible decimal.

Whether such a repaired value is real is a separate question, and MinerU cannot
answer it — its spaced digits make a genuine number unfindable there. Check the
text layer Zotero already extracted (`~/Zotero/storage/<ATTKEY>/.zotero-ft-cache`)
before calling anything invented: a value can survive intact there while being
invisible in the MinerU Markdown, and one paper may report the same quantity twice
at different precision in different sections. The original PDF and its conditions remain the reference; either extraction can be wrong.

Join digits before padding the prompt — digits first, then the decimal point,
because `2 0 1 . 6` must become `201 . 6` before a decimal rule can see numbers on
both sides of the dot, and iterate until stable so `201 . 6` → `201.6`. Run it on
`clean_html` output, and cover it with cases that must NOT change: a DOI
(`10.1038/nenergy.2016.141`), a year followed by punctuation, a JCPDS number.

Joining reduces the damage without fixing it: `5 . 6 3 . 7 4 4` still holds two
numbers with no separator. Pair the code change with a template rule that forbids
inventing a value — the model may transcribe what it sees and flag the value as
OCR-damaged, never normalise it into something plausible.

## Never corrupt the science

A regex insert that fires between `δ` and `-`, or that doubles a space inside
`mAh g^-1`, edits data rather than layout. Verify a normaliser on real note text
with two independent counters:

- **completeness**: remaining CJK↔Latin adjacencies after padding == 0;
- **over-insertion** == 0 across each damage class: a doubled space inside a
  unit (`mAh  g`), a split formula (`[A-Za-z] - [A-Za-z]`), a space before CJK
  punctuation (`\s[，。、：；！？]`), and leading/trailing whitespace.

When authorized source material is available, inspect a real note body as well as fixtures; the fixture is written by
the same hand that wrote the regex.

## Order of operations

- The short-note validator measures the **padded** string: `prepare()` runs before
  `validate_short_note()`, so the 500-character cap includes the inserted CJK/Latin
  spaces. Keep the model output well under the cap; padding can push a borderline
  short-note over the limit. (The prompt-side length budget is separate and is
  applied before padding.)
- Padding must be idempotent. Already-spaced input must not gain a second space,
  because notes are re-rendered and normalisation can run twice.

## Fixed labels stay byte-identical

When a title is a fixed label plus the item title, pad the **item title at the
assembly point**, not inside the renderer. A prefix such as `AI条目分析 - ` gets
matched byte-for-byte by scripts and tests; padding it silently breaks prefix
matching while looking like a cosmetic change.

## Updating assertions when the format changes

When changing formatting behavior, assertions pinning earlier literals (`第3页`, `体系A`, `纯文本项 1` variants) may fail. Update each expectation to the new
correct string. Do not relax the assertion to a shorter substring and do not
delete it: that hides a real regression later. Add the normaliser's own coverage
next to it — boundary cases, non-boundary cases, idempotence.

## Scope of normalization and validation

These transformations enforce layout and notation; they do not validate scientific claims. `stages.py::parse_text_packet()` accepts differing numerical/scientific notations and checks structure, source labels and figure references rather than exact numeric/text matches. Do not turn OCR normalization or a failed substring search into a new hard rejection of model content. Full analysis builds evidence from `content_list.json`; joining OCR digits in helper paths is not a guarantee that every structured block has been repaired. Keep original data and inspect ambiguous values in the PDF.

## Editing the prompt templates

- Preserve each file's existing line endings. Templates in this repo are mixed:
  some are CRLF, some LF. Rewriting a CRLF file as LF turns a four-line edit into
  a whole-file diff and buries the real change.
- Put the rule beside the JSON format rules and spell out the exact unit forms
  (`mAh g^-1`, `δ-MnO2`), plus one wrong/right pair. An abstract instruction such
  as "use consistent spacing" gets ignored.
- Sweep the whole template for examples that contradict the new rule. A rule that
  forbids super/subscripts sitting above a worked example containing `cm⁻²` and
  `MnO₂` teaches the model the opposite of what it says, and the model will follow
  the example.
- Fix the worked example in the short-note template too; a correct rule above an
  incorrectly spaced example teaches the wrong thing.
