# `ai-note2` verification and recovery

Use this procedure for the current three-chain `zot ai-note2` workflow. The separate [AI Note verification](ai-note-verification.md) reference covers `ai-note`, which has different inputs, image handling and writes.

## Prepare and inspect inputs

1. Run `uv run zot ai-note2 ITEMKEY --dry-run` from the repository root. Confirm the selected AI source note, attachment keys, role and identity status, block count and budget. A cache miss can call MinerU and write its parse cache; this preview does not call the analysis model or write Zotero.
2. Only `journalArticle` is supported. No recognized source note returns `skipped`; multiple notes require `--source-note NOTEKEY`. Use repeatable `--attachment KEY` options when the requested input is narrower than all available PDFs. PDF roles are heuristics and identity can remain unconfirmed.
3. For a local service test, use `uv run zot ai-note2 ITEMKEY --local-only --progress-lines`. An existing verified publication requires `--force` even for a new local run. Local-only still reserves a run and writes its ledger and local artifacts; it does not create a Zotero writer. Do not add `--force` to bypass a pending or uncertain run.
4. Preserve the configured primary and fallback models. A successful short probe proves only short-request connectivity. `timeout_seconds` and SDK retries apply per request; generation, bounded structural repairs, fallback routes and model verification can accumulate beyond that timeout. `max_tokens=0` omits the request parameter; the provider controls the output limit.

## Examine the actual artifacts

- `analysis.json` is the authoritative structured content; `object-index.json` and Markdown/HTML are derived. Each file is staged and replaced individually; the set is not one atomic transaction. `run.json` carries the verification verdicts and run metadata. New runs do not create an SVG.
- `note.md` and `note.html` place directly registered materials, author reasoning and reviews near claims. Unlinked materials and reviews that cannot be placed under a claim remain visible in the pending section. The blue heading style can be stripped by the viewer.
- The generation prompt asks for Chinese writing using ASD-STE100 clarity principles; it does not establish formal compliance with that English controlled language. Prompt instructions are not deterministic guarantees.
- Check schema/reference validity, frozen source locations and model source verdicts separately. Verification uses `source_set_id`, `attachment_key` and `block_id`; it accepts a single JSON code fence but still requires complete unique object verdicts and registered sources. A `支持` verdict is a model judgment about source correspondence, not proof of a scientific conclusion. `部分支持`, `不支持` and `无法核对` remain visible and do not automatically block publishing.
- No image pixels are sent to either stage. Figure numbers and captions do not prove that plots, data points, colours or micrographs were examined. Spot-check sample, units and test conditions against the source, distinguish author inference from observed or calculated results, and examine whether a hypothesized mechanism is being used as its own evidence.
- Once a generated document passes structure validation, `diagnostic/generated.json` is saved under the run's state directory before the second stage; a returned verification response is saved as `diagnostic/verification-response.txt` before parsing. These files contain paper/model content and are restricted to the current user. They do not capture a response that never arrives. Inspect them offline when available; rendering or parsing a retained response is a replay, not a fresh successful service run.

## Publish and reconcile

Only publish when the user has authorized the Zotero write and the output has been inspected. `uv run zot ai-note2 ITEMKEY --source-note NOTEKEY` creates a child note, uploads `<ITEMKEY>-<run>-analysis.json` as a parent attachment, and adds the configured evidence tag. `--force` appends a run; it does not replace an earlier note.

Publication is sequential, not atomic. `verified` means the note identity/run marker, JSON attachment metadata and downloaded file SHA-256, and parent tag were read back. Check Zotero's own sync before expecting remote writes in the local SQLite view.

If a write is uncertain or a later step fails after an earlier write, keep the ledger and known keys; do not run a new generation with `--force` or a new idempotency key. Use `uv run zot ai-note2 ITEMKEY --reconcile`. Unknown note creation requires a unique remote note matching the parent and run marker. Unknown attachment upload requires a unique matching filename and downloaded bytes whose SHA-256 matches the saved local JSON. Zero or multiple matches, a missing file, failed download or digest mismatch remain unresolved and require inspection; do not blindly create a duplicate. If upload was explicitly rejected, recovery can retry the missing attachment using the unchanged saved JSON. Report each confirmed remote write separately.

## Report

Report the actual run status and artifact paths, input roles and their uncertainty, model verdicts separately from manual findings, whether graphics were examined, and the note/attachment/tag readback level. A passing mocked test, successful parsing of retained output or a green schema is not evidence of fresh end-to-end model success or an actual Zotero upload.
