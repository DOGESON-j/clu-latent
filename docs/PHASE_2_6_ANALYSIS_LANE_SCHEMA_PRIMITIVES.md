# Phase 2.6: Analysis Lane Schema Primitives

Status: **schema/validation-only implementation — no analysis runtime**.
Builds on the frozen Phase 2.5 Analysis Lanes / Tracking Adapter Design
(`docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`, tag
`phase-2.5-analysis-lanes-tracking-adapter-design-freeze`) and on the
frozen Phase 2.4 Portable Package Export Rules
(`docs/PHASE_2_4_PORTABLE_PACKAGE_EXPORT_RULES.md`, tag
`phase-2.4-portable-package-export-rules-freeze`). This phase adds the
smallest safe Python layer — `src/clu_latent/analysis_lanes.py` — so a
**future** adapter can check a candidate lane event's shape and bounds
before anyone proposes writing it into a package. It implements no
video/audio analysis, no FFmpeg-based tracker, no OCR runtime, no
object detector, and no ML model dependency.

> Given a raw, adapter-produced dict that claims to be a
> `scene_events`/`ocr_events`/`cross_lane_link_events`/... record, how
> do we reject anything malformed, oversized, path-unsafe, or making an
> identity/causal claim it has no business making — before it is ever
> handed to the shared `EventEnvelope`, written to a track, reviewed,
> or locked?

## Scope of this phase

Implemented:

- `ANALYSIS_LANE_NAMES` / `SUPPORTED_ANALYSIS_LANES`: the 14-lane
  catalog from Phase 2.5, verbatim and in the same order.
- `is_supported_analysis_lane(name)`, `normalize_analysis_lane_name(name)`.
- `validate_analysis_event(record, ...)` / `validate_analysis_track(events, ...)`:
  non-raising `(errors, warnings)` validators, matching the
  `review.validate_review_track` convention already in the codebase.
- Safe-claim rules: identity-field rejection for object/tracking lanes,
  neutral-label allowances for object and audio lanes, causal-relation
  rejection for `cross_lane_link_events`.
- `AnalysisAdapterReceipt`: a documented dataclass shape a future
  adapter run can fill in and serialize (`.to_dict()`), not wired to
  any disk write or CLI command.
- Five new `Limits` fields (`max_analysis_*`) in
  `src/clu_latent/security/limits.py`, alongside the existing
  `max_review_*` bounds, so nothing here hardcodes a byte count.
- `tests/test_analysis_lanes.py`: 62 tests.

Not implemented (explicitly out of scope, per the phase instruction):

- No adapter runtime, no FFmpeg tracker, no OCR engine, no object
  detector, no ML model dependency, no external tool invocation.
- No new track file is written. No `tracks/<lane>.jsonl` exists yet.
- No wiring into `validate.py`, `lock.py`, `tracks.py`, `manifest.py`,
  or any CLI command. `analysis_lanes.py` is not imported by any of
  them.
- No receipt is written to disk. `AnalysisAdapterReceipt` is an
  in-memory shape only.

## Core principle

Restated from Phase 2.5, because every check in this module exists to
enforce it at the schema layer:

> Adapters produce **evidence**. Evidence is not truth. Detected does
> not mean trusted. Generated does not mean canonical. Canonical means
> validated, bounded, receipted, reviewable, and lockable.

`validate_analysis_event`/`validate_analysis_track` only ever check
**shape and bounds** — never whether a detection is *correct*.
Correctness is a human review question (see below), not a schema
question.

## Why schema-only, and why raw dicts instead of `EventEnvelope`

Every canonical track in CLULatent already shares one envelope,
`event.EventEnvelope` (`id`, `type`, `t_start_ms`, `t_end_ms`,
`producer`, `confidence`, `payload`). A future lane track is just
another canonical track using that same envelope — this phase adds no
new envelope container schema.

But `EventEnvelope` is a Pydantic model with **lenient** coercion: it
would silently accept `"1000"` (a string) for `t_start_ms` and convert
it. That's fine for already-trusted internal construction, but wrong
for the *first* gate a future adapter's raw, external, untrusted JSON
output should pass through. `validate_analysis_event` therefore checks
raw dicts with strict `isinstance` checks (including the classic
Python gotcha that `bool` is an `int` subclass, so `True`/`False` are
explicitly excluded from every numeric/int check) — a stricter,
adapter-facing gate that would run *before* anything is ever handed to
`EventEnvelope.model_validate()`.

Because there is no adapter runtime yet, this phase cannot be
integration-tested against a real tool. It is deliberately narrow: a
shape/bounds checker and a receipt shape, nothing else.

## What is validated

Every analysis event must have `id`, `type`, `t_start_ms`, `t_end_ms`,
`producer`, `payload`. Rules enforced by `validate_analysis_event`:

- `id`, `type`: bounded strings (`max_analysis_id_bytes`,
  `max_analysis_type_bytes`).
- `t_start_ms`, `t_end_ms`: integers (not bool), `t_start_ms >= 0`,
  `t_end_ms >= t_start_ms`.
- `duration_ms`, if present: integer, consistent with
  `t_end_ms - t_start_ms`.
- `confidence`, if present: float/int (not bool), `0 <= confidence <= 1`.
- `producer`: an object with bounded `name`/`version` strings, matching
  `event.Producer`'s existing two-field shape (not a bare string —
  every existing convention in the codebase already treats producer as
  a two-field object).
- `payload`: must be a JSON object (dict); serialized size bounded by
  `max_analysis_payload_bytes` (same byte-length-of-`json.dumps`
  pattern `review.py` already uses for `max_review_payload_bytes`).
- Any path-like payload key (`path`, or a key ending in `_path`): must
  be a relative POSIX path with no parent traversal
  (`security.paths.validate_relative_posix`); if a real `package_root`
  is supplied, also checked for filesystem containment and symlink
  escape (`security.paths.resolve_in_package`). Without a
  `package_root` (the common case — an adapter checking output before
  any package exists), only the lexical check runs, since full
  symlink-escape safety is a filesystem-time property, not a
  pure-schema one.

`validate_analysis_track(events, lane=...)` runs the above per event,
plus track-level checks (duplicate `id`s within the track), aggregating
every error/warning across the whole list rather than stopping at the
first bad record.

## Safe claim rules

**Object/region/tracking lanes** (`object_proposal_events`,
`object_tracking_events`, and similar): neutral, non-identifying labels
like `"ball-like candidate"`, `"person-like region"`,
`"object_candidate_7"` are allowed — labels are claims, not truth. Any
payload containing `person_name`, `identity`, `face_identity`, or
`biometric_identity` is **rejected outright** — these fields are never
allowed in an automatic-adapter payload, not even set to a placeholder.
Separately, if a boolean flag like `is_identity`/`is_identity_claim` is
present (legitimate fields sketched in Phase 2.5's payload examples),
it must be exactly `False` — this is a distinct rule from the
field-presence rejection above: "must not exist" vs. "must default to
false."

**Audio lanes**: neutral evidence labels are allowed —
`impact-like`, `speech-like`, `music-like`, `repeating pattern`,
`left-channel dominant`, `sudden silence`. There is no fixed forbidden
word list for audio labels (unlike the identity-field hard rejection
above) — the instruction was to *avoid requiring* an exact-cause claim
as schema truth, not to reject any payload containing one, so this is
enforced by simply never requiring a causal field.

**Cross-lane links** (`cross_lane_link_events`): payload supports
`source_event_ids`, `target_event_ids`, `relation_type`, `confidence`.
Rules: links are evidence relationships, not semantic truth;
referenced ids must be bounded strings (`max_analysis_id_bytes` each);
`relation_type` must be a bounded string
(`max_analysis_label_bytes`) and must not be one of a small forbidden
set of causal-claim relation types (e.g. `"causes"`, `"caused_by"`) —
correlation must never be schema-encoded as proof of cause. A link
event referencing itself as both source and target of the same id is
rejected as a shape error, not a semantic one.

## Adapter receipt contract

`AnalysisAdapterReceipt` (dataclass, `.to_dict()`) documents the shape
a future adapter run receipt should have, mirroring the existing
`receipts.ReceiptLog` field-building convention without writing
anything to disk:

```txt
adapter_name, tool_name, tool_version, model_name, model_version,
parameters, input_sources, output_tracks, event_counts, status,
warnings, skipped_count, clamped_count, bounded_count,
failure_details, environment
```

When a future phase adds a real adapter, its receipt (e.g. appended to
`receipts/analyze.jsonl`) can follow this shape. Because `lock.py`
already globs `receipts/*.jsonl` generically, that future file is
lock-covered without any code change — the same observation Phase
2.3/2.4/2.5 already made for hypothetical review/export/analysis
receipts.

## Interaction with existing systems

### Review (Phases 2.0–2.2)

Once a lane track exists, every lane event with a stable `id` is a
valid review target exactly like today's speech/caption/silence
events. Humans can approve, reject, correct, override, or note lane
events through the existing `review_events.jsonl` mechanism without
mutating the original detector track. "Reviewed truth" stays a
read-time resolution over review events — never a rewritten detector
track. This phase adds no review-side code; it only guarantees that
whatever a future lane track contains is small, bounded, and
type-safe enough to be a sane review target when that day comes.

### Locking (Phase 1.7.5)

`lock.py` already globs `tracks/*.jsonl` and `receipts/*.jsonl`
generically. A future `tracks/scene_events.jsonl` (or any other lane
file) is automatically covered by the existing hash-lock mechanism the
moment it exists on disk — no lock.py change is required by this phase
or anticipated to be required when lanes are implemented, so any
future lane file's presence, absence, or content change is detected by
the existing lock/verify flow without modification.

### Portability (Phase 2.4)

Nothing in this phase changes export/import behavior, because no new
track file is written yet. The path-safety checks added here
(`validate_relative_posix`/`resolve_in_package` reuse) exist so that
*when* a lane track is eventually written, any path-like payload field
inside it already obeys the same relative-POSIX, symlink-safe,
non-absolute rules every other canonical path in a `.clulatent`
package already obeys — so a future lane track requires no special
case in Phase 2.4's export rules.

## Why this is not CLUBIN

CLUBIN (mentioned in Phase 2.5's non-goals) is a hypothetical packaged
binary/runtime product. This phase ships no binary, no runtime, no
packaged tool — only Python schema/validation functions and a
dataclass, importable but never invoked by any CLI command.

## Why this is not Studio UI

No UI, no interactive review surface, no visualization is added here.
The only interfaces are pure functions and a dataclass; how a human
reviews or visualizes lane events remains entirely a future concern,
unaffected by this phase.

## Why this is not semantic truth generation

`validate_analysis_event`/`validate_analysis_track` check shape and
bounds only — id uniqueness, timestamp ordering, confidence range,
payload size, path safety, absence of forbidden identity/causal
fields. They never assert that a detection is *correct*, never resolve
conflicting detections, never merge/deduplicate across lanes, and never
promote a passing record to "canonical" on their own — validation is a
precondition for canonicity (per the restated core principle above),
not canonicity itself. Only reviewed, locked, receipted tracks get
that status, in whatever future phase implements them.

## Non-goals (restated)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No new track file, no CLI command, no wiring into
  `validate.py`/`lock.py`/`tracks.py`/`manifest.py`.
- No receipt written to disk.
- No identity recognition, no biometric database, no real-person
  identification.
- No claim that correlation proves cause.
- No change to existing tracks, review, locking, or export behavior.

## Relationship to prior phases

| Phase | Relationship |
|---|---|
| 2.5 analysis lane design | This phase implements the schema layer for exactly the 14-lane catalog and trust ladder Phase 2.5 defined; no lane names or rules were added or changed. |
| 2.4 portability | Path-like payload fields reuse `security/paths.py` unchanged; no export-rule change needed. |
| 2.0–2.2 review | No review-side code added; this phase only ensures future lane records are review-target-shaped when a lane track eventually exists. |
| 1.7.5 locking / 2.3 reviewed workflow | No lock.py change; existing generic `tracks/*.jsonl`/`receipts/*.jsonl` globs already cover future lane files. |

## What later phases still need to build

Not ordered as a commitment — only a backlog map, narrowed from Phase
2.5's:

1. Manifest/constants reservation for chosen lane track file names.
2. A real adapter (e.g. scene detection via an optional extra) that
   calls `validate_analysis_event`/`validate_analysis_track` before
   writing.
3. Wiring `validate_analysis_track` into `validate.py`'s package
   validation for any lane track present in `manifest.tracks`.
4. `receipts/analyze.jsonl` writer using the `AnalysisAdapterReceipt`
   shape.
5. Cross-lane link referential-integrity policy (strict vs. warn) at
   validation time.
6. Review-side handling once real lane events exist (should be
   additive, no new review.py rules expected).

## Files changed

- `src/clu_latent/security/limits.py`: five new `max_analysis_*`
  bound fields.
- `src/clu_latent/analysis_lanes.py` (new): lane catalog, event/track
  validators, safe-claim rules, `AnalysisAdapterReceipt`.
- `tests/test_analysis_lanes.py` (new): 62 tests.
- `docs/PHASE_2_6_ANALYSIS_LANE_SCHEMA_PRIMITIVES.md` (new, this
  document).
- `README.md`: short Phase 2.6 roadmap note.

## Summary

Phase 2.6 gives future analysis-lane adapters the smallest safe shape
to write into: a strict, non-raising, bounded validator for individual
events and whole tracks, safe-claim rules that keep object/tracking and
audio lanes from smuggling in identity or causal-truth claims, and a
documented (but unused) adapter receipt contract — all with zero new
runtime dependency, zero new track file, and zero change to existing
validation, locking, review, or export behavior. Building an actual
adapter, wiring validation into `validate.py`, and writing real lane
tracks all remain later, explicit phases.
