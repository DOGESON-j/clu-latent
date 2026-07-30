# Phase 2.9: Analysis Lane Validation Integration

Status: **validation wiring only — no analysis runtime**. Builds on the
frozen Phase 2.8 Analysis Lane CLI Commands
(`docs/PHASE_2_8_ANALYSIS_LANE_CLI_COMMANDS.md`, tag
`phase-2.8-analysis-lane-cli-commands-freeze`), and transitively on the
frozen Phase 2.7 writer receipts, Phase 2.6 schema primitives, and
Phase 2.4 portability rules. This phase makes `clulatent validate
<package>` (and `validate_package`, the function it calls) actually
run Phase 2.6's analysis-lane validators against any supported lane
track it finds, and run a new conservative shape check against
`receipts/analyze.jsonl` if that file exists. It implements no
video/audio analysis, no FFmpeg-based tracker, no OCR runtime, no
object detector, no ML model dependency, no semantic truth generation,
and no adapter execution runtime.

> Phase 2.6 wrote the lane validators. Phase 2.7 wrote a safe path to
> get validated events onto disk. Phase 2.8 gave a human a door to
> write them by hand. Until now, nothing forced `clulatent validate`
> to actually *look* at a lane track once it existed on disk — a
> package with a hand-edited or bug-produced bad lane record could
> still report `PASS`. This phase closes that gap.

## Scope of this phase

Implemented, in `src/clu_latent/validate.py` and `src/clu_latent/analysis_lanes.py`:

- Inside `validate_package`'s existing per-track loop, any track whose
  `manifest.tracks[].name` is one of the fourteen Phase 2.5 lane names
  (`analysis_lanes.SUPPORTED_ANALYSIS_LANES`) is additionally validated
  with Phase 2.6's `validate_analysis_track`, run against the real
  `package_path` (full path/symlink safety, not just the lexical
  check). This runs on the same raw record dicts already parsed by the
  existing bounded JSONL reader for the generic `EventEnvelope` check —
  no second read of the file.
- Two new functions in `analysis_lanes.py`: `validate_analysis_receipt`
  (one entry) and `validate_analysis_receipts` (a whole batch),
  following the same non-raising `(errors, warnings)` convention as
  `validate_analysis_event`/`validate_analysis_track`. `validate_package`
  calls `validate_analysis_receipts` against `receipts/analyze.jsonl`
  if that file exists and is non-empty.
- No new CLI command: `clulatent validate <package>` already prints
  `report.errors`/`report.warnings` and exits 1 on any error, so a
  lane-specific or receipt-specific validation failure surfaces
  automatically, with no change needed to `cli.py`'s `validate` command
  itself.

Not implemented (explicitly out of scope, matching Phase 2.5–2.8):

- No adapter runtime, no FFmpeg tracker, no OCR engine, no object
  detector, no ML model dependency, no external tool invocation.
- No cross-lane referential-integrity checking (whether
  `source_event_ids`/`target_event_ids` in a `cross_lane_link_events`
  payload actually exist in another track) — still explicitly deferred,
  exactly as Phase 2.5 left it.
- No change to `lock.py`, `reindex.py`, or the derived search index.
- No requirement that every supported lane exist in a package, and no
  requirement that a lane have a corresponding receipt.

## Core principle (restated)

Analysis lanes are evidence tracks, not truth. This phase adds no new
trust to anything already on disk — it only makes sure `clulatent
validate` actually *checks* the same rules Phase 2.6 always defined,
instead of silently skipping them for lane tracks the way every prior
phase did. *Detected does not mean trusted. Generated does not mean
canonical. Canonical means validated, bounded, receipted, reviewable,
and lockable.* Phase 2.9 is what makes "validated" actually apply at
the package-validation layer, not just at write time.

## Why lane validation runs on raw records, not `EventEnvelope`s

`validate_package`'s existing per-track loop already builds `EventEnvelope`
instances via Pydantic's lenient `model_validate` for the generic
shape check every track gets. Phase 2.6's `validate_analysis_event`/
`validate_analysis_track` are deliberately written against raw,
untrusted dicts instead (see `analysis_lanes.py`'s own module
docstring: `EventEnvelope`'s lenient coercion would silently accept a
numeric string like `"1000"` for `t_start_ms`, defeating a stricter
gate). This phase reuses the same raw records the JSONL reader already
produced — collected once per track into a list alongside the existing
`EventEnvelope` construction — and runs the lane validator against
that list, independent of whether each individual record also passed
(or failed) the generic envelope check. This means a lane-specific
violation like an identity claim in an `object_proposal_events` payload
is reported even for a record that is otherwise a perfectly
shape-valid `EventEnvelope` — the generic check has no way to know
"no identity claims" is a rule at all; only the lane-specific check
does.

## Manifest/track descriptor behavior

No new rule was added for analysis lane `TrackDescriptor`s beyond what
every track (analysis lane or not) already gets from
`validate_package`: `track.file` must be a relative POSIX-style path
(lexical check), and must resolve inside the package root with no
symlink escape (`security.paths.resolve_in_package`, already used for
every manifest-declared path in this project). A missing track file
listed in the manifest is already reported cleanly (`"track file
listed in manifest is missing"`) — this phase does not change that
message or its trigger for analysis lane tracks specifically, since
the existing generic check already covers it identically for every
track, lane or not.

Per the phase's explicit conservative scope: no supported lane is
required to exist in a package (most packages will have zero lane
tracks for a long time, until a real adapter exists — that must never
be an error), and no lane is required to have a `receipts/analyze.jsonl`
entry, even once the lane exists — receipts describe an adapter *run*,
and a lane track can legitimately exist (e.g. written by hand via
`clulatent analysis append`, Phase 2.8) with `--no-receipt`, or before
Phase 2.7 added the receipt file at all.

## Receipts validation, conservative

`receipts/analyze.jsonl` is not a `manifest.tracks` entry (it is a
fixed conventional path, `constants.ANALYSIS_RECEIPTS_FILE`, the same
one `analysis_writer.write_analysis_receipt` writes to) and is never
required to exist. If it does exist and is non-empty,
`validate_package` parses it with the same bounded JSONL reader every
other canonical file in this project uses (catching malformed JSON /
non-object lines exactly like `receipts/ingest.jsonl` already does),
then runs each parsed entry through the new `validate_analysis_receipt`:

- `adapter_name`/`tool_name`/`tool_version` (required, bounded label
  strings) and `status` (required, one of `success`/`partial`/`failure`)
  — the same four fields `AnalysisAdapterReceipt` itself requires.
- `model_name`/`model_version`/`failure_details` — bounded if present,
  never required.
- `output_tracks` — each entry must be either a bare supported lane
  name (e.g. `"scene_events"`) or its canonical
  `tracks/<lane>.jsonl` path. Both accepted forms come from one small,
  fixed whitelist built from `ANALYSIS_LANE_NAMES`, so this single
  membership check also rules out an absolute path, a parent-traversal
  segment, or a symlink-escape attempt on its own — none of those
  strings can ever equal a whitelisted form.
- `input_sources` — bounded strings, each checked through the same
  lexical-relative-POSIX-plus-full-containment two-stage path check
  every other package-relative path in this project gets
  (`resolve_in_package` against the real `package_path`).
- `event_counts`/`parameters`/`environment` — must be JSON objects,
  bounded by the same `max_analysis_payload_bytes` limit the writer
  itself already enforces at write time.
- `warnings` — bounded text strings.
- `skipped_count`/`clamped_count`/`bounded_count` — non-negative
  integers if present.

This is intentionally a *shape* check, not a truth check: it confirms
a receipt entry is well-formed enough to trust as a receipt at all
(right fields, right types, bounded, path-safe), never that the
adapter's claims about what it actually did are correct — that
remains outside any automated check, exactly like every other receipt
in this project (`receipts/ingest.jsonl` has never had per-entry shape
validation either; this phase adds it only for `analyze.jsonl` because
the user-facing requirement asked for it specifically). A missing
`receipts/analyze.jsonl` is never an error at any point in this check —
see "Manifest/track descriptor behavior" above.

## Error style

Every new check reports through the same non-raising `(errors,
warnings)` list convention `validate_analysis_event`/
`validate_analysis_track`/`validate_review_track` already use — nothing
new here raises an exception into `validate_package`, and
`validate_package` itself has never let an internal exception escape
as a traceback (every internal error path already converts to an
`errors.append(...)` entry). `clulatent validate` prints
`report.errors` as a numbered list and exits 1 if there are any, with
no Python traceback ever visible to a user — this was already true
before this phase and required no change.

## CLI behavior

`clulatent validate <package>` needed no code change: it already calls
`validate_package` and prints whatever `report.errors`/`report.warnings`
contains, so every new check in this phase surfaces automatically.
`clulatent analysis lanes`/`append`/`append-file`/`validate-file`
(Phase 2.8) are unmodified and continue to work exactly as before —
`validate-file`'s own read-only check already called
`validate_analysis_track` directly and remains the fast, package-free
way to check a batch of candidate events before ever writing them;
`clulatent validate` is the complementary, package-level check for
events that are already on disk.

## Lock behavior

No change. `lock.py` already globs `tracks/*.jsonl` and
`receipts/*.jsonl` generically (Phase 1.7.5), so any analysis lane
track or `receipts/analyze.jsonl` entry was already covered by the
integrity lock hash the moment it existed on disk — Phase 2.7 already
established this, and Phase 2.9 adds no new file `lock.py` needs to
know about. No unlock/relock workflow was added or changed.

## Interaction with existing systems

### Review (Phases 2.0–2.2)

No change needed. `review_events.jsonl` still gets its own
`validate_review_track` call exactly as before; this phase adds an
independent lane-specific check for supported analysis lane tracks
alongside it, in the same per-track loop, with no interaction between
the two. Once an analysis-lane event exists (validated or not — review
targets by id, not by validity), `clulatent review approve/reject/
correct ...` can already act on it; a future correction or rejection
of a lane event flagged invalid by this phase's new checks works
exactly the same way review already works for every other track.

### Writer receipts (Phase 2.7)

No change to `analysis_writer.py`. Every event/receipt the Phase 2.7
writer produces already satisfies Phase 2.6's validation *before* it is
written, so a freshly-written package always passes this phase's new
`validate_package` checks too — the only way to fail them is to hand-
edit a track or receipt file after the fact (exactly how this phase's
own tests produce their failure cases), or to hand-write one via a
route that bypasses the writer entirely.

### CLI writing (Phase 2.8)

No change to `cli.py`'s `analysis` command group. `clulatent analysis
append`/`append-file` still call the Phase 2.7 writer, which still
validates before writing — this phase's new package-level checks are a
second, independent confirmation that whatever ends up on disk (via
the CLI, the writer directly, or by hand) is still lane-valid,
matching the same "layered, independent checks" precedent Phase 2.7's
own docstring already established for envelope-vs-lane validation.

### Portability (Phase 2.4)

No change needed. Every path this phase resolves —
`tracks/<lane>.jsonl` (already checked generically), and now
`receipts/analyze.jsonl` plus any `output_tracks`/`input_sources`
entries inside it — goes through the same relative-POSIX,
symlink-safe `resolve_in_package` check every other canonical path in
this project already uses. No absolute paths, no parent traversal, no
symlink escapes: enforced identically to every prior phase.

## Why this is not CLUBIN and not Studio UI

Nothing in this phase compiles, packages, or transforms any file into
a binary form — CLUBIN remains a planned future format this project
does not build. Nothing in this phase adds a UI, visualization, or
interactive review flow — Studio UI remains unbuilt. This phase is
exclusively a validation-time check: it reads already-canonical
JSON/JSONL files and reports whether they conform to rules Phase 2.6
already defined; it produces no new artifact and changes no on-disk
file.

## Why this is not semantic truth generation

`validate_package` never asserts that a lane event's *content* is
correct — only that its *shape* is valid (bounded, well-typed,
non-identity-claiming, causally-honest). A `scene_boundary` event that
claims a cut at the wrong timestamp, or an `object_proposal` that
misidentifies what's in frame, passes every check in this phase
identically to a correct one — validity is a shape property, not a
truth property. Resolving what is *actually true* about a video
remains entirely a human-review question (Phase 2.0–2.2's
`review_events.jsonl`), unaffected by this phase.

## Non-goals (restated)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime.
- No cross-lane referential-integrity enforcement.
- No requirement that every supported lane exist, or that an existing
  lane have a receipt.
- No mutation of any file — `validate_package` remains fully read-only.
- No claim that a shape-valid event or receipt is "true," only that it
  is well-formed enough to be reviewable and lockable.

## Relationship to prior phases

| Phase | Relationship |
|---|---|
| 2.8 CLI commands | Unmodified; `clulatent validate` is a separate, complementary command from `clulatent analysis validate-file` — one checks a package already on disk, the other checks a candidate batch before writing. |
| 2.7 writer receipts | Every write this module produces already satisfies the checks this phase adds; on-disk failures can only come from hand-editing or a non-writer write path. |
| 2.6 schema primitives | `validate_package` now actually calls `validate_analysis_track` for supported lane tracks; two new functions (`validate_analysis_receipt(s)`) extend the same module with a receipt-shape checker, following its exact `(errors, warnings)` convention. |
| 2.5 design | Cross-lane referential-integrity policy remains deferred exactly as that doc left it — still explicitly out of scope here. |
| 2.4 portability | Every path this phase resolves uses the existing `resolve_in_package` check; no export-rule change needed. |
| 2.2 review writer | `validate_review_track`'s call site is unchanged; the new analysis-lane check runs alongside it in the same per-track loop, independently. |
| 1.7.5 locking | Existing generic `tracks/*.jsonl`/`receipts/*.jsonl` globs already covered every file this phase validates; no `lock.py` change needed. |

## What later phases still need to build

1. A real adapter that produces genuinely detected events (still
   requires an actual detector/tool — not part of this phase or a
   committed dependency).
2. Cross-lane referential-integrity validation (strict vs. warn),
   deferred since Phase 2.5.
3. A Studio UI or any interactive review surface.

## Files changed

- `src/clu_latent/analysis_lanes.py`: two new functions,
  `validate_analysis_receipt`/`validate_analysis_receipts`, plus their
  supporting helpers (`_check_output_track_reference`,
  `_check_input_sources`).
- `src/clu_latent/validate.py`: `validate_package`'s per-track loop now
  also runs `validate_analysis_track` for supported lane tracks (reusing
  the raw records already parsed for the generic envelope check), and a
  new block validates `receipts/analyze.jsonl` if present.
- `tests/test_validate_analysis_lanes.py` (new): 16 tests.
- `docs/PHASE_2_9_ANALYSIS_LANE_VALIDATION_INTEGRATION.md` (new, this
  document).
- `README.md`: short Phase 2.9 roadmap note.

## Summary

Phase 2.9 makes `clulatent validate` actually enforce the rules Phase
2.6 always defined for analysis lane tracks, and adds a conservative
shape check for `receipts/analyze.jsonl` when one exists. No analysis
intelligence is added, no new artifact is produced, and no existing
behavior for non-lane tracks changes — a package with no analysis
lanes at all validates exactly as it always did, and a package with a
lane written through the Phase 2.7 writer or the Phase 2.8 CLI
continues to validate cleanly, because it was already valid by
construction.
