# Phase 2.1: Reviewed State Resolver

Status: **implemented** (read-only resolver). Builds on the frozen
Phase 2.0 review events implementation
(`docs/PHASE_2_REVIEW_EVENTS.md`, tag
`phase-2.0-review-events-implementation-freeze`).

## Scope of this phase

Implemented:

- `src/clu_latent/review_resolver.py`: a pure, read-only module that
  computes the *current* `review_state` of every source event in a
  package, at read time, by folding `tracks/review_events.jsonl` over
  the package's other canonical tracks.
- Public functions: `resolve_review_states`, `resolve_review_state_for_event`,
  `load_review_events`, `resolve_package_review_states`,
  `summarize_review_states`.
- `ResolvedReviewState`, a small dataclass carrying the resolved state
  plus enough context (`latest_review_event_id`, `review_event_ids`,
  `corrected_payload`, `reviewer_id`, `reviewed_at`, `reason`,
  `certainty`, and boolean `is_*`/`has_conflict` flags) for a caller to
  act on without re-deriving it.
- A small, read-only CLI command: `clulatent review-state <package>`,
  printing the same counts `summarize_review_states` computes.
- Tests: `tests/test_review_resolver.py`.

Not implemented this phase (explicitly out of scope, per the Phase 2.1
implementation instruction):

- No Studio UI, no caption runtime, no diarization/speaker runtime, no
  identity recognition, no `semantic_events` generation.
- No `reviewed_truth` (or any other new) track. Resolved state is never
  written back into the package — every function in this module is
  read-only with respect to the package on disk.
- No CLI command that *writes* review events (that is still deferred
  Phase 2.x work per `docs/PHASE_2_REVIEW_EVENTS.md`).
- No cryptographic signatures, no cloud/collaboration/accounts, no
  CLUBIN, no change to lock behavior, no automatic merging of
  conflicting review history into a single "winning" truth beyond the
  deterministic latest-wins rule described below (conflicts are
  surfaced, not silently resolved away).

## Core principle

Unchanged from Phase 1.9/2.0: **review events are additive evidence.**
Nothing in this phase mutates, deletes, reorders, or overwrites a
source event's own record in its own track, or a review event's own
record in `review_events.jsonl`. `resolve_review_states` and everything
built on it only ever *reads* already-canonical data (in memory, or via
the existing tolerant/strict JSONL readers) and returns plain Python
objects that exist only for the duration of the call. Re-running the
resolver after the underlying package changes always recomputes from
scratch — there is no cached or persisted "resolved" state anywhere.

## What the resolver reads

- Every canonical track other than `review_events` contributes its
  record ids to the universe of "source events" the resolver will
  report a state for.
- `tracks/review_events.jsonl`, if the manifest declares a track named
  `"review_events"`. Packages with no such track are fully supported:
  every source event resolves to `review_state: "unreviewed"`.

`load_review_events` and `resolve_package_review_states` are the two
disk-touching entry points; `resolve_review_states` and
`resolve_review_state_for_event` operate purely over already-parsed
`EventEnvelope` lists and never touch disk, so they can be reused
directly against hand-built or already-loaded data (as the unit tests
do).

## How one source event's state is resolved

Only the five judgment types from Phase 1.9/2.0 change a source event's
state: `review_approval` → `approved`, `review_rejection` → `rejected`,
`review_correction` → `corrected`, `review_override` → whatever
`review_state` it declares (`corrected` or `rejected`), and
`review_status` → whatever `review_state` it declares (any assertable
state). `human_note` and `review_session_summary` never change state,
by design — they are collected and read elsewhere (a note's own
`source_event_ids`, a summary's own `review_event_ids`), but play no
role in `resolve_review_states` at all.

For a source event referenced by more than one judgment event:

1. **Ordering.** Judgments are sorted by `reviewed_at` (parsed as
   ISO-8601, `Z` treated as UTC) when it parses; a judgment whose
   `reviewed_at` is missing or unparseable sorts as if it happened at
   the epoch, with ties (including all-unparseable groups) broken by
   the judgment's position in the input list — i.e. track/file order.
   This is deliberately simple ("small and boring") rather than a full
   causal/vector-clock model: it matches the two behaviors the Phase
   2.1 instruction asked for (prefer `reviewed_at`, otherwise preserve
   track order) without inventing a third ordering rule for mixed
   groups.
2. **Latest wins.** The chronologically/order-last judgment's state
   becomes the source event's `review_state`; its `reviewer_id`,
   `reviewed_at`, `reason`, `certainty` (`EventEnvelope.confidence`),
   and — if it is a `review_correction`/`review_override` —
   `corrected_payload`, are exposed directly on the `ResolvedReviewState`.
3. **Supersession.** If any judgment in the group declares
   `supersedes_event_ids`, every id it names is recorded as
   "superseded" for this source event; `is_superseded` is `True`
   whenever at least one such reference exists anywhere in the group.
   The superseded judgment's own record is never touched — this flag
   only affects which judgments count as "unsuperseded" for conflict
   detection (next point). If every judgment in a group ends up
   superseded (e.g. a cycle), the resolver falls back to the full
   ordered group rather than reporting nothing.
4. **Conflict detection.** `has_conflict` is `True` when, among the
   *unsuperseded* judgments in the group, either (a) more than one
   distinct `review_state` value appears, or (b) more than one
   correcting judgment (`review_correction`/`review_override`) appears
   — even if they happen to assert the same state. (b) exists because
   two independent, unlinked corrections can both say `"corrected"`
   while disagreeing about *what* the correct value is; comparing only
   the abstract state would miss that. Conflicts are never auto-resolved
   or hidden — `has_conflict` is a flag for the caller, and the
   "latest wins" state above is still reported alongside it, exactly as
   the Phase 2.1 instruction specifies ("Do not fail validation for
   conflicts here. The resolver should report them.").

A source event with no applicable judgment events resolves to
`review_state: "unreviewed"`, `is_reviewed: False`, and every other
field at its default (`None`/`[]`/`False`).

## `superseded` is a flag, not a final state

`review.REVIEW_STATES` includes `"superseded"` as a member, explicitly
documented (in `review.py`) as "a derived, read-time-only state" that
no factory or validator lets a human assert directly — exactly the
state this phase's resolver is responsible for deriving. But a source
event's own `ResolvedReviewState.review_state` field never literally
holds the string `"superseded"`: by construction, the "latest wins"
judgment is never itself the target of a later supersession (there is
nothing later than the latest). Instead, `is_superseded` is the boolean
flag that reports "this resolved state has an active supersession
chain behind it" — i.e. an earlier judgment for this same source event
was explicitly superseded by a later one. `summarize_review_states`
counts this flag under the `superseded` key, not by matching
`review_state == "superseded"`.

## Defensiveness (this phase does not duplicate validation)

`review.validate_review_track` remains the authority on *shape*
correctness of `review_events.jsonl` — this module does not re-run
those checks. It is defensive on top of that only so a malformed or
hand-edited package can never crash resolution:

- `source_event_ids`/`supersedes_event_ids` that are not a JSON array
  of strings are treated as empty, never raise.
- A judgment event whose `review_state` is missing, not a string, or
  not a member of the allowed set for its type is skipped (with a
  warning), never raises — this is also where the same
  frozenset-membership crash class fixed in `review.py`
  (`review_state not in ASSERTABLE_REVIEW_STATES` on an unhashable
  value) is avoided, via the same `isinstance(str)`-first guard.
- A `source_event_ids` reference to an id that is not a known source
  event in the package is ignored (with a warning), not an error —
  this is the resolver's equivalent of Phase 2.0's deferred
  "cross-track referential integrity" check; the resolver's defensive
  posture here is intentionally lenient (skip and warn) rather than
  strict (fail), since it is a read-time convenience tool, not a
  validator.

## Lock behavior

Unchanged. This phase adds no new track, writes nothing to any package,
and does not touch `lock.py`, `reindex.py`, `index.py`, or `tracks.py`.
A package's lock status is unaffected by running the resolver or the
`review-state` CLI command against it, at any point, any number of
times.

## Files changed

- `src/clu_latent/review_resolver.py` (new).
- `src/clu_latent/cli.py`: added the read-only `review-state` command.
- `tests/test_review_resolver.py` (new).

## What Phase 2.2+ still need to build

- A `clulatent review` CLI (or equivalent) that actually writes
  `review_events.jsonl` records to a package (still deferred from
  Phase 2.0).
- Cross-track `source_event_ids` resolution and `original_payload`
  drift detection in `validate_review_track` itself (still deferred
  from Phase 2.0 — this phase's resolver is intentionally lenient
  about dangling references, not strict).
- Any consumer (inspector/viewer, search index) that surfaces resolved
  review state to a human beyond the plain-text `review-state` CLI
  summary added here.
