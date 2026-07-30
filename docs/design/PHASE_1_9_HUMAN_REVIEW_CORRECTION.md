# CLULatent Phase 1.9: Human Review / Correction Layer

**Status**: Design-only. No implementation. No runtime behavior. No UI. No ML
dependencies. Schemas, field definitions, and validation rules only.

**Motivation**: CLULatent Phases 1.7–1.8 produce *model-generated* perception
events — `speech_segment` (Whisper), `speaker_label`/`speaker_turn`/
`overlapping_speech` (future diarization), plus `keyframes` and
`audio_events`. Every one of these is, by design, **untrusted producer
output**: a model's best guess, carrying a confidence score but no authority.

At some point a human looks at that output and forms a judgment: *this
transcript is wrong*, *this speaker label is correct*, *this segment should
not exist*, *the real text is "…"*, *this whole region needs another look*.
Today CLULatent has nowhere to record that judgment. This phase defines how
CLULatent represents **human review and correction** as a first-class,
append-only, auditable layer that sits *on top of* model events without
mutating them.

The guiding principle: **a human decision is itself just another event** —
one more producer in the same envelope, distinguished by `producer.name` and
a `review`-family `type`, always pointing back (via `source_event_ids`) at
the model event(s) it judges. Review never rewrites history; it annotates
it.

---

## Table of Contents

1. [Core Design Principles](#core-design-principles)
2. [The Immutability Rule](#the-immutability-rule)
3. [Track File](#track-file)
4. [Review States](#review-states)
5. [Common Payload Fields](#common-payload-fields)
6. [Event Types & Schemas](#event-types--schemas)
7. [State Resolution: Deriving Reviewed Truth](#state-resolution-deriving-reviewed-truth)
8. [Provenance & the Producer Field](#provenance--the-producer-field)
9. [Validation Rules](#validation-rules)
10. [Receipts & Non-Fatal Warnings](#receipts--non-fatal-warnings)
11. [Interaction With Locking (Phase 1.7.5)](#interaction-with-locking-phase-175)
12. [Non-Goals](#non-goals)
13. [Implementation Timeline](#implementation-timeline)
14. [Worked Examples](#worked-examples)

---

## Core Design Principles

### 1. Human decisions are events, not edits

A status change, correction, approval, rejection, override, or note is
recorded as a **new event** in a dedicated track — never as an in-place edit
of the model event. This preserves the full chain of "what the model said" →
"what the human decided" → "why". The original model event stays
byte-stable so package locking (Phase 1.7.5) and re-analysis diffs remain
meaningful.

### 2. Reuse the existing event envelope

Every review event is a canonical `EventEnvelope` (`event.py`) — the same
container used by `keyframes`, `audio_events`, `speech_events`, and the
Phase 1.8 `speaker_events` design:

```python
{
  "id": "string (e.g., 'rv_000000')",
  "type": "string (see Event Types below)",
  "t_start_ms": int (>= 0),
  "t_end_ms": int (>= 0, >= t_start_ms),
  "producer": {"name": "human:<reviewer_id>", "version": "review-protocol-version"},
  "confidence": float | null,   # reviewer's self-reported certainty
  "payload": { ... type-specific, see below ... }
}
```

No new top-level envelope schema. No new envelope-level fields. The
human/machine distinction lives entirely in `producer.name` and the `type`
string, exactly as the speaker layer (Phase 1.8) distinguishes producers.

### 3. Every judgment points back to what it judges

`source_event_ids` is **required and non-empty** for every event type that
renders a judgment (`review_status`, `review_approval`, `review_rejection`,
`review_correction`, `review_override`). A judgment about nothing is
meaningless; validation rejects it. `human_note` and
`review_session_summary` relax this — see their schemas.

### 4. Append-only, monotonic

The review track is append-only. A reviewer changing their mind appends a
*new* review event, which may supersede one or more earlier ones via
`supersedes_event_ids`, rather than deleting or rewriting anything. The
latest non-superseded, non-conflicting event(s) for a given target are the
effective decision. This mirrors how `receipts/ingest.jsonl` is append-only.

### 5. Confidence means something different for humans

For a model, `confidence` is a probability. For a human, the same envelope
field is *self-reported certainty* (sometimes called "certainty" in this
document to make the distinction explicit) — a reviewer may mark a
correction `1.0` ("I am certain the word is 'ainabfaylo'") or `0.5` ("I
think this is the right speaker but the audio is muddy"). It is optional
(`null` = "certainty not stated"), but recommended, and it is **never**
compared against or blended with model confidence. They are different
quantities that happen to share a field name — no schema change was needed
to carry it.

---

## The Immutability Rule

This is the load-bearing constraint of the entire phase:

> **A human review event MUST NOT modify, delete, or overwrite any
> model-generated event, any other track file's records, or the source
> media. It may only ADD records to `tracks/review_events.jsonl` (and its
> manifest track entry / record_count).**

Consequences:

- `speech_events.jsonl`, `speaker_events.jsonl`, `audio_events.jsonl`,
  `keyframes.jsonl` are **frozen** with respect to review. A future
  `clulatent review` command writes *only* the review track and the
  manifest bookkeeping for it.
- "Corrected text" does not overwrite the Whisper text in the
  `speech_segment` payload. It lives in the `review_correction` event's
  `corrected_payload`, alongside `original_payload` (copied for
  auditability), pointing at the original via `source_event_ids`.
- Consumers that want "the reviewed truth" resolve it at read time (see
  [State Resolution](#state-resolution-deriving-reviewed-truth)) rather than
  reading a mutated field. There is exactly one source of model output and a
  separate, additive source of human judgment.

Why so strict: it keeps model output reproducible (re-running Whisper
yields the same `speech_events.jsonl`), keeps locking honest (a locked
model track stays locked even after review), and makes review fully
reversible (drop the review track → back to raw model output).

---

## Track File

### New Track: `review_events.jsonl`

A new canonical track at `tracks/review_events.jsonl`, sorted by
`t_start_ms` like every other track. Manifest declaration:

```json
{
  "name": "review_events",
  "file": "tracks/review_events.jsonl",
  "record_count": 0,
  "sorted_by": "t_start_ms",
  "schema_id": "clulatent.track.event_envelope",
  "schema_version": "0.1.0"
}
```

Record ids share a single namespace for the track (`rv_000000`,
`rv_000001`, …) regardless of `type` — the same convention `keyframes.jsonl`
uses for its own ids, and how `speech_events.jsonl` mixes `speech_segment`
and `speech_activity` under one id prefix.

---

## Review States

Every judgment event asserts (or transitions a target toward) one
`review_state`. This is a closed enum, independent of the event `type`
string, so a consumer can filter/aggregate on state without switching on
type:

| State | Meaning |
|---|---|
| `unreviewed` | No human has looked at this yet (the implicit default for any event with no review record; can also be asserted explicitly to walk back a prior state). |
| `needs_review` | Triaged as requiring human attention (flagged, not yet resolved). |
| `uncertain` | A human looked, but could not confidently decide (explicit "I don't know", distinct from not having looked at all). |
| `reviewed` | A human looked and recorded a decision, without that decision being specifically approval/rejection/correction (e.g., a plain acknowledgment, or the umbrella state for `review_session_summary` bookkeeping). |
| `approved` | A human confirmed the target event is correct as-is. |
| `corrected` | A human supplied a corrected value/interpretation for the target event. |
| `rejected` | A human asserts the target event is wrong and should not be trusted downstream. |
| `superseded` | This review event's judgment has itself been replaced by a later review event (set by resolution, never asserted directly by the superseding event's own `review_state`). |

`superseded` is special: it is never the `review_state` a reviewer *writes*
into a new event's payload. It is the state the [resolution
algorithm](#state-resolution-deriving-reviewed-truth) assigns, at read
time, to an *older* review event once a newer one lists it in
`supersedes_event_ids`. Every other state may be asserted directly.

---

## Common Payload Fields

These fields appear, with the same meaning, across every event type below
(exact requiredness per type is called out in each schema):

| Field | Type | Meaning |
|---|---|---|
| `source_event_ids` | `[string]` | Ids of the event(s) being judged/annotated. May span any canonical track (model tracks or `review_events` itself, for chains). |
| `supersedes_event_ids` | `[string]` | Ids of prior `review_events` records this event replaces. Empty array = supersedes nothing. Renders the superseded records' resolved state as `superseded` (see [Review States](#review-states)). |
| `reviewer_id` | `string` | Stable, opaque reviewer identifier (e.g. `"jk"`, `"reviewer-3"`, `"panel-a"`). Also encoded in `producer.name` as `"human:<reviewer_id>"`; duplicated in payload for convenient querying. Length-bounded and sanitized (see [Validation Rules](#validation-rules)). |
| `reviewer_label` | `string \| null` | Optional human-readable display label for `reviewer_id` (a name, a handle, a role like `"senior-reviewer"`). Purely cosmetic — never used as a stable key, never assumed unique. Sanitized before any console rendering. |
| `review_state` | `string \| null` | One of the eight states above. Required for `review_status`/`review_approval`/`review_rejection`/`review_correction`/`review_override`; omitted for `human_note`; not applicable to `review_session_summary` (which instead carries a state histogram — see its schema). |
| `reason` | `string \| null` | Free-text (but length-bounded, sanitized) justification for the decision. Optional everywhere, recommended for `review_rejection` and `review_override`. |
| `corrected_payload` | `object \| null` | Present only on `review_correction`/`review_override`. A **partial patch**: keys are dotted field paths into the target event (`payload.text`, `payload.language`, `t_end_ms`, …), values are the human-supplied corrected values. Paired with `original_payload` for drift detection. |
| `original_payload` | `object \| null` | Present only on `review_correction`/`review_override`. Mirrors the keys of `corrected_payload`, holding the model's original values as they existed at review time — copied in for a self-contained audit trail and for drift detection against re-ingests. |
| `reviewed_at` | `string` (ISO-8601 UTC) | Wall-clock time the human made the decision. Distinct from `t_start_ms`/`t_end_ms`, which are on the *media* timeline. Required on every type. |

`confidence` (the human's self-reported certainty) lives at the envelope
level, not inside `payload` — see [Core Design Principle
5](#5-confidence-means-something-different-for-humans).

---

## Event Types & Schemas

| Type | Purpose | `source_event_ids` |
|---|---|---|
| `review_status` | Lightweight state transition with no accompanying correction — triage, flagging, or plain acknowledgment. | Required, non-empty |
| `review_approval` | Confirms target event(s) are correct as-is. | Required, non-empty |
| `review_rejection` | Asserts target event(s) are wrong and should not be trusted downstream. | Required, non-empty |
| `review_correction` | Supplies a corrected value for specific field(s) of a target event, without deleting the original. | Required, non-empty |
| `review_override` | Replaces a target event's whole interpretation (broader than a field-level correction). | Required, non-empty |
| `human_note` | Freestanding annotation — commentary that is not itself a judgment. | Optional, may be empty |
| `review_session_summary` | Session-level rollup of a batch of review work. | N/A — uses `review_event_ids` instead |

### 1. `review_status`

```json
{
  "id": "rv_000000",
  "type": "review_status",
  "t_start_ms": 30000,
  "t_end_ms": 33000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": null,
  "payload": {
    "source_event_ids": ["ts_000009"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": "Jayden K.",
    "review_state": "needs_review",
    "reason": "audio is muddy here, needs a second pass",
    "reviewed_at": "2026-07-08T15:39:00Z"
  }
}
```

Used for triage: flag something for later attention (`needs_review`), mark
explicit non-decision (`uncertain`), record a plain look-and-acknowledge
(`reviewed`), or walk back a stale state (`unreviewed`). `t_start_ms`/
`t_end_ms` mirror the target event's span.

### 2. `review_approval`

```json
{
  "id": "rv_000001",
  "type": "review_approval",
  "t_start_ms": 1000,
  "t_end_ms": 5000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": 1.0,
  "payload": {
    "source_event_ids": ["ts_000000"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "review_state": "approved",
    "reason": null,
    "reviewed_at": "2026-07-08T15:40:00Z"
  }
}
```

`t_start_ms`/`t_end_ms` mirror the reviewed model event's span (copied, so
the review is time-localizable on the same timeline and satisfies
sorting/duration-bound rules). No new value is asserted — approval means
"trust the original as-is".

### 3. `review_rejection`

```json
{
  "id": "rv_000002",
  "type": "review_rejection",
  "t_start_ms": 8000,
  "t_end_ms": 9500,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": 0.9,
  "payload": {
    "source_event_ids": ["ts_000005"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "review_state": "rejected",
    "reason": "hallucinated_segment: no speech here, Whisper hallucinated text over silence",
    "reviewed_at": "2026-07-08T15:41:00Z"
  }
}
```

Rejection does **not** delete `ts_000005` from `speech_events.jsonl`; it
marks it rejected for [state resolution](#state-resolution-deriving-reviewed-truth).
`reason` is free text but a short leading controlled-vocabulary token
(`hallucinated_segment`, `wrong_language`, `out_of_bounds`,
`misattributed_speaker`, `not_speech`, `duplicate`, …) is recommended for
machine-filterable rejections.

### 4. `review_correction`

```json
{
  "id": "rv_000003",
  "type": "review_correction",
  "t_start_ms": 12000,
  "t_end_ms": 15000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": 1.0,
  "payload": {
    "source_event_ids": ["ts_000008"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "review_state": "corrected",
    "reason": "singular form",
    "original_payload": {"payload.text": "the mitochondria is the powerhouse"},
    "corrected_payload": {"payload.text": "the mitochondrion is the powerhouse"},
    "reviewed_at": "2026-07-08T15:42:00Z"
  }
}
```

- `corrected_payload` / `original_payload` are **partial patches**: only the
  fields actually being corrected appear as keys, using dotted paths
  (`payload.text`, `payload.language`, `payload.speaker_id`, `t_end_ms`, …).
  The correction is descriptive data, not an executable patch operation.
- `original_payload` is copied in at review time so the correction is
  legible without opening the original track, and so validation can detect
  drift (see [Validation Rules](#validation-rules), rule 6).
- Multiple corrections against the same source event (different keys, or
  later ones via `supersedes_event_ids`) are allowed and compose.

### 5. `review_override`

```json
{
  "id": "rv_000004",
  "type": "review_override",
  "t_start_ms": 20000,
  "t_end_ms": 24000,
  "producer": {"name": "human:senior-reviewer", "version": "1.0"},
  "confidence": 0.95,
  "payload": {
    "source_event_ids": ["ts_000011"],
    "supersedes_event_ids": ["rv_000003"],
    "reviewer_id": "senior-reviewer",
    "reviewer_label": "Senior Reviewer",
    "review_state": "corrected",
    "reason": "this isn't speech at all, it's a musical sting; whole segment reclassified",
    "override_kind": "reclassify",
    "original_payload": {"type": "speech_segment"},
    "corrected_payload": {"type": "audio_event", "subtype": "music"},
    "reviewed_at": "2026-07-08T15:45:00Z"
  }
}
```

- `override_kind` (string): e.g. `reclassify` (change what kind of thing
  this is), `respan` (assert different start/end), `merge` (this + siblings
  are one thing), `split` (this is really several).
- `corrected_payload` here plays the same role as in `review_correction`,
  just broader in scope (may replace `type`, not only leaf payload fields).
- `review_state` for an override is normally `corrected`; if the override's
  effect is to declare the target should not exist at all (a `reclassify`
  to "not this kind of event"), `rejected` is also valid — validation
  permits either but requires internal consistency with `corrected_payload`
  (see rule 6a).
- `supersedes_event_ids` commonly points at an earlier, narrower correction
  from a different reviewer, as shown above.

### 6. `human_note`

```json
{
  "id": "rv_000005",
  "type": "human_note",
  "t_start_ms": 0,
  "t_end_ms": 0,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": null,
  "payload": {
    "source_event_ids": [],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "note_text": "Background music throughout makes VAD noisy; consider re-running with a higher silence threshold.",
    "reviewed_at": "2026-07-08T15:52:00Z"
  }
}
```

- `note_text` (string, required): the note itself. This is the one field
  unique to `human_note` — it carries the content that every other type
  puts in the optional `reason`.
  Length-bounded and sanitized like every other free-text field.
- `source_event_ids` **may be empty** — a note can be general commentary
  about the package/session rather than about a specific event. If
  non-empty, the same dangling-reference rule applies as any other type.
- `review_state` is omitted (a note is not itself a judgment and does not
  transition any target's state). If a note accompanies a decision, pair it
  with a separate judgment event via matching `t_start_ms`/`t_end_ms` and
  `source_event_ids`, or use the `reason` field on that judgment event
  instead of a standalone note.
- `t_start_ms == t_end_ms == 0` is valid for a whole-package note with no
  natural timeline anchor; a note about a specific span should mirror that
  span's timestamps instead.

### 7. `review_session_summary`

```json
{
  "id": "rv_000006",
  "type": "review_session_summary",
  "t_start_ms": 0,
  "t_end_ms": 33000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": null,
  "payload": {
    "review_event_ids": ["rv_000000", "rv_000001", "rv_000002", "rv_000003", "rv_000005"],
    "reviewer_id": "jk",
    "reviewer_label": "Jayden K.",
    "session_id": "session-2026-07-08-jk-1",
    "started_at": "2026-07-08T15:38:00Z",
    "ended_at": "2026-07-08T15:53:00Z",
    "counts_by_review_state": {
      "needs_review": 1,
      "approved": 1,
      "rejected": 1,
      "corrected": 1,
      "uncertain": 0,
      "reviewed": 0,
      "unreviewed": 0
    },
    "reason": "First pass over the intro segment."
  }
}
```

- Uses `review_event_ids` (not `source_event_ids`) — it references *review*
  records, not model records, since it is a rollup of a batch of review
  work, not a judgment about model output.
- `t_start_ms`/`t_end_ms` span the reviewed region (min/max of the
  referenced events' timestamps), or `[0, source_duration_ms]` if the
  session covered the whole package.
- `counts_by_review_state` is a convenience aggregate — it must equal the
  actual distribution of `review_state` values across the referenced
  `review_event_ids` (validation rule 9).
- Purely an audit/bookkeeping event: it does not participate in per-target
  [state resolution](#state-resolution-deriving-reviewed-truth) and does
  not itself carry a `review_state`.

---

## State Resolution: Deriving Reviewed Truth

Downstream consumers ("what should I actually trust for event X?") resolve
this deterministically, at read time, without anything being mutated:

1. **Collect** every `review_status` / `review_approval` / `review_rejection`
   / `review_correction` / `review_override` record whose `source_event_ids`
   includes X. (`human_note` and `review_session_summary` are excluded —
   they carry no `review_state`.)
2. **Drop superseded records**: any record whose id appears in another
   record's `supersedes_event_ids` is excluded from the live set; its
   resolved state is `superseded`.
3. **If the live set is empty**, X's resolved state is `unreviewed`.
4. **If the live set has exactly one record**, that record's `review_state`
   is X's resolved state, and (for `review_correction`/`review_override`)
   applying its `corrected_payload` over X's original payload yields the
   resolved payload.
5. **If the live set has more than one record with agreeing
   `review_state`**, resolution is unambiguous; if they disagree (e.g. one
   live `approved` and one live `rejected`, with no `supersedes_event_ids`
   relationship between them), this is **surfaced as a validation warning**
   ("unresolved conflicting reviews for X") — human disagreement is data,
   not something to silently hide. Tooling that needs a single answer
   anyway breaks the tie deterministically by latest `reviewed_at`, but the
   warning is emitted regardless.

This resolution is a *read-time* algorithm. It is fully specified here so
any consumer (a future `clulatent review-status` command, a Rust CLUBIN
compiler, an external tool) computes the same answer for the same package
state. Every resolved state or payload traces back, by construction, to the
exact `review_events` record(s) that produced it, and from there to the
`source_event_ids` those records cite — satisfying "reviewed truth must be
traceable back to original evidence" without needing a separate stored
"truth" event type. Phase 1.9 defines the algorithm; it does not implement
it.

---

## Provenance & the Producer Field

The `producer` field carries the human/machine distinction with zero schema
change:

- Model events: `producer.name` ∈ {`"faster-whisper"`, `"pyannote.audio"`,
  `"ffmpeg"`, …}.
- Review events: `producer.name` = `"human:<reviewer_id>"` (the `human:`
  prefix is the reserved marker). `producer.version` records the *review
  protocol* version, not a model version.

Rationale:
- A single grep/filter (`producer.name` starting with `"human:"`) cleanly
  separates human assertions from machine assertions across every track.
- Reviewer identity is stable and queryable both in `producer.name` and
  (duplicated) in `payload.reviewer_id`.
- No identity/PII policy is imposed beyond: `reviewer_id` is an opaque
  handle, not required to be a real name; `reviewer_label` is a purely
  cosmetic display string. Whether either maps to a real person is out of
  scope and out of the package.

---

## Validation Rules

Additions to `clulatent validate` (design-only; enforced in a future
phase). These fail the package (hard errors) unless noted as warnings.

**Envelope & bounds (inherited, no new code):**

1. Every review event must parse as a valid `EventEnvelope` — negative
   timestamps and `t_end_ms < t_start_ms` are already caught by existing
   Pydantic validators.
2. **Duration bounds (Phase 1.7.1)**: `t_start_ms`/`t_end_ms` must be within
   `source_duration_ms ± EVENT_DURATION_TOLERANCE_MS`. Review events are on
   the media timeline and obey the same bound as every other event,
   including `review_session_summary`.

**Referential integrity:**

3. `source_event_ids` must be present and non-empty for `review_status`,
   `review_approval`, `review_rejection`, `review_correction`,
   `review_override`. For `human_note` it may be empty. Every id present in
   `source_event_ids` (for any type) must resolve to an existing event in
   the package — in **any** canonical track, including `review_events`
   itself. A dangling reference is a hard error.
4. `review_session_summary.review_event_ids` must be non-empty, and every
   id must resolve to an existing `review_events` record. A dangling
   reference is a hard error.
5. `supersedes_event_ids`, when non-empty, must reference existing
   `review_events` records. A review event must not list its own id in its
   own `supersedes_event_ids` (self-supersession is a hard error), and the
   overall supersedes graph across the track must be acyclic (a cycle is a
   hard error).

**Consistency of corrections/overrides:**

6. For `review_correction` and `review_override`: `corrected_payload` and
   `original_payload` must be non-empty and share the exact same set of
   keys. Each key must be a syntactically valid dotted path. Each value in
   `original_payload` must equal the current value at that path in the
   referenced source event — if the model track has since changed
   (re-ingest) so the original no longer matches, this is a hard error: the
   correction was made against a different reality and must be re-reviewed.
   (This is what makes copying `original_payload` in worth the redundancy.)
6a. For `review_override` specifically, `review_state` must be consistent
    with `corrected_payload`: if `corrected_payload` asserts a different
    `type` for the target (a `reclassify`), `review_state` must be
    `corrected` or `rejected`; for any other `override_kind`, `review_state`
    must be `corrected`.
7. A review event's `t_start_ms`/`t_end_ms` should mirror (be contained
   within, allowing tolerance) the span of its `source_event_ids`. A review
   whose time range doesn't overlap what it claims to review is a
   **warning** (it may be legitimate for `respan` overrides).

**Bounded/sanitized labels:**

8. `reviewer_id`, `reviewer_label`, `reason`, `note_text`, `session_id` are
   all length-bounded (a conservative fixed cap, consistent with the
   existing `Limits` philosophy in `security/limits.py`) and must not
   contain NUL bytes or raw control sequences — same lexical class of check
   already applied to path strings in `security/paths.py`. Any of these
   values rendered to a terminal must pass through `safe_console_text`
   first, exactly like every other package-derived string today (no new
   sanitization surface, just new fields flowing through the existing one).

**Session-summary consistency:**

9. `review_session_summary.counts_by_review_state` must equal the actual
   distribution of `review_state` across the events referenced by
   `review_event_ids` (recomputed from those records, not trusted as
   asserted). A mismatch is a hard error — an inaccurate summary is worse
   than no summary.

**Truth coherence (warnings, not failures — human disagreement is data):**

10. Two live (non-superseded) events of contradictory `review_state` (e.g.
    `approved` and `rejected`) targeting the same `source_event_id` →
    **warning**: "unresolved conflicting reviews for X".
11. A `review_correction`/`review_override` whose `corrected_payload`
    disagrees with another live correction for the same target and same
    field, with no `supersedes_event_ids` relationship between them →
    **warning**.

**Producer marking:**

12. Every record in `review_events.jsonl` must have `producer.name`
    beginning with `"human:"`. A machine-produced record in the review
    track is a hard error (and, symmetrically, a `"human:"`-prefixed record
    in a *model* track is a hard error — humans write only to the review
    track).

**Immutability (cross-track, hard error):**

13. Presence of a `review_events.jsonl` track must not correlate with any
    change to other tracks' `record_count` or record bytes relative to what
    locking recorded. Where a package is locked (Phase 1.7.5), `verify-lock`
    over the model tracks must still pass after review; only the review
    track and manifest bookkeeping may differ. (Formally: review is
    additive-only.)

---

## Receipts & Non-Fatal Warnings

A future `clulatent review` operation records receipts in the existing
append-only `receipts/ingest.jsonl` (reusing the Phase 1.7.1 `warnings`
field on `ReceiptLog.add`):

```python
receipts.add(
    operation="review",
    status="success" | "partial" | "failure",
    source_path="tracks/review_events.jsonl",
    files_created=["tracks/review_events.jsonl"],
    errors=[...],
    warnings=[
        "review rv_000004 supersedes rv_000003 by a different reviewer (senior-reviewer over jk)",
        "unresolved conflict: rv_000010 (approved) and rv_000011 (rejected) both live for ts_000042",
    ],
)
```

Non-fatal situations that produce a warning rather than aborting:
- A review event superseding another authored by a different reviewer
  (escalation trail).
- Detected unresolved conflicts (rules 10–11 above).
- A correction whose `original_payload` still matches but whose source
  event has low model confidence (informational: "human confirmed a
  low-confidence model output").
- A `human_note` with empty `source_event_ids` (general commentary — worth
  flagging for provenance, not an error).

Fatal situations (receipt `status: "failure"`, no review track committed)
map to the hard-error validation rules: dangling `source_event_ids`,
`original_payload` drift, cyclic `supersedes_event_ids`, out-of-bounds
timestamps, a machine record in the review track, or a
`review_session_summary` whose counts don't match its referenced events.

---

## Interaction With Locking (Phase 1.7.5)

Review and locking are complementary, and Phase 1.9 introduces **no new
locking behavior** — it uses exactly what Phase 1.7.5 already provides:

- **Lock first, then review.** A package locked at ingest time keeps its
  model tracks provably unchanged. Adding review events changes *only*
  `review_events.jsonl` + manifest bookkeeping.
- **Review a locked package on a copy, or plan to re-lock.** Per
  `docs/LOCKING.md`, there is deliberately no `unlock` command. If a
  package is already locked, the recommended path is either:
  1. work on a copy of the package with `lock/` removed (per the existing
     "no `unlock` command" workflow), or
  2. append `tracks/review_events.jsonl` directly to the original package,
     accepting that `clulatent verify-lock` will now report the review
     track as an untracked/"extra" file in `--strict` mode — this is the
     **correct, expected signal** that reviewed content was added after
     the original freeze, not a bug.
- **Re-lock to seal the reviewed state, using the existing `--force`
  flag.** After a review pass, `clulatent lock` is re-run over the package.
  Because the package is already locked, `clulatent lock` **refuses by
  default** (Phase 1.7.5's re-lock guard) — this is intentional and is not
  bypassed or special-cased by Phase 1.9. The reviewer must explicitly pass
  `clulatent lock --force` to produce a *new* lock manifest that now also
  covers `review_events.jsonl`, sealing the human-blessed state. The old
  lock (model-only) and new lock (model + review) are both meaningful,
  independently verifiable snapshots. No new `--force`-like flag is
  introduced for review; the existing one is reused as-is.
- Review never provides a reason to weaken or bypass locking. Consistent
  with Phase 1.7.5, there is still no unlock path; review is additive, and
  locking remains tamper-evidence, not access control.

---

## Non-Goals

Explicitly **out of scope** for Phase 1.9:

- **No UI.** No review app, web viewer, keyboard shortcuts, or annotation
  frontend. This phase defines the on-disk representation only; how a human
  *enters* a review is a separate concern.
- **No ML dependencies.** No model, no active learning, no
  "suggest a correction" automation. Humans author reviews; machines do
  not.
- **No semantic events.** Reviews judge existing perception events (speech,
  speaker, audio, keyframes). They do not introduce meaning, roles,
  sentiment, or intent.
- **No CLUBIN.** No compiled binary, no Rust runtime integration. State
  resolution is *specified* for a future compiler to consume, not built.
- **No mutation of model tracks or source media.** The immutability rule is
  absolute (see [that section](#the-immutability-rule)).
- **No automatic truth claims.** Nothing in this phase infers, guesses, or
  auto-resolves a decision — every `review_state` is asserted by an
  explicit human-authored event. Conflicting live reviews are surfaced, not
  silently averaged or auto-picked.
- **No identity verification.** `reviewer_id`/`reviewer_label` are opaque,
  self-declared handles. There is no authentication, login, or verification
  that a given reviewer id corresponds to a real, consistent person.
- **No permissions system.** Anyone able to write to the package can author
  a review event under any `reviewer_id`. There is no access control over
  who may review, approve, or override.
- **No cryptographic signatures.** Review events are not signed. Trust in
  "who really wrote this review" is exactly as strong (and no stronger)
  than trust in whoever had filesystem access — the same caveat
  `docs/LOCKING.md` states for the integrity lock itself. A future phase
  could layer signing on top of both without changing anything specified
  here.
- **No cloud services, no network, no auto-download.** Review is a local,
  file-only operation.
- **No workflow engine.** No assignment, queues, SLAs, or multi-stage
  approval routing. `supersedes_event_ids` gives a minimal escalation
  trail; anything richer is out of scope.
- **No inter-package review.** Reviews live inside the package they judge;
  there is no cross-package or shared review store.
- **No change to package locking behavior.** Phase 1.9 reuses Phase 1.7.5's
  `lock`/`verify-lock`/`lock-status`/`--force` exactly as they exist today.

---

## Implementation Timeline

### Phase 1.9: Schema & Design ✓ (This Document)

- Define the review event types, the `review_events.jsonl` track, the
  `review_state` enum, and the immutability rule.
- Specify the state-resolution algorithm.
- Specify validation rules (referential integrity, immutability, label
  sanitization, conflict warnings).
- Add README roadmap note.
- Run test suite to confirm no behavior changed.
- Design-only commit; no runtime code.

### Phase 1.9.x (Future): Review Track Support

- Extend `validate.py` with the referential-integrity / immutability /
  label-bounding / conflict rules (most bounds checks are already
  inherited from Phase 1.7.1).
- Teach `reindex` to index review events (tolerant reader, same as other
  tracks).
- A `clulatent review add …` write command (append-only, operation-locked
  like `lock`/`reindex`).
- Tests: each event type's round-trip, `supersedes_event_ids` chains,
  dangling-reference rejection, `original_payload` drift detection,
  immutability enforcement, label sanitization.

### Phase 2.0+ (Future): Truth Consumption

- A `clulatent review-status` (or similar) read command implementing the
  state-resolution algorithm.
- Resolution-aware `query`/`timeline` (show blessed output, flag
  conflicts).
- CLUBIN compiler consumes resolved state as canonical.

---

## Worked Examples

### 1. Correcting a Whisper transcript

See [`review_correction`](#4-review_correction) above: `ts_000008`
("the mitochondria is the powerhouse") corrected to "the mitochondrion is
the powerhouse" via `rv_000003`. `speech_events.jsonl` is never touched.

### 2. Rejecting a bad speech segment

See [`review_rejection`](#3-review_rejection) above: `ts_000005`
(hallucinated text over silence) marked `rejected` via `rv_000002`. It
remains in `speech_events.jsonl`, byte-identical; downstream resolution
drops it from trusted output.

### 3. Approving a speaker label

```json
{
  "id": "rv_000007",
  "type": "review_approval",
  "t_start_ms": 1000,
  "t_end_ms": 5000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": 0.9,
  "payload": {
    "source_event_ids": ["sp_000001"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "review_state": "approved",
    "reason": "voice matches speaker_0 from the intro",
    "reviewed_at": "2026-07-08T15:55:00Z"
  }
}
```

`sp_000001` is a Phase 1.8 `speaker_label` event — review freely crosses
into the speaker track, same as any other model track.

### 4. Adding a human note

See [`human_note`](#6-human_note) above: a whole-package observation
(`rv_000005`) about background music affecting VAD quality, with empty
`source_event_ids`.

### 5. Superseding a model event with a reviewed correction

```json
{
  "id": "rv_000008",
  "type": "review_correction",
  "t_start_ms": 12000,
  "t_end_ms": 15000,
  "producer": {"name": "human:senior-reviewer", "version": "1.0"},
  "confidence": 1.0,
  "payload": {
    "source_event_ids": ["ts_000008"],
    "supersedes_event_ids": ["rv_000003"],
    "reviewer_id": "senior-reviewer",
    "reviewer_label": "Senior Reviewer",
    "review_state": "corrected",
    "reason": "double-checked against a cleaner audio pass; mitochondrion is still right, but language tag was wrong",
    "original_payload": {"payload.text": "the mitochondria is the powerhouse", "payload.language": "es"},
    "corrected_payload": {"payload.text": "the mitochondrion is the powerhouse", "payload.language": "en"},
    "reviewed_at": "2026-07-08T16:00:00Z"
  }
}
```

`rv_000008` lists `rv_000003` in `supersedes_event_ids`. Resolution now
treats `rv_000003` as `superseded` and `rv_000008` as the live, resolved
`corrected` state for `ts_000008` — a full escalation trail (`jk`'s first
pass, then `senior-reviewer`'s refinement) remains readable in the track.

### End-to-end resolved view

For `ts_000008`, walking the chain: `rv_000003` (corrected, by `jk`) →
superseded by `rv_000008` (corrected, by `senior-reviewer`) → resolved
state is `corrected`, resolved text is "the mitochondrion is the
powerhouse", resolved language is `en`. Every step — who, when, why, and
against exactly which prior event — is reconstructable from
`review_events.jsonl` alone, and `speech_events.jsonl` never changed.

---

## Summary: What Phase 1.9 Delivers

1. **A review track** (`tracks/review_events.jsonl`) using the existing
   event envelope — no schema break.
2. **Seven event types**: `review_status`, `review_approval`,
   `review_rejection`, `review_correction`, `review_override`,
   `human_note`, `review_session_summary`.
3. **An explicit `review_state` enum**: `unreviewed`, `needs_review`,
   `uncertain`, `reviewed`, `approved`, `corrected`, `rejected`,
   `superseded`.
4. **An absolute immutability rule**: review is additive-only; model
   tracks and source media are never mutated.
5. **A deterministic state-resolution algorithm** for downstream
   consumers, fully specified (not implemented), always traceable back to
   `source_event_ids`.
6. **Provenance via `producer.name = "human:<reviewer_id>"`** plus payload
   `reviewer_id`/`reviewer_label` — a zero-schema-change human/machine
   split.
7. **Validation rules**: referential integrity, `original_payload` drift
   detection, acyclic `supersedes_event_ids`, label sanitization,
   immutability enforcement, and conflict *warnings* that surface (never
   hide) human disagreement.
8. **Zero change to locking behavior**: reuses Phase 1.7.5's
   `lock`/`verify-lock`/`lock-status`/`--force` exactly as they exist,
   including the requirement to explicitly pass `--force` to re-lock after
   review.
9. **Design-only**: no UI, no ML, no CLUBIN, no runtime code, no behavior
   change.
