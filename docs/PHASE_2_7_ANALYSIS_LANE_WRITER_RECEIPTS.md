# Phase 2.7: Analysis Lane Writer Receipts

Status: **safe writer implementation — no analysis runtime**. Builds on
the frozen Phase 2.6 Analysis Lane Schema Primitives
(`docs/PHASE_2_6_ANALYSIS_LANE_SCHEMA_PRIMITIVES.md`, tag
`phase-2.6-analysis-lane-schema-primitives-freeze`), and transitively
on the frozen Phase 2.5 design and Phase 2.4 portability rules. This
phase adds `src/clu_latent/analysis_writer.py`: the smallest
lock-aware, atomic, validate-before-write path for **already-produced**
analysis-lane events to enter a real `.clulatent` package. It
implements no video/audio analysis, no FFmpeg-based tracker, no OCR
runtime, no object detector, no ML model dependency, and no semantic
truth generation.

> Given a batch of raw candidate lane-event dicts a future adapter has
> already produced, how do they get safely appended to
> `tracks/<lane>.jsonl`, with the manifest kept in sync and a receipt
> recording what happened — without ever mutating a source track, an
> unrelated track, the derived search index, or a locked package?

## Scope of this phase

Implemented, in `src/clu_latent/analysis_writer.py`:

- `append_analysis_events(package_path, lane, events, *, adapter_name, tool_name, tool_version, ...)`:
  validates the full batch (via Phase 2.6's `validate_analysis_track`,
  run against the real `package_root` for full path/symlink safety),
  then atomically writes the lane track, the manifest's
  `TrackDescriptor`, and (by default) an adapter receipt.
- `append_analysis_event(...)`: singular convenience wrapper.
- `write_analysis_receipt(package_path, receipt)`: writes one
  `AnalysisAdapterReceipt` (Phase 2.6) to `receipts/analyze.jsonl` on
  its own — e.g. to record a failed adapter run that produced no
  events at all.
- `build_analysis_track_path(lane)`: the conventional
  `tracks/<lane>.jsonl` relative path for a supported lane.
- `ANALYSIS_RECEIPTS_FILE = "receipts/analyze.jsonl"` (new
  `constants.py` entry — the name Phase 2.5's design doc already
  reserved as "TBD").

Not implemented (explicitly out of scope, matching Phase 2.5/2.6):

- No adapter runtime, no FFmpeg tracker, no OCR engine, no object
  detector, no ML model dependency, no external tool invocation.
- No CLI command (`clulatent analyze ...` does not exist yet — see
  "Why the writer acquires its own operation lock" below).
- No cross-lane referential-integrity checking (whether
  `source_event_ids`/`target_event_ids` actually exist in another
  track) — still explicitly deferred by the Phase 2.5 design doc.
- No mutation of `sources/`, any other `tracks/*.jsonl`, or
  `index/search.sqlite`.

## Core principle

Unchanged from Phase 2.5/2.6: adapters produce **evidence**, not
truth. This phase adds the *validated*, *bounded*, and *receipted*
legs of "canonical means validated, bounded, receipted, reviewable,
and lockable" — *reviewable* and *lockable* already work unmodified
the moment a lane track exists, because both `review_writer.py` and
`lock.py` already operate generically over `manifest.tracks`/
`tracks/*.jsonl`/`receipts/*.jsonl` (see below). Nothing written by
this module is ever treated as more trustworthy than what Phase 2.6
already checked — `append_analysis_events` adds no new trust, only a
safe place to put already-evidenced records.

## Why raw dicts in, `EventEnvelope` on disk

Callers pass plain dicts (the same shape `analysis_lanes.validate_analysis_event`
checks), not `EventEnvelope` instances — matching Phase 2.6's own
reasoning: the first, strictest gate for untrusted adapter output
should not rely on Pydantic's lenient coercion. Once a batch passes
`validate_analysis_track` in full, each record is projected onto the
shared envelope's six fields (`id`, `type`, `t_start_ms`, `t_end_ms`,
`producer`, `confidence`, `payload`) and constructed via
`EventEnvelope.model_validate(...)`, exactly like every other track in
this project. This is a second, independent check (envelope-level
`extra="forbid"` on both `EventEnvelope` and `Producer`), so a shape
that is valid to Phase 2.6's rules but incompatible with the shared
envelope (e.g. an unrecognized key nested inside `producer`) still
fails cleanly, before anything is written.

`duration_ms`, if a candidate record supplies it, is checked by Phase
2.6 for consistency with `t_end_ms - t_start_ms` but is never itself
persisted — the shared envelope has no such field, and once
consistency is confirmed the value carries no information beyond what
`t_end_ms - t_start_ms` already encodes. Any *other* top-level field
the shared envelope does not recognize is rejected outright, not
silently dropped, since an unrecognized key most likely signals an
adapter bug.

## Why the writer acquires its own operation lock

`review_writer.append_review_event` does **not** acquire the operation
lock itself — the `clulatent review ...` CLI commands wrap it in
`security.operation_lock.operation_lock(...)`. Phase 2.7 adds no CLI
surface (there is still no adapter runtime to drive one), so
`append_analysis_events`/`write_analysis_receipt` acquire the
operation lock themselves around the full validate-then-write
sequence. This keeps calling either function directly — from a test,
a script, or eventually a real adapter — safe against two concurrent
writers without requiring a CLI wrapper first. A future
`clulatent analyze ...` command, if one is ever added, would call
these functions exactly as they are; it would not need its own extra
lock acquisition.

## Write ordering and atomicity

Up to three files are written, in a fixed order, each via the same
temp-file + fsync + `os.replace` pattern `review_writer._atomic_write`
already established:

1. `tracks/<lane>.jsonl` — full track (existing events + new,
   re-sorted by `t_start_ms`).
2. `manifest.json` — the lane's `TrackDescriptor` created or updated
   (`record_count` reflecting the new total).
3. `receipts/analyze.jsonl` — one appended `AnalysisAdapterReceipt`
   entry (only if `write_receipt=True`, the default).

If step 2 fails, step 1 is rolled back (deleted if newly created,
restored to its prior bytes otherwise). If step 3 fails, both step 2
(manifest restored to its prior bytes) and step 1 are rolled back. All
validation — schema/bounds (Phase 2.6), duplicate-id-against-existing-
track, unrecognized-top-level-field, and receipt bound checks — runs
*before* step 1, so the overwhelming majority of rejections (bad
shape, bad lane, identity claims, path escapes, ...) never touch disk
at all.

## Interaction with existing systems

### Review (Phases 2.0–2.2)

No change needed. `review_writer.py` finds a review target by
searching every non-review track in `manifest.tracks` for a matching
event id — a lane track written by this module is just another entry
in that list. Once written, every lane event is immediately a valid
`clulatent review approve/reject/correct/...` target.

### Locking (Phase 1.7.5)

No change needed. `lock.py` already globs `tracks/*.jsonl` and
`receipts/*.jsonl` generically, so `tracks/<lane>.jsonl` and
`receipts/analyze.jsonl` are covered by the next `clulatent lock` the
moment they exist — exactly the precedent Phase 2.5/2.6 already
identified. `append_analysis_events` independently refuses to write
against a package with a currently-valid integrity lock
(`lock_status() == "locked"`), matching `review_writer.py` exactly, so
a locked package's on-disk lock hash is never invalidated by a
surprise lane write.

### Portability (Phase 2.4)

No change needed. A lane track written by this module is ordinary
canonical JSONL using the existing shared envelope, and every payload
path field is checked through `security.paths.resolve_in_package`
against the real package root before being accepted — the same
relative-POSIX, symlink-safe rule every other canonical path in a
`.clulatent` package already follows.

### Receipts

`receipts/analyze.jsonl` follows the same "read full file, append one
entry, atomically rewrite" pattern `review_writer.py` uses for
`tracks/review_events.jsonl`, rather than a raw byte-append — this
avoids ever leaving a torn/partial JSON line on disk if a write is
interrupted. Each entry is one `AnalysisAdapterReceipt.to_dict()`
(Phase 2.6), with its own bound checks applied before writing (reusing
`max_analysis_label_bytes`/`max_analysis_text_bytes`/
`max_analysis_payload_bytes` from `security/limits.py` — no new
`Limits` fields were needed for this phase).

## Non-goals (restated)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No CLI command.
- No cross-lane referential-integrity enforcement.
- No mutation of source tracks, unrelated tracks, or the derived
  search index.
- No unlock/relock workflow, no `--force` past a valid integrity lock.
- No claim that a written event is "trusted" or "canonical" beyond
  what Phase 2.6's validation already established.

## Relationship to prior phases

| Phase | Relationship |
|---|---|
| 2.6 schema primitives | Every candidate event is validated with `validate_analysis_track` unchanged; this phase adds no new validation rule to that module. |
| 2.5 design | Implements the "adapter contract" and "receipts" sections' `receipts/analyze.jsonl` placeholder concretely; cross-lane referential-integrity policy remains deferred exactly as that doc left it. |
| 2.4 portability | Written tracks/receipts are ordinary portable canonical files; no export-rule change needed. |
| 2.2 review writer | `analysis_writer.py` mirrors `review_writer.py`'s atomic-write, rollback, and lock-refusal conventions; the one deliberate divergence (self-acquired operation lock) is explained above. |
| 1.7.5 locking | Existing generic `tracks/*.jsonl`/`receipts/*.jsonl` globs cover every file this phase writes with no `lock.py` change. |

## What later phases still need to build

1. A real adapter that calls `append_analysis_events` with genuinely
   detected events (still requires an actual detector/tool — not part
   of this phase or a committed dependency).
2. A `clulatent analyze ...` CLI surface, once an adapter exists to
   drive it.
3. Cross-lane referential-integrity validation (strict vs. warn),
   deferred since Phase 2.5.
4. Wiring lane-track validation into `validate.py`'s package-level
   checks (currently `validate_package` has no lane-specific logic;
   Phase 2.6's validators are not yet called from there).

## Files changed

- `src/clu_latent/constants.py`: `ANALYSIS_RECEIPTS_FILE` constant.
- `src/clu_latent/analysis_writer.py` (new): the writer described
  above.
- `tests/test_analysis_writer.py` (new): 32 tests.
- `docs/PHASE_2_7_ANALYSIS_LANE_WRITER_RECEIPTS.md` (new, this
  document).
- `README.md`: short Phase 2.7 roadmap note.

## Summary

Phase 2.7 gives a future analysis-lane adapter (or, today, a test or a
script) the smallest safe path to turn already-validated Phase 2.6
event dicts into real, atomic, lock-respecting writes: one lane track,
one manifest update, and one receipt, all-or-nothing, never touching a
source track, an unrelated track, or the derived search index. No
analysis intelligence is added — only the safe door for future
intelligence to write through.
