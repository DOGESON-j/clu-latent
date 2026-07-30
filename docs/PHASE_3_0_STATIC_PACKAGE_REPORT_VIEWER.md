# Phase 3.0 — Static Package Report Viewer

Base: Phase 2.25 Public Repo Owner Decision is frozen
(tag `phase-2.25-public-repo-owner-decision-freeze`).

This phase adds a static, local-first HTML report generator for
`.clulatent` packages — the first major usability jump after the Phase 2
trust/adapter/public-readiness work. It makes a package understandable to
a human without requiring Studio UI, a web server, cloud services, ML,
CLUBIN, or a plugin runtime.

## Core principle (unchanged)

CLULatent is a **local-first media evidence package format**. Adapter
outputs are **evidence, not truth**. Generated does not mean canonical.
**Canonical** means validated, bounded, receipted, reviewable, and
lockable. The report **displays** evidence that already exists in the
package. It does not create truth, verify the original media, re-run any
adapter, or claim an adapter understood the media.

## What this phase adds

- `src/clu_latent/report.py` — `generate_report(package_path) ->
  ReportResult` (`.html: str`, `.warnings: list[str]`). Read-only: reads
  `manifest.json`, `tracks/*.jsonl`, `receipts/*.jsonl`, and lock status
  via the existing `validate_package`, `lock_status`,
  `resolve_package_review_states`/`summarize_review_states`, and
  `read_track_file` helpers. Never mutates the package.
- `clulatent report <package> --output report.html [--force]` — new CLI
  command in `src/clu_latent/cli.py`.
- `tests/test_report.py`.
- This file, and a Phase 3.0 roadmap bullet in `README.md`.

## What this phase does NOT do

- No publishing, pushing, remote, GitHub release, or PyPI upload.
- No package-version change (`0.1.0` unchanged); no license change.
- No ML dependency, no semantic understanding, no object recognition/OCR.
- No dynamic plugin loading, Studio UI, or CLUBIN.
- No web server — the report is a single static HTML file opened
  directly in a browser (`file://`), nothing is served over a socket.
- No network access — no external `<script>`, stylesheet, font, image,
  or CDN reference anywhere in the generated document.
- No mutation of the package: no index, receipt, or lock file is written
  or changed inside `package_path`; the command only ever writes
  `--output`.
- No broad refactor of existing modules.

## Command

```
clulatent report <package_path> --output report.html
```

- `--output` / `-o` (required): where to write the HTML file.
- `--force`: overwrite `--output` if it already exists (same convention
  as `analysis generate-ffmpeg-visual-change-adapter-result`).
- Exits 1 with a clean error (never a traceback) if the package cannot
  be read at all (missing path, missing/invalid `manifest.json`),
  `--output` already exists without `--force`, or `--output`'s parent
  directory does not exist.
- A package that exists and has a readable manifest but otherwise fails
  validation (bad hash, missing track file, ...) still produces a full
  report — the Validation section simply shows **FAIL** with the
  specific errors, bounded and escaped.

## Report sections

The generated HTML has eight sections, in order:

1. **Package summary** — package path, package id, `clulatent_version`,
   status, source summary, track count, analysis track count, receipt
   count, lock status, validation status.
2. **Trust status** — the evidence-not-truth statement, validation and
   lock status, and warnings if the package is unlocked, validation
   failed, or as a standing reminder that the report reflects only local
   package contents.
3. **Timeline overview** — a chronological table merged across all
   tracks: track name, event id, type, start/end ms, confidence,
   producer name, and a bounded, safe payload summary (never a raw
   payload dump; truncated at a fixed character limit, and the whole
   table is capped at a fixed event count with a note if truncated).
4. **Track overview** — every track declared in the manifest, its
   category (source / review / analysis), event count, and file.
5. **Adapter evidence** — for each analysis-lane track: event count,
   conservative per-type labels (never an identity/understanding claim),
   confidence min/avg/max, matching `receipts/analyze.jsonl` entries
   (adapter/tool/model metadata), and any adapter warnings.
6. **Receipts** — ingest receipts (`receipts/ingest.jsonl`) and analyze
   receipts (`receipts/analyze.jsonl`), each as its own table.
7. **Review** — if `tracks/review_events.jsonl` has events: counts by
   kind (approvals, rejections, corrections, overrides, notes, status
   changes, session summaries) plus the current effective review state
   via the existing read-only resolver. If there are no review events:
   "No review events found."
8. **Validation** — pass/fail, error count, warning count, and the
   (bounded, escaped) messages themselves.

## Safety

- Every piece of package-originated text is stripped of control
  characters (mirroring `security/console.py`'s terminal-safety pattern)
  and then `html.escape(..., quote=True)`'d before being written into
  the document — no raw event payload, manifest field, or receipt field
  is ever injected as raw HTML.
- No `<script>` tag anywhere in the module; CSS is a small inline
  `<style>` block using only system font stacks — no `@import`, no
  external `url()`, no CDN, no remote font.
- Large payloads and long timelines are bounded
  (`DEFAULT_MAX_TIMELINE_EVENTS`, `DEFAULT_MAX_PAYLOAD_CHARS`,
  `DEFAULT_MAX_MESSAGE_CHARS`) so a pathological package cannot produce
  an unbounded report.
- Malformed tracks and missing optional files degrade to a warning in
  the Trust section, not a crash — only a fundamentally unreadable
  package (no manifest) raises a clean error.
- Path handling reuses `security.paths.resolve_in_package` (the same
  containment/symlink-safety check used everywhere else in the
  codebase) for every manifest-declared relative path.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new `tests/test_report.py`
suite, with no regression in existing adapter/demo/validation/locking/
review tests.
