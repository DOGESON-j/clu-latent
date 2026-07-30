# Phase 2.0: Review Events

Status: **implemented** (factories + validation). This is the first
Phase 2 implementation work, built on the frozen Phase 1.13 readiness
review (`docs/reviews/PHASE_1_13_PHASE_2_READINESS_REVIEW.md`,
recommendation: GO WITH CAVEATS).

## Scope of this phase

Implemented:

- `tracks/review_events.jsonl` as a canonical track, using the existing
  shared `EventEnvelope` (no new envelope schema).
- Seven review event types, matching the Phase 1.9 design
  (`docs/design/PHASE_1_9_HUMAN_REVIEW_CORRECTION.md`):
  `review_status`, `review_approval`, `review_rejection`,
  `review_correction`, `review_override`, `human_note`,
  `review_session_summary`.
- Factory functions for all seven types in `src/clu_latent/review.py`.
- Track-local validation (`review.validate_review_track`), wired into
  `validate_package` (`validate.py`) whenever a track named
  `"review_events"` is present in `manifest.json`.
- Tests: `tests/test_review_events.py`.

Not implemented this phase (explicitly deferred):

- A `clulatent review` CLI command that writes to
  `review_events.jsonl` on disk.
- A state-resolution algorithm that turns review events into a single
  "current reviewed state" per source event. Reviewed state is **not**
  resolved into final truth by anything in this phase — resolution is
  Phase 2.1/2.2 future work.
- Any consumer (viewer, search index, CLI) that reads or displays
  review events.
- Cross-track referential integrity (see below).

Core principle, unchanged from Phase 1.9: **human review is additive
evidence**. Nothing in this package format, and nothing added in this
phase, ever mutates, deletes, or overwrites a model-generated event,
another track's records, or the source media. A review event only ever
*adds* a new record to `review_events.jsonl`.

## Event types and states

Same seven types and the same closed `review_state` enum (`unreviewed`,
`needs_review`, `uncertain`, `reviewed`, `approved`, `corrected`,
`rejected`, `superseded`) as specified in Phase 1.9. `superseded` is a
derived, read-time-only state — nothing in this phase lets a caller
directly assert it; both the factories and `validate_review_track`
reject it if a judgment event tries to declare `review_state:
"superseded"` directly.

## Deliberate deviations from the Phase 1.9 design doc

- **Id prefixes.** The Phase 1.9 doc's worked examples use a single
  shared `rv_` id namespace (`rv_000000`, `rv_000001`, ...) regardless
  of type. This phase's factories instead use a per-type prefix
  (`rv_status_`, `rv_approval_`, `rv_rejection_`, `rv_correction_`,
  `rv_override_`, `rv_note_`, `rv_session_`), each followed by a
  zero-padded index — this was the Phase 2.0 implementation
  instruction, and makes a record's type identifiable from its id
  alone. It does not change the envelope shape or any validation rule.
- **`review_session_summary` payload shape.** The Phase 1.9 "Common
  Payload Fields" table lists `reviewed_at` as required on every type,
  but the doc's own worked example for `review_session_summary` omits
  it in favor of `started_at`/`ended_at`. This implementation follows
  the concrete worked example: `make_review_session_summary_event` has
  no `reviewed_at` parameter.
- **`supersedes_event_ids` on correction/override.** The Phase 1.9 doc
  says this field is required "where appropriate," but its own first
  worked correction example (`rv_000003`, correcting `ts_000008`) uses
  `supersedes_event_ids: []` — a first-time correction has nothing to
  supersede yet. This implementation treats `supersedes_event_ids` as
  always optional for every type, consistent with that example, while
  still validating it whenever present (see below).

## Validation: what is checked, and what is deliberately deferred

`review.validate_review_track` is a track-local pass, called from
`validate_package` in addition to (not instead of) the generic
per-record `EventEnvelope` schema check every track already gets. It
checks, per record:

- `type` is one of the seven recognized review event types.
- `producer.name` starts with `human:` (the review-track half of Phase
  1.9 rule 12 — a `human:`-prefixed producer being *forbidden* outside
  `review_events.jsonl` is not checked here, since nothing in this
  phase writes producer-prefixed records to any other track).
- `reviewer_id`, `reviewer_label`, `reason`, `note_text`,
  `override_kind`, `session_id` are bounded, NUL/control-character-free
  text (`Limits.max_review_label_bytes` / `max_review_text_bytes`). The
  `human:` producer prefix's suffix (i.e. `producer.name` itself) is
  bounded the same way, since it is otherwise reviewer-controlled,
  unchecked free text living outside `payload`.
- `source_event_ids`, `supersedes_event_ids`, and
  `review_session_summary.review_event_ids` are all validated for
  *shape* before any existence/self-reference check runs: each must be
  a JSON array of short, non-empty strings (each bounded by
  `Limits.max_review_label_bytes`), or the field is rejected outright.
  This matters because a bare string, number, or object in one of
  these fields is truthy — a naive `if not value` requiredness check
  alone would let it silently satisfy "non-empty" and skip every
  downstream `isinstance(..., list)`-guarded check.
- `source_event_ids` is present and non-empty for the five judgment
  types (`review_status`, `review_approval`, `review_rejection`,
  `review_correction`, `review_override`), and never references the
  record's own id.
- `supersedes_event_ids`, when present, never references the record's
  own id, and every id it references must exist somewhere else in the
  *same* `review_events.jsonl` (a dangling reference is an error). The
  full `supersedes_event_ids` graph across the track is checked for
  cycles.
- `review_state` is required on judgment types and must be a string
  matching one of the eight-state enum, excluding the derived-only
  `superseded` — checked with an explicit `isinstance(str)` guard
  first, so an unhashable value (a list or object from a hand-edited
  record) is reported as a validation error instead of crashing
  `validate_package` with an uncaught `TypeError`.
- `review_correction`/`review_override`: `original_payload` and
  `corrected_payload` are both required, bounded
  (`Limits.max_review_payload_bytes`), JSON-object-shaped with
  dotted-path keys, and must share the exact same key set (Phase 1.9
  rule 6 — same-shape patch, only values differ). For
  `review_override`, `review_state` must be `"corrected"` unless
  `corrected_payload` reclassifies `type`, in which case `"rejected"`
  is also allowed (Phase 1.9 rule 6a).
- `human_note`: `note_text` is required; an empty `source_event_ids` is
  allowed (general commentary) but produces a warning, not an error.
- `review_session_summary`: `review_event_ids` is required and
  non-empty, never self-referencing, and every referenced id must exist
  in the same track. `counts_by_review_state` must itself be a JSON
  object (a non-object value, e.g. a string, is rejected outright) and
  is recomputed from the actual `review_state` of every referenced
  event and compared against the declared value — a mismatch is a hard
  error (Phase 1.9 rule 9).

**Deliberately deferred** (per the Phase 2.0 implementation
instruction — not "simple and safe" enough for this conservative
phase):

- Resolving `source_event_ids` against ids in *other* canonical tracks
  (`keyframes`, `audio_events`, `speech_events`, `semantic_events`).
  This would require `validate_review_track` (or its caller) to load
  and index every other track's ids for every validation run. This is
  future Tier-4 validation, per the schema freeze draft's Validation
  Tiers (`docs/spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md`).
- `original_payload` drift detection — verifying a correction's
  `original_payload` still matches the *current* value in the
  referenced source event (Phase 1.9 rule 6's staleness check, meant to
  catch corrections made against data that a later re-ingest changed).
  This also requires cross-track lookups and is deferred alongside the
  above. Only the local half of rule 6 — `original_payload` and
  `corrected_payload` sharing the same key set — is enforced now.
- The "`human:` prefix forbidden outside `review_events.jsonl`" half of
  Phase 1.9 rule 12, since no code in this phase writes to any other
  track.
- Immutability enforcement (Phase 1.9 rule 13) is upheld by
  construction — nothing in this phase's code path writes to any track
  other than `review_events.jsonl` — but is not separately re-verified
  by a new validation check, since there is nothing yet that could
  violate it.

`supersedes_event_ids` and `review_session_summary.review_event_ids`
are checked fully (existence + acyclicity), because both fields only
ever point at other *review* events, so resolving them against the
same track's own id set is both safe and complete — unlike
`source_event_ids`, which points at *other* tracks.

## Locking

No locking behavior changes this phase. `review_events.jsonl` becomes
canonical, and is automatically covered by package integrity locking
the moment it exists, because `lock.py` already globs every `.jsonl`
file under `tracks/` generically (no track-name whitelist to update).
Practical consequences, unchanged from Phase 1.9's own guidance:

- Adding review events to an already-locked package modifies it, so
  its existing lock (`lock/package.lock.json` /
  `package.lock.sha256`) will no longer match — re-locking requires
  the existing `clulatent lock --force` flow, same as any other
  content change.
- There is no new `unlock` command and none is planned by this
  addition. Prefer working on a copy, or re-locking explicitly with
  `--force`, consistent with how every other track addition is already
  expected to interact with locking.

## Files changed

- `src/clu_latent/constants.py`: `REVIEW_EVENTS_TRACK_NAME`,
  `REVIEW_EVENTS_TRACK_FILE`.
- `src/clu_latent/security/limits.py`: `max_review_label_bytes`,
  `max_review_text_bytes`, `max_review_payload_bytes`.
- `src/clu_latent/review.py` (new): factories + `validate_review_track`.
- `src/clu_latent/validate.py`: calls `validate_review_track` for any
  track named `"review_events"`.
- `tests/test_review_events.py` (new).

## What Phase 2.1/2.2 still need to build

- A `clulatent review` CLI (or equivalent) that actually writes
  `review_events.jsonl` records to a package, under the existing
  operation-lock discipline.
- The state-resolution algorithm from Phase 1.9 ("Review States"
  section) — deterministic, read-time computation of a single current
  `review_state` per source event from the full history of judgment
  events referencing it, respecting `supersedes_event_ids` chains.
- Cross-track `source_event_ids` resolution and `original_payload`
  drift detection (the two deferrals above), once a safe, bounded way
  to load other tracks' ids during validation is designed.
- Any consumer (inspector/viewer, search index) that surfaces reviewed
  state to a human.
