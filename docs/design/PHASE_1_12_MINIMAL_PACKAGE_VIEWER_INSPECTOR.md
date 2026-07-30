# CLULatent Phase 1.12: Minimal Package Viewer / Inspector

**Status**: Design-only. No implementation. No UI code. No frontend
dependencies. No Electron/React/Flask/FastAPI/web-server code. No runtime
behavior, no ML, no CLUBIN. No change to validation, locking, or package
schemas. Views, safety principles, and layout only.

**Motivation**: After Phases 1.7–1.11, a `.clulatent` package is
structured, validated, lockable, receipted, and formally specified
(`docs/spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md`). What is still missing is a
**human-facing way to look at one**. Today a person inspects a package by
running individual CLI commands (`inspect`, `timeline`, `query`,
`validate`, `verify-lock`, `lock-status`) and mentally stitching the
output together. Phase 1.12 designs the first **minimal, read-only
viewer/inspector** that presents that same information as a coherent set
of views — so a human can open a package and understand its contents,
provenance, validation state, and trust (lock) state **without modifying
it**.

The guiding principle, inherited from every prior phase: **the viewer
observes, it never asserts.** It reads canonical data, renders it safely,
and reports what the existing read-only commands already compute. It adds
no new authority over the package and — critically — it prepares the
ground for a future review/correction UI (Phase 1.9's `review_events`)
**without implementing any editing in this phase**.

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Relationship to Existing CLI Commands](#2-relationship-to-existing-cli-commands)
3. [Views / Screens](#3-views--screens)
4. [Safety Principles](#4-safety-principles)
5. [Package Modification Rules](#5-package-modification-rules)
6. [Possible Implementation Approaches](#6-possible-implementation-approaches)
7. [Non-Goals](#7-non-goals)
8. [Relationship to Phase 2](#8-relationship-to-phase-2)
9. [Worked Examples](#9-worked-examples)
10. [Summary](#10-summary-what-phase-112-delivers)

---

## 1. Purpose

The Phase 1.12 viewer exists to:

- **Local-first package inspection.** Everything happens on the local
  filesystem against a plain `.clulatent` directory. No network, no
  server, no account.
- **Read-only by default.** Opening, browsing, and inspecting a package
  never writes to it. Read-only is the *default and only* mode in this
  phase (see [§5](#5-package-modification-rules)).
- **Explain package contents.** Turn the manifest, tracks, media,
  receipts, and lock files into human-legible views — what is in here,
  who produced it, when, and how much of it there is.
- **Help humans verify package trust state.** Surface validation results
  and lock state prominently so a person can answer "is this package
  internally consistent, and has it changed since it was locked?" at a
  glance.
- **Prepare for future review/correction UI — but do not implement review
  editing yet.** The layout reserves space for review state (Phase 1.9)
  and caption evidence (Phase 1.10) so the eventual review editor is an
  additive change, not a redesign. This phase renders those as
  *read-only, mostly-empty-today* panels; it never writes
  `review_events.jsonl`.

Non-purpose (stated up front to bound scope): the viewer is **not** a
correctness oracle. It reports what `validate` and `verify-lock` compute;
it does not itself decide whether perception data is *accurate* — that is
human review's job, in a later phase.

---

## 2. Relationship to Existing CLI Commands

The viewer is a **presentation layer over primitives that already exist**.
It introduces no new package-reading logic; every view maps onto a
read-only capability already implemented and tested:

| View / data | Existing primitive (read-only) |
|---|---|
| Package overview, source summary | `inspect` (`Manifest`, source hash check) |
| Timeline | `timeline` (merged, sorted `EventEnvelope` stream) |
| Track browser / event detail | `Manifest.tracks` + `tracks.read_track_file` |
| Search/query | `query` (derived `index/search.sqlite`, opened read-only) |
| Validation panel | `validate` (`ValidationReport`) |
| Lock panel | `verify-lock` / `lock-status` (`LockVerifyReport`, `LockStatus`) |
| Receipts panel | `receipts/ingest.jsonl` (bounded JSONL read) |

Consequence: this design does not require any new parsing, resolution, or
validation code. A conformant viewer calls the same functions the CLI
does, and inherits their existing safety guarantees
(`resolve_in_package`, `iter_jsonl_bounded`, `open_readonly`,
`safe_console_text`). If a future implementation needs a shared read-only
"package model" object, that is a refactor of existing readers, not new
behavior — and is out of scope for this design doc.

---

## 3. Views / Screens

The viewer is organized as a set of views. A given implementation
([§6](#6-possible-implementation-approaches)) may present them as TUI
panes, sections of a static HTML report, or (later) app screens — the
*content* below is normative; the *presentation* is not.

### 3.1 Package Overview

The landing view. Answers "what am I looking at, and can I trust it?"

- Package path / name.
- Schema version (`clulatent_version`) and, per track, `schema_id` /
  `schema_version`.
- Source file summary: filename, container format, dimensions, codecs,
  `has_audio`, source `sha256` + hash-check result
  (`verified` / `MISMATCH` / `missing`).
- Duration (`source.duration_ms`, rendered as a timecode).
- Track counts: per-track `record_count`, and which tracks are present.
- **Validation status** (pass / fail + error/warning counts), from the
  validation panel.
- **Lock status** (`unlocked` / `locked` / `lock-invalid` /
  `lock-partial`), from the lock panel.
- Receipt summary: number of operations, latest operation + status,
  whether any receipt carried warnings/errors.

### 3.2 Manifest / Source Summary

The full, structured `manifest.json`, rendered as labeled fields (not raw
JSON, though a "raw" toggle is allowed). Shows every `SourceInfo` field,
`timebase`, `media.keyframes` bookkeeping, `index` pointer (with its
`canonical: false` clearly shown), and `receipts` pointer. This view makes
the canonical-vs-derived distinction visible: derived artifacts
(index, keyframe media) are labeled as such.

### 3.3 Timeline View

A chronological, merged view of all track events on a single media
timeline (the viewer form of the `timeline` command).

- A **time ruler** spanning `[0, source.duration_ms]`.
- Lanes/rows for each event source:
  - keyframes (`media/keyframes/*` markers),
  - audio events (`silence` / `non_silent_audio` / `audio_track_present`),
  - speech events (`speech_activity` / `speech_segment`),
  - **(reserved)** caption events (Phase 1.10),
  - **(reserved)** speaker events (Phase 1.8),
  - **(reserved)** review events (Phase 1.9).
- **Overlapping events** shown explicitly (stacked or offset), not
  collapsed — e.g. a `speech_segment` and a future overlapping
  `caption_segment` covering the same span are both visible.
- Selecting an event opens its [Event Detail Panel](#36-event-detail-panel).

Reserved lanes render as present-but-empty on a Phase 1 package (no such
track file) — never as an error.

### 3.4 Track Browser

A list of all tracks, implemented and reserved.

- Track name and `file`.
- **Implemented vs reserved/design-only** clearly flagged (keyframes /
  audio_events / speech_events = implemented; semantic_events = reserved;
  speaker_events / review_events / caption_events = design-only,
  typically absent).
- Event count (`record_count`, cross-checked against the actual file).
- `schema_id` / `schema_version` when available (from the
  `TrackDescriptor`).
- Per-track validation errors / warnings (from the validation panel).
- **JSONL line references** — each event traceable to its
  `file:lineno`, so a human can find the exact source line (matching how
  `validate`/`read_track_file` already report `path:lineno`).

### 3.5 Media / Keyframe Preview

A read-only gallery of extracted keyframes (`media/keyframes/*.jpg`),
each tied to its `keyframe` event's `frame_index` and timestamp. Images
are resolved via `resolve_in_package` (no path escape) and are treated as
**derived** — the view labels them as rebuildable, not canonical. No
image is executed or interpreted; only displayed (or, in a text/TUI
implementation, listed by path + dimensions).

### 3.6 Event Detail Panel

The full `EventEnvelope` for a selected event:

- `id`, `type`, `t_start_ms`, `t_end_ms` (with a timecode rendering).
- `producer` (`name` + `version`).
- `confidence` (or "not stated" when `null`).
- `payload` (structured, per-`type`).
- **(reserved)** `payload.source_event_ids` /
  `payload.supersedes_event_ids` when present (Phase 1.9/1.10) — rendered
  as clickable links to the referenced events where they resolve.
- A **raw JSON view** of the exact line (safely rendered).
- Per-event validation status (does it parse as a valid envelope; is it
  within duration bounds; does it point at events that exist).

### 3.7 Speech / Caption Text Panel

A text-oriented view of spoken/caption content: `speech_segment` text
(with detected `language`), and **(reserved)** `caption_segment` text
(with `source_format`, `language`, `is_translation`). Presented side by
side when both exist for overlapping spans, so a human can compare Whisper
output against imported captions **without the viewer merging or
preferring either** (consistent with Phase 1.10's no-silent-merge rule).
All text passes through `safe_console_text` before display.

### 3.8 Receipts / Logs Panel

The append-only `receipts/ingest.jsonl`, one row per operation:

- Ingest (and future) receipts: `operation`, `status`, `timestamp`.
- **Warnings vs errors** shown separately (the fatal/non-fatal split) —
  e.g. clamp/skip warnings for out-of-duration Whisper segments surface
  here as warnings, not errors.
- Producer / tool metadata (`tool.name` / `tool.version`, resolved
  `tool_path`).
- Subprocess trail where present (`timed_out`, `*_truncated`,
  `stderr_tail`, `limits_snapshot`).
- A visible reminder that **model/tool outputs are untrusted** — a
  receipt records *what happened*, not *that the result is correct*.

### 3.9 Validation Panel

The current `validate` result for the package:

- Overall pass/fail (`ValidationReport.valid`) + error/warning lists.
- Specific failure classes surfaced legibly: duration-bound failures,
  malformed/oversized JSONL, missing track/source files, path/containment
  violations, manifest schema failures.
- Lock mismatch noted here when relevant (cross-referenced with the lock
  panel).
- **Strict / future validation notes**: the panel indicates which
  [validation tiers](../spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md#11-validation-tiers)
  are actually being run (today: Tier 1 only) and flags checks that are
  *reserved for future tiers* (per-type payload schema, strict extra-file
  enforcement, review-aware rules) as "not yet enforced" rather than
  silently passing.

### 3.10 Lock Status Panel

The integrity/operation lock state (from `verify-lock` / `lock-status`):

- State: `unlocked` / `locked` / `lock-partial` / `lock-invalid`.
- `package.lock.json` summary: `lock_version`, `created_at`, policy
  (`include_index`, `strict_extra_files`), file count.
- `package.lock.sha256` status: whether the lock manifest itself verifies
  (a lock-hash mismatch is its own distinct signal).
- Changed / missing / extra files (from `LockVerifyReport`), with `extra`
  clearly explained as "appeared after lock time" rather than an error in
  itself.
- **Whether a re-lock would require `--force`** — i.e. the package is
  already locked, so `clulatent lock` would refuse without `--force`
  (Phase 1.7.5 re-lock guard). The viewer *states* this; it never
  performs it.
- Operation lock status if a `package.operation.lock.json` /
  `<output>.oplock.json` is present (pid/host/operation) — shown as "a
  write may be in progress or a stale lock exists", explicitly distinct
  from the integrity lock.

---

## 4. Safety Principles

The viewer inherits and must uphold every existing package-reading safety
guarantee. These are hard requirements, not suggestions:

- **Do not trust package contents.** manifest, tracks, receipts, and lock
  files are all untrusted input. Parse defensively; a malformed package
  must produce a clear error state in the relevant view, never a crash or
  a security issue.
- **Never execute package data.** No string in a package is ever eval'd,
  shell-interpreted, or treated as code. Text is data.
- **Never follow unsafe paths.** Every package-relative path
  (`source.stored_path`, `tracks[].file`, `payload.path`, keyframe images,
  index, receipts) is resolved via `resolve_in_package` — absolute paths,
  `..` traversal, and symlink escapes are rejected before any file is
  opened.
- **Render all text safely.** Every package-derived string reaching a
  terminal or report passes through `safe_console_text` (control-sequence
  / terminal-injection safe). Free-text fields stay length-bounded.
- **Bound JSONL / file reads.** Tracks and receipts are read via
  `iter_jsonl_bounded` (line + record caps); the manifest via a bounded
  read. The viewer never slurps an unbounded file into memory.
- **Open SQLite read-only only.** `index/search.sqlite` is opened via the
  existing read-only path (`open_readonly`) — the viewer never opens the
  index writable, and never treats it as authority over the canonical
  tracks.
- **Do not modify packages by default.** In Phase 1.12, *by default* is
  *always*: the viewer has no write path at all (see
  [§5](#5-package-modification-rules)).
- **Do not auto-fetch remote assets.** No URLs in package data are
  fetched. No network access. Everything rendered comes from the local
  package.
- **Do not auto-run ML models.** Opening a package never triggers VAD,
  Whisper, diarization, or any inference. The viewer displays existing
  track output; it does not produce new perception data.
- **Do not auto-unlock or re-lock.** The viewer never creates, replaces,
  or removes any lock file. It only *reports* lock state.

---

## 5. Package Modification Rules

- **Phase 1.12 viewer is strictly read-only.** It has no command,
  toggle, or mode that writes to a package. Full stop.
- **A future review editor** (a later phase, building on Phase 1.9) may
  write `tracks/review_events.jsonl` — additive-only, never mutating model
  tracks. That editor is explicitly **not part of this phase**; this
  design only reserves the UI space for it.
- **Writes must use operation locks.** When a future writer is built, it
  must acquire the operation lock (cooperative write guard) around its
  write, exactly like `ingest`/`lock`/`reindex` do today — so two writers
  cannot race on the same package.
- **Writes to locked packages require a copy or an explicit new lock
  cycle.** Per `docs/LOCKING.md`, there is no `unlock`. A future editor
  targeting a locked package must either work on a copy with `lock/`
  removed, or accept that the review track appears as an `extra` file
  against the old lock and then re-lock deliberately.
- **No silent lock replacement.** Re-locking always requires the explicit
  `--force` (Phase 1.7.5 re-lock guard). Neither the viewer nor any future
  editor may replace a lock silently.

---

## 6. Possible Implementation Approaches

Deliberately **not chosen** in this phase — the design fixes the *content
and safety contract*, not the technology. Candidate approaches, roughly in
increasing weight:

- **CLI / TUI inspector.** A richer terminal experience over the existing
  commands (e.g. a paged, navigable view). Zero new heavy dependencies;
  closest to what exists today. Strong default for "boring and local".
- **Local static HTML report generator.** A command that emits a
  single self-contained, offline HTML file summarizing a package (no
  server, no live JS framework, no network). Human-shareable as a file,
  still read-only.
- **Local desktop app (later).** A packaged desktop viewer. Heavier;
  deferred well past this phase.
- **Lightweight local web UI (later).** A localhost-only viewer. Explicitly
  **not** in scope now (no web-server code this phase), noted only so the
  layout doesn't preclude it.

**Design bias**: keep the *first* implementation boring, local, and
dependency-light — a TUI or static-HTML report over the primitives in
[§2](#2-relationship-to-existing-cli-commands) — precisely because it must
inherit the existing safety guarantees with minimal new surface.

---

## 7. Non-Goals

Explicitly **out of scope** for Phase 1.12:

- **No editing.** The viewer changes nothing in a package.
- **No review/correction UI yet.** Reviewing, approving, correcting, or
  rejecting events (Phase 1.9) is not implemented here — only *displayed*
  read-only once such data exists.
- **No semantic interpretation.** The viewer shows perception events; it
  does not infer meaning, roles, intent, or identity from them.
- **No cloud sharing.** No upload, no hosted packages, no share links.
- **No collaboration.** No multi-user state, presence, comments, or
  merge.
- **No login / accounts.** No authentication or identity system.
- **No model inference.** Opening a package never runs ML.
- **No real-time video processing.** No live decode/transcode/analysis
  pipeline; the viewer reads already-produced package data.
- **No CLUBIN.** No compiled binary format, no Rust runtime.
- **No production "Studio" app.** This is a minimal inspector, not a
  finished product; scope is deliberately small.

---

## 8. Relationship to Phase 2

Phase 1.12 builds the **human-facing inspection layer** that Phase 2
depends on. Phase 2 is where reviewed, corrected packages become real
(Phase 1.9 implemented) — and a human cannot responsibly review what they
cannot first *see*.

Concretely, this phase:

- Gives humans a coherent way to **understand package state before
  review/correction exists** — so when the review editor arrives, users
  already have a trusted lens on the underlying evidence.
- **Reserves the review/caption/speaker surfaces** in the timeline, track
  browser, event detail, and text panels, so adding the Phase 1.9 review
  editor (and Phase 1.10 caption import, Phase 1.8 speakers) is an
  *additive* UI change rather than a rebuild.
- Establishes the **read-only-first, safety-inheriting** posture that the
  future writer must not violate: even once editing exists, inspection
  stays non-destructive, operation-locked, and lock-aware.

Per the schema freeze draft's
[Phase 2 readiness list](../spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md#15-phase-2-readiness),
this is item 5 (the "minimal inspector/viewer"): not strictly required to
*freeze* the schema, but required to *use* the review layer meaningfully.

---

## 9. Worked Examples

### 1. Opening a package summary

A user opens `clip.clulatent`. The **Package Overview** shows:
`clulatent_version 0.1.0`; source `clip.mp4`, 1280x720, 30fps, has audio;
duration `00:09:58`; source hash `verified`; tracks `keyframes` (598),
`audio_events` (44), `speech_events` (absent — not transcribed);
validation `PASS (0 errors, 1 warning)`; lock `locked`; receipts `1
operation (ingest, success, 1 warning)`. From here every other view is one
navigation away.

### 2. Inspecting a speech event

The user selects `ts_000008` in the **Track Browser** →
**Event Detail Panel**:

```json
{
  "id": "ts_000008",
  "type": "speech_segment",
  "t_start_ms": 12000,
  "t_end_ms": 15000,
  "producer": {"name": "faster-whisper", "version": "1.0.3"},
  "confidence": 0.87,
  "payload": {"text": "the mitochondria is the powerhouse", "language": "en"}
}
```

Panel shows: timecode `00:12.000 → 00:15.000`; producer
`faster-whisper 1.0.3`; confidence `0.87`; envelope-valid; within duration
bounds; source line `tracks/speech_events.jsonl:8`. Text is rendered
through `safe_console_text`.

### 3. Viewing a lock mismatch

The user edited a track after locking. The **Lock Status Panel** shows
state `lock-invalid`; `package.lock.sha256` verifies (the lock manifest
itself is intact), but `changed: ["tracks/speech_events.jsonl"]`. The
**Package Overview** lock badge turns red, and the **Validation Panel**
cross-references it. The viewer *reports* the mismatch and notes that
re-locking would require `--force` — it does not offer to re-lock.

### 4. Seeing a clamped Whisper warning in receipts

The **Receipts Panel** shows the ingest receipt with `status: success` and
one **warning** (not an error): `"speech_segment[4] clamped: t_end_ms
exceeded source duration by 240ms"`. The panel groups it under warnings,
attributes it to `faster-whisper`, and displays the standing reminder that
model output is untrusted until reviewed. The operation still succeeded.

### 5. Comparing Whisper speech vs future captions

On a package that has both `speech_events.jsonl` and (Phase 1.10, once it
exists) `caption_events.jsonl`, the **Speech / Caption Text Panel** places
`ts_000008` ("the mitochondria is the powerhouse", Whisper) next to
`cap_000000` ("the mitochondria is the powerhouse", imported SRT) for the
same span. Both are shown as independent evidence; the viewer marks that
they cover the same time range but does **not** merge, rank, or reconcile
them.

### 6. Seeing future review status

On a package that (Phase 1.9, once implemented) has
`review_events.jsonl`, the **Event Detail Panel** for `ts_000008` shows a
read-only "Reviewed: corrected" badge, derived from a `review_correction`
(`rv_000003`) whose `source_event_ids` includes `ts_000008`, with a link
to that review event and its corrected text. The viewer **displays** the
resolved review state; it provides no control to create, edit, or
supersede it in this phase.

---

## 10. Summary: What Phase 1.12 Delivers

1. **A read-only viewer/inspector design** — local-first, non-destructive,
   presenting a `.clulatent` package as coherent views instead of scattered
   CLI output.
2. **Eleven views**: package overview, manifest/source summary, timeline,
   track browser, media/keyframe preview, event detail, speech/caption
   text, receipts/logs, validation, lock status, and search/query.
3. **A presentation layer over existing primitives** (`inspect`,
   `timeline`, `query`, `validate`, `verify-lock`, `lock-status`) — no new
   package-reading logic, inheriting every existing safety guarantee.
4. **A hard safety contract**: untrusted contents, no execution, safe path
   resolution, bounded reads, read-only SQLite, safe text rendering, no
   network, no ML, no lock mutation.
5. **Read-only-only modification rules** for this phase, with reserved
   (not implemented) space for a future operation-locked, additive review
   editor.
6. **Deferred implementation choice** — CLI/TUI or static-HTML first,
   deliberately boring and local; desktop/web left open, not built.
7. **Reserved surfaces** for Phase 1.8 speakers, 1.9 review, and 1.10
   captions, so the eventual review UI is additive.
8. **Design-only**: no UI code, no dependencies, no runtime behavior, no
   schema/validation/locking change.
