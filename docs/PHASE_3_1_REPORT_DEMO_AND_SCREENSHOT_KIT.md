# Phase 3.1 — Report Demo and Screenshot Kit

Base: Phase 3.0 Static Package Report Viewer is frozen
(tag `phase-3.0-static-package-report-viewer-freeze`).

Status: planning / requirements gathering. This document records
requirements as they are decided, ahead of implementation.

## Screenshot kit: PASS vs FAIL demo reports

The screenshot kit must distinguish between two kinds of demo report:

1. **Clean PASS demo report** — generated from a package like
   `sample.clulatent` that validates cleanly. This is the
   **recommended public screenshot** — it is what should represent
   CLULatent in READMEs, docs, and any public-facing materials.
2. **Optional validation-failure demo report** — generated from a
   package that intentionally or historically contains invalid data.
   This FAIL report is not the public default. It may be documented
   separately as a **trust-model example**, demonstrating that
   CLULatent surfaces invalid package data instead of hiding it
   (consistent with the "evidence, not truth" / validated-bounded-
   receipted-reviewable-lockable trust model from Phase 2/3.0).

## Generated artifacts are local-only

Demo packages and demo reports produced while building or exercising
the screenshot kit are local artifacts, not repository content:

- `sample.clulatent`
- `sample-transcribed.clulatent`
- `report.html`
- `report-transcribed.html`

None of the above must be committed; they are not committed today.
`.gitignore` covers all of them
(`*.clulatent/`, `sample*.clulatent/`, and `report*.html`). Any
`report.html`/`report-transcribed.html` that had previously been
committed by mistake have been untracked (`git rm --cached`) while
keeping the local files in place.
