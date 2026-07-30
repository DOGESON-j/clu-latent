# CLULatent Phase 1.10: Caption / Subtitle Import

**Status**: Design-only. No implementation. No runtime behavior. No new
dependencies. No UI. Schemas, field definitions, and validation rules only.

**Motivation**: Phase 1.7C produces `speech_segment` events by running
Whisper over the source audio. But many videos already carry captions or
subtitles — `.srt` sidecars, `.vtt` sidecars, or caption/subtitle streams
muxed directly into the container. These may be human-authored,
auto-generated, edited, translated, partial, or flatly wrong. Today
CLULatent has no way to bring that evidence into a package at all. This
phase defines how CLULatent **imports caption/subtitle content as
first-class, timestamped speech evidence** — with its own track, its own
provenance, and no assumption that it agrees with (or is more/less
trustworthy than) Whisper's output.

The guiding principle, carried over from Phase 1.7C/1.8/1.9: **an imported
caption is evidence, authored by an external producer, sitting in its own
track next to (not merged into) every other speech-related track.** Nothing
about importing a caption asserts it is correct, and nothing about it
overwrites or replaces `speech_events.jsonl`. Reconciling disagreeing
sources of speech evidence is deferred to human review (Phase 1.9) or a
future resolution phase — never done silently at import time.

---

## Table of Contents

1. [Core Design Principles](#core-design-principles)
2. [Supported Input Types](#supported-input-types)
3. [Track File & Package Layout](#track-file--package-layout)
4. [Producer Convention](#producer-convention)
5. [Event Types & Schemas](#event-types--schemas)
6. [Payload Field Reference](#payload-field-reference)
7. [Validation Rules](#validation-rules)
8. [Receipts & Non-Fatal Warnings](#receipts--non-fatal-warnings)
9. [Interaction With Phase 1.9 Review](#interaction-with-phase-19-review)
10. [Interaction With Whisper (`speech_events`)](#interaction-with-whisper-speech_events)
11. [Interaction With Locking (Phase 1.7.5)](#interaction-with-locking-phase-175)
12. [Non-Goals](#non-goals)
13. [Implementation Timeline](#implementation-timeline)
14. [Worked Examples](#worked-examples)
15. [Summary](#summary-what-phase-110-delivers)

---

## Core Design Principles

### 1. Captions are a separate track, not a merge into `speech_events`

Imported captions do **not** become `speech_segment` records and do not
share `speech_events.jsonl`. They live in a new track,
`tracks/caption_events.jsonl` (see [rationale](#track-file--package-layout)
below). This keeps every track single-producer-shaped: `speech_events.jsonl`
stays "what Whisper said," `caption_events.jsonl` becomes "what the caption
file said," and neither has to be diffed apart after the fact.

### 2. Reuse the existing event envelope

Every caption event is a canonical `EventEnvelope` (`event.py`) — the same
container used by `keyframes`, `audio_events`, `speech_events`, and the
Phase 1.8/1.9 `speaker_events`/`review_events` designs:

```python
{
  "id": "string (e.g., 'cap_000000')",
  "type": "string (see Event Types below)",
  "t_start_ms": int (>= 0),
  "t_end_ms": int (>= 0, > t_start_ms for caption_segment),
  "producer": {"name": "caption-import:<source_format>", "version": "importer-version"},
  "confidence": float | null,   # usually null — see Payload Field Reference
  "payload": { ... type-specific, see below ... }
}
```

No new top-level envelope schema. No new envelope-level fields. The
caption/model/human distinction lives entirely in `producer.name` and the
`type` string, exactly as Phase 1.8 (model) and Phase 1.9 (`human:`)
distinguish producers.

### 3. The original file is archived, not re-derived

Just as `sources/source.mp4` embeds a byte-for-byte copy of the source
video with a `.sha256` sidecar, every imported caption/subtitle *file* is
archived byte-for-byte alongside its hash (see [Track File & Package
Layout](#track-file--package-layout)). Canonical `caption_segment` records
carry the *parsed, sanitized* cue text; the raw original (with all its
markup, positioning, and styling) is always recoverable from the archived
copy. No dual-storage of "raw vs. cleaned" text inside the event itself.

### 4. Import is evidence, not truth

Nothing about the act of importing a caption file asserts it is accurate.
It carries exactly the same epistemic status as a Whisper `speech_segment`:
a producer's output, with optional `confidence`, subject to human review
(Phase 1.9) like any other track. See [Interaction With Phase 1.9
Review](#interaction-with-phase-19-review).

### 5. No silent reconciliation with Whisper

`caption_events.jsonl` and `speech_events.jsonl` may describe the same time
range with different text, different language, or different segmentation.
Phase 1.10 does not compare, merge, vote between, or prefer one over the
other. See [Interaction With
Whisper](#interaction-with-whisper-speech_events).

---

## Supported Input Types

Phase 1.10 designs for four input shapes; only the file-syntax ones
(`srt`, `vtt`) are given a dedicated `source_format` value, since
"sidecar" vs. "embedded" and "translation" are *properties* layered on top
(see [Payload Field Reference](#payload-field-reference)):

| Input | `source_format` | `provenance.import_method` (example) | Notes |
|---|---|---|---|
| `.srt` sidecar file | `srt` | `cli:caption-import` | SubRip; no native language/style metadata — importer strips numbering + basic `<i>`/`<b>` tags. |
| `.vtt` sidecar file | `vtt` | `cli:caption-import` | WebVTT; may carry `REGION`/cue settings and a file-level `Kind`/`Language` header — importer strips cue settings (positioning, `<c>` voice tags) from canonical `text`. |
| Embedded caption/subtitle stream (e.g. `mov_text`, muxed WebVTT, EIA-608) | `embedded` | `cli:caption-import` (via `ffprobe`/`ffmpeg` stream extraction) | Demuxed from the container by `ffprobe`/`ffmpeg` (reusing the existing hardened subprocess wrapper — no new dependency) before parsing; stream language tag, if present in the container, populates `language`. |
| Translated subtitle file (any of the above) | same as underlying file's format | same | Not a distinct format — a translated `.srt`/`.vtt` is still `srt`/`vtt`; translation is expressed via `is_translation`/`translated_from_language` (see below), not a new `source_format` value. |

A future importer command is expected to be named `clulatent
caption-import <package> <file> [--format srt|vtt|embedded] [--language ..]
[--translation-of <language>]` (not built in this phase — naming only, to
anchor the CLI surface for Phase 1.10.x).

---

## Track File & Package Layout

### New Track: `caption_events.jsonl`

```json
{
  "name": "caption_events",
  "file": "tracks/caption_events.jsonl",
  "record_count": 0,
  "sorted_by": "t_start_ms",
  "schema_id": "clulatent.track.event_envelope",
  "schema_version": "0.1.0"
}
```

Record ids share a single namespace for the track (`cap_000000`,
`cap_000001`, …) regardless of `type`, the same convention every other
track uses.

### New archive directory: `sources/captions/`

Mirrors the existing `sources/source.mp4` + `sources/source.sha256`
pattern — every imported caption/subtitle file is copied in whole,
hashed, and never re-derived:

```txt
example.clulatent/
  sources/
    source.mp4
    source.sha256
    captions/
      001_original.srt
      001_original.sha256
      002_fr_translated.vtt
      002_fr_translated.sha256
  tracks/
    caption_events.jsonl
```

Filenames are `<3-digit index>_<sanitized original basename>` — the index
matches `payload.source_file`'s ordering and gives deterministic, collision-
free naming without trusting the original filename's uniqueness.

### Manifest addition: `caption_sources`

A new manifest list, parallel to `tracks`, recording one entry per
imported file (design proposal — not a code change in this phase):

```json
{
  "caption_sources": [
    {
      "index": 1,
      "stored_path": "sources/captions/001_original.srt",
      "sha256_path": "sources/captions/001_original.sha256",
      "source_format": "srt",
      "language": "en",
      "is_translation": false,
      "translated_from_language": null,
      "imported_at": "2026-07-08T16:10:00Z"
    }
  ]
}
```

This is the manifest-level twin of `payload.provenance` on individual
`caption_segment` records — it lets `inspect`/`validate` enumerate imported
files without scanning the whole track.

---

## Producer Convention

Every `caption_events.jsonl` record's `producer.name` follows
`"caption-import:<source_format>"` (e.g. `"caption-import:srt"`,
`"caption-import:vtt"`, `"caption-import:embedded"`). `producer.version`
records the importer's version, not a model version — there is no ML
model involved in caption import.

This is the same zero-schema-change pattern Phase 1.8 uses for model
producers and Phase 1.9 uses for `"human:<reviewer_id>"`: one more
reserved `producer.name` prefix, one more thing a single grep can isolate
across every track.

---

## Event Types & Schemas

| Type | Purpose | `source_event_ids` |
|---|---|---|
| `caption_segment` | One imported caption/subtitle cue, timestamped. | N/A (this *is* the source evidence) |
| `caption_source_summary` | Rollup of one imported file's cues. | N/A — uses `caption_event_ids` instead |
| `caption_gap` | Explicit flag for a detected timing gap between two consecutive cues from the same source. | Required, exactly 2 ids |
| `caption_overlap` | Explicit flag for two cues whose time ranges overlap. | Required, exactly 2 ids |
| `caption_language_note` | Annotation about a language declaration mismatch or absence, sourced from format/container metadata only (never inferred from cue text — see [Non-Goals](#non-goals)). | Optional, may be empty |

### 1. `caption_segment`

```json
{
  "id": "cap_000000",
  "type": "caption_segment",
  "t_start_ms": 1000,
  "t_end_ms": 4200,
  "producer": {"name": "caption-import:srt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "text": "Welcome back to the show.",
    "language": "en",
    "source_format": "srt",
    "source_file": "sources/captions/001_original.srt",
    "caption_index": 0,
    "speaker_hint": null,
    "styling_removed": false,
    "is_translation": false,
    "translated_from_language": null,
    "provenance": {
      "import_method": "cli:caption-import",
      "imported_at": "2026-07-08T16:10:00Z",
      "original_filename": "original.srt"
    }
  }
}
```

- `text` is the parsed, sanitized cue text — SRT numbering and basic
  markup already stripped, so `styling_removed` is `false` here (nothing
  to strip in this particular cue).
- `caption_index` is the cue's 0-based position within its own source
  file, distinct from the envelope `id`, which is global to the track.

### 2. `caption_source_summary`

```json
{
  "id": "cap_000041",
  "type": "caption_source_summary",
  "t_start_ms": 0,
  "t_end_ms": 598000,
  "producer": {"name": "caption-import:srt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "caption_event_ids": ["cap_000000", "cap_000001", "..."],
    "source_file": "sources/captions/001_original.srt",
    "source_format": "srt",
    "language": "en",
    "is_translation": false,
    "translated_from_language": null,
    "cue_count": 40,
    "gap_count": 3,
    "overlap_count": 0,
    "imported_at": "2026-07-08T16:10:00Z"
  }
}
```

- Uses `caption_event_ids` (not `source_event_ids`) — it references
  *caption* records from this import, not events in another track,
  mirroring Phase 1.9's `review_session_summary.review_event_ids`.
- `cue_count`/`gap_count`/`overlap_count` are convenience aggregates that
  must equal the actual counts of associated `caption_segment`/
  `caption_gap`/`caption_overlap` records for this `source_file`
  (validation rule 9 — same "don't trust the summary" posture as Phase
  1.9 rule 9).
- `t_start_ms`/`t_end_ms` span the whole imported file (min/max cue
  timestamps).

### 3. `caption_gap`

```json
{
  "id": "cap_000015",
  "type": "caption_gap",
  "t_start_ms": 42000,
  "t_end_ms": 46500,
  "producer": {"name": "caption-import:srt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "source_event_ids": ["cap_000012", "cap_000013"],
    "source_file": "sources/captions/001_original.srt",
    "source_format": "srt",
    "gap_ms": 4500
  }
}
```

- Flags a timing gap between two consecutive cues from the same
  `source_file` at or above an importer-configurable threshold (design
  proposal: default 2000 ms, not fixed by this document). This is a
  *timing* observation only — it does not assert the gap is silence,
  missing coverage, or an error; that judgment is left to human review or
  cross-referencing against `speech_events`/`audio_events`.
- `t_start_ms`/`t_end_ms` span the gap itself (preceding cue's end to
  following cue's start).

### 4. `caption_overlap`

```json
{
  "id": "cap_000022",
  "type": "caption_overlap",
  "t_start_ms": 88000,
  "t_end_ms": 88800,
  "producer": {"name": "caption-import:vtt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "source_event_ids": ["cap_000030", "cap_000031"],
    "source_file": "sources/captions/002_fr_translated.vtt",
    "source_format": "vtt",
    "overlap_ms": 800
  }
}
```

- Flags two cues (normally from the same `source_file`) whose
  `[t_start_ms, t_end_ms)` ranges intersect — legitimate for
  karaoke-style dual-line VTT, suspicious otherwise. Represents the
  overlap explicitly rather than leaving it as an unexplained anomaly
  (see validation rule 7).
- `t_start_ms`/`t_end_ms` span exactly the overlapping region.

### 5. `caption_language_note`

```json
{
  "id": "cap_000000",
  "type": "caption_language_note",
  "t_start_ms": 0,
  "t_end_ms": 0,
  "producer": {"name": "caption-import:vtt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "source_event_ids": [],
    "source_file": "sources/captions/003_auto.vtt",
    "declared_language": null,
    "container_language": "en",
    "note_text": "VTT file header declared no Language:, but the container's embedded subtitle stream reports 'en'; language on caption_segment records was populated from the container tag."
  }
}
```

- **Only ever populated from declared metadata already present in the
  format or container** (a VTT `Language:` header, an SRT filename
  convention, an `ffprobe` stream `language` tag) — **never** from
  inferring language out of the cue text itself. Doing the latter would
  require an NLP/language-ID dependency, which this phase explicitly does
  not add (see [Non-Goals](#non-goals)).
- `source_event_ids` may be empty (a whole-file note, as here) or
  reference specific `caption_segment` ids if only some cues are
  affected.
- `t_start_ms == t_end_ms == 0` is valid for a whole-file note with no
  natural per-cue anchor, same convention as Phase 1.9's `human_note`.

---

## Payload Field Reference

Fields shared across `caption_segment` (and, where noted, the other
types):

| Field | Type | Meaning |
|---|---|---|
| `text` | `string` | The cue's parsed, sanitized text. Required, non-empty, length-bounded. `caption_segment` only. |
| `language` | `string \| null` | BCP-47-ish tag (`en`, `en-US`, `fr`) for this cue. Populated from file/container metadata when declared; `null` if genuinely undeclared (never guessed from text — see [Non-Goals](#non-goals)). |
| `source_format` | `string` (enum) | One of `srt`, `vtt`, `embedded`. Closed set — see [validation rule 4](#validation-rules). |
| `source_file` | `string` | Package-relative path into `sources/captions/`, resolved through the existing `security/paths.py` containment check — same treatment as `payload.path` on `keyframe` events. |
| `caption_index` | `int` | 0-based position of this cue within its own source file. `caption_segment` only. |
| `speaker_hint` | `string \| null` | Best-effort speaker label lifted from textual caption conventions (`"[John]: ..."`, `"SPEAKER 2:"`, leading `">> "`). A hint only — does **not** create or imply a Phase 1.8 `speaker_label` event. |
| `styling_removed` | `bool` | Whether the importer stripped markup/positioning/styling (`<i>`, `<c>`, cue settings, `REGION` blocks, `X-TIMESTAMP-MAP`, …) to produce `text`. The stripped original is always recoverable from the archived file in `sources/captions/`, never duplicated in the event. |
| `is_translation` | `bool` | Whether this cue/file is a translation of another language's captions. |
| `translated_from_language` | `string \| null` | Required (non-null) when `is_translation` is `true`; must be `null` when `false` (validation rule 6). |
| `provenance` | `object` | `{"import_method": string, "imported_at": ISO-8601, "original_filename": string}` — *how and when* this got into the package, distinct from *where the file lives now* (`source_file`) and *what syntax it's in* (`source_format`). `original_filename` is cosmetic only (sanitized, never trusted as a stable key, same posture as Phase 1.9's `reviewer_label`). |
| `source_event_ids` | `[string]` | Only on `caption_gap`/`caption_overlap`/`caption_language_note` — the `caption_segment` id(s) the record concerns. |

`confidence` (envelope-level) is **usually `null`** for caption import:
unlike Whisper (a real probability) or human review (self-reported
certainty), subtitle formats generally carry no per-cue confidence value.
It is only ever set to a non-null value when the *source itself* declares
one (e.g. some embedded ASR-derived caption streams carry a per-cue
confidence in their container metadata) — the importer must never
synthesize a confidence score. This mirrors the "confidence means
something different" principle from Phase 1.9 §5, extended to a third
producer kind.

---

## Validation Rules

Additions to `clulatent validate` (design-only; enforced in a future
phase). These fail the package (hard errors) unless noted as warnings.

**Envelope & bounds (inherited, no new code):**

1. Every caption event must parse as a valid `EventEnvelope` — negative
   timestamps and `t_end_ms < t_start_ms` are already caught by existing
   Pydantic validators.
2. **Duration bounds (Phase 1.7.1)**: `t_start_ms`/`t_end_ms` must be
   within `source_duration_ms ± EVENT_DURATION_TOLERANCE_MS`, exactly like
   every other event.
3. `caption_segment` tightens the base envelope: `t_end_ms` must be
   **strictly greater than** `t_start_ms` (a zero-duration cue is a hard
   error). `caption_gap`/`caption_overlap` inherit the base `>=` rule
   only (a same-instant gap/overlap boundary is degenerate but not
   invalid).

**Path & format safety:**

4. `source_file` must resolve through `resolve_in_package` exactly like
   `payload.path` on `keyframe` events — no absolute paths, no `..`, no
   symlink escape. A path outside `sources/captions/` is a hard error.
5. `source_format` must be one of the closed enum `{srt, vtt, embedded}`.
   An unrecognized value is a hard error — adding a new format is a
   schema-version bump, not a silent accept.
6. `is_translation`/`translated_from_language` must be paired: if
   `is_translation` is `true`, `translated_from_language` must be
   non-null; if `false`, it must be `null`. An unpaired claim is a hard
   error.

**Referential integrity:**

7. Two live `caption_segment` records from the same `source_file` with
   overlapping `[t_start_ms, t_end_ms)` ranges, with no `caption_overlap`
   record whose `source_event_ids` cites both, produce a **warning**
   ("unexplained caption overlap"). Emitting the `caption_overlap` record
   at import time silences it.
8. `caption_gap`/`caption_overlap` `source_event_ids` must contain
   exactly two ids, both resolving to existing `caption_segment` records
   from the same `source_file`. Self-reference (both ids equal) or a
   dangling id is a hard error.
9. `caption_source_summary.caption_event_ids` must be non-empty, every id
   must resolve to an existing `caption_events` record for that
   `source_file`, and `cue_count`/`gap_count`/`overlap_count` must equal
   the actual, recomputed counts — a mismatch is a hard error (an
   inaccurate summary is worse than no summary, same posture as Phase
   1.9 rule 9).

**Archive integrity:**

10. Every distinct `source_file` referenced by any `caption_events`
    record must have a corresponding archived copy under
    `sources/captions/` with a matching `.sha256` sidecar, exactly like
    the existing `source.mp4`/`source.sha256` check. A missing archive or
    a hash mismatch is a hard error.

**Bounded/sanitized labels:**

11. `text`, `provenance.original_filename`, and `note_text` are all
    length-bounded and must not contain NUL bytes or raw control
    sequences — the same lexical class of check already applied to path
    strings (`security/paths.py`) and Phase 1.9's free-text fields. Any
    value rendered to a terminal passes through `safe_console_text`
    first; no new sanitization surface.

**Producer marking:**

12. Every record in `caption_events.jsonl` must have `producer.name`
    beginning with `"caption-import:"`, and the suffix after the colon
    must equal that record's `source_format` (via the record itself for
    `caption_segment`, or the associated file's format for the other
    types). A mismatched or missing prefix is a hard error, mirroring
    Phase 1.9 rule 12's `"human:"` check.

**Immutability (cross-track, hard error):**

13. Importing captions must not correlate with any change to
    `speech_events.jsonl`, `keyframes.jsonl`, `audio_events.jsonl`, or
    `review_events.jsonl` record counts or bytes. Caption import is
    additive-only to `caption_events.jsonl` + `sources/captions/` +
    manifest bookkeeping — the same immutability posture Phase 1.9
    established for review.

---

## Receipts & Non-Fatal Warnings

A future `clulatent caption-import` operation records receipts in the
existing append-only `receipts/ingest.jsonl` (reusing the Phase 1.7.1
`warnings` field on `ReceiptLog.add`):

```python
receipts.add(
    operation="caption_import",
    status="success" | "partial" | "failure",
    source_path="sources/captions/001_original.srt",
    files_created=[
        "sources/captions/001_original.srt",
        "sources/captions/001_original.sha256",
        "tracks/caption_events.jsonl",
    ],
    errors=[...],
    warnings=[
        "unexplained caption overlap: cap_000030/cap_000031 (800ms)",
        "no declared language in source file or container; caption_segment.language left null",
    ],
)
```

Non-fatal situations that produce a warning rather than aborting:
- An unexplained overlap not accompanied by a `caption_overlap` record
  (rule 7).
- No declared language anywhere in the file/container (language stays
  `null` — not a failure, just an unresolved fact).
- A `caption_gap` at or above the configured threshold (informational,
  not inherently an error — long gaps are normal in many videos).

Fatal situations (receipt `status: "failure"`, nothing committed) map to
the hard-error validation rules: bad `source_file` path, unrecognized
`source_format`, unpaired translation fields, dangling
`caption_gap`/`caption_overlap` references, a summary whose counts don't
match, a missing/mismatched archive hash, or a missing/mismatched
`producer.name` prefix.

---

## Interaction With Phase 1.9 Review

Caption import introduces **zero new review machinery** — `review_events`
already supports referencing any track's event ids via
`source_event_ids`, so `caption_segment`/`caption_gap`/`caption_overlap`/
`caption_language_note` ids work exactly like `speech_segment` or
`speaker_label` ids did in Phase 1.9's examples:

- **Approve** a caption cue as accurate: `review_approval` with
  `source_event_ids: ["cap_000000"]`.
- **Correct** a caption cue's text: `review_correction` with
  `original_payload`/`corrected_payload` keyed by dotted paths
  (`payload.text`, `payload.language`, …) into the `caption_segment`,
  exactly as shown for `speech_segment` in Phase 1.9 §4.
- **Reject** a caption cue (e.g. garbled auto-caption text): `review_rejection`.
- **Supersede** a caption's interpretation entirely: `review_override`.

Imported captions are evidence, not automatically truth — the same
principle Phase 1.9 established for Whisper output applies identically
here, with no special-casing.

---

## Interaction With Whisper (`speech_events`)

`caption_events.jsonl` and `speech_events.jsonl` are independent,
co-equal evidence tracks that may describe overlapping time ranges with
different text, different segmentation boundaries, or different declared
languages. Phase 1.10 deliberately:

- **Does not merge them.** No import-time step reads `speech_events.jsonl`
  or writes into it, and no step reads `caption_events.jsonl` while
  producing `speech_events.jsonl`. `--transcribe` (Whisper) and
  `caption-import` are independent, order-agnostic operations.
- **Does not silently prefer one.** Neither track is marked more
  authoritative than the other anywhere in the schema. A consumer
  wanting "the transcript" must consult both, or wait for human review to
  establish a resolved view.
- **Preserves both sources of evidence** when they disagree. Disagreement
  is not an error at import time — it is simply two producers' output
  coexisting, discoverable by any consumer that queries both tracks for
  the same time range.
- **Defers resolution.** Reconciling `caption_segment` vs. `speech_segment`
  disagreement (which one is right, or whether both are partially right)
  is out of scope for this phase. The mechanism that would express that
  judgment already exists — a Phase 1.9 review event can reference ids
  from *both* tracks in `source_event_ids` — but Phase 1.10 does not
  extend the [state-resolution algorithm](PHASE_1_9_HUMAN_REVIEW_CORRECTION.md#state-resolution-deriving-reviewed-truth)
  to reason across tracks; that remains a future design question.

---

## Interaction With Locking (Phase 1.7.5)

Caption import is, like Phase 1.9 review, a canonical-track-modifying
operation, and Phase 1.10 introduces **no new locking behavior** — it
follows exactly the same shape Phase 1.9 established:

- **Lock captures a point in time.** A package locked after ingest (and
  optionally after Whisper/VAD) has its model tracks provably unchanged.
  Caption import, run afterward, changes `caption_events.jsonl` +
  `sources/captions/*` + manifest bookkeeping only.
- **Import into a locked package on a copy, or plan to re-lock.** Per
  `docs/LOCKING.md`, there is deliberately no `unlock` command. If the
  package is already locked, the recommended path is either:
  1. work on a copy of the package with `lock/` removed, or
  2. import directly, accepting that `clulatent verify-lock --strict`
     will report `caption_events.jsonl` and the new
     `sources/captions/*` files as untracked/"extra" — the **correct,
     expected signal** that caption evidence was added after the
     original freeze, not a bug.
- **Re-lock to seal the imported state, using the existing `--force`
  flag.** After caption import, `clulatent lock` refuses by default
  (Phase 1.7.5's re-lock guard). The operator must explicitly pass
  `clulatent lock --force` to produce a new lock manifest covering
  `caption_events.jsonl` and the archived caption source files. No new
  flag is introduced; the existing one is reused as-is, exactly as Phase
  1.9 specifies for post-review re-locking.
- Caption import never provides a reason to weaken or bypass locking.
  Locking remains tamper-evidence, not access control, and there is still
  no unlock path.

---

## Non-Goals

Explicitly **out of scope** for Phase 1.10:

- **No semantic interpretation.** Caption import stores what the file
  says, timestamped and attributed. It does not infer meaning, intent,
  or sentiment.
- **No translation engine.** `is_translation`/`translated_from_language`
  record a *declared* relationship (from the file itself, a filename
  convention, or operator input at import time) — Phase 1.10 does not
  translate anything or verify a translation's accuracy.
- **No OCR subtitle extraction.** Image-based subtitle formats (e.g.
  DVD/Blu-ray PGS, VobSub) are out of scope — only text-based formats
  (`srt`, `vtt`, text-based embedded streams) are designed for here. OCR
  would require a new dependency and is explicitly not added.
- **No diarization.** `speaker_hint` is a passive textual observation,
  never a claim equivalent to a Phase 1.8 `speaker_label`/`speaker_turn`
  event. No diarization model is invoked.
- **No language detection from cue content.** `language` is populated
  only from declared file/container metadata (see [Event Types §5,
  `caption_language_note`](#5-caption_language_note)) — never inferred
  from the text itself. Adding real language-ID would require a new
  dependency.
- **No UI.** No caption browser, editor, or sync-adjustment tool. This
  phase defines the on-disk representation only.
- **No cloud caption service.** Import is a local, file-only operation —
  no fetching captions from a network service, no auto-download.
- **No CLUBIN.** No compiled binary, no Rust runtime integration.
- **No automatic reconciliation with Whisper.** See [Interaction With
  Whisper](#interaction-with-whisper-speech_events) — both tracks persist
  independently; nothing auto-merges or auto-prefers.
- **No new ML dependencies.** Parsing `.srt`/`.vtt` text and demuxing an
  embedded stream via the existing `ffprobe`/`ffmpeg` subprocess wrapper
  require no model weights and no new Python ML packages.
- **No change to package locking behavior.** Phase 1.10 reuses Phase
  1.7.5's `lock`/`verify-lock`/`lock-status`/`--force` exactly as they
  exist today.

---

## Implementation Timeline

### Phase 1.10: Schema & Design ✓ (This Document)

- Define the caption event types, the `caption_events.jsonl` track, the
  `sources/captions/` archive layout, and the manifest `caption_sources`
  proposal.
- Specify validation rules (path safety, format enum, referential
  integrity, archive-hash integrity, immutability).
- Specify interaction with Phase 1.9 review and with Whisper
  `speech_events`.
- Add README roadmap note.
- Run test suite to confirm no behavior changed.
- Design-only commit; no runtime code.

### Phase 1.10.x (Future): Import Support

- `.srt`/`.vtt` parsers (pure-Python, no new dependency) producing
  `caption_segment`/`caption_gap`/`caption_overlap`/
  `caption_source_summary` records.
- Embedded-stream extraction via the existing hardened `ffprobe`/`ffmpeg`
  subprocess wrapper.
- Extend `validate.py` with the rules specified above.
- Teach `reindex` to index caption events (tolerant reader, same as other
  tracks).
- A `clulatent caption-import …` write command (append-only,
  operation-locked like `lock`/`reindex`/a future `review add`).
- Tests: each event type's round-trip, overlap/gap detection, archive
  hash verification, translation-field pairing, producer-prefix
  enforcement, dangling-reference rejection.

### Phase 2.0+ (Future): Cross-Track Resolution

- Extend the Phase 1.9 state-resolution algorithm (or a successor) to
  reason across `speech_events` and `caption_events` when both cover the
  same span.
- CLUBIN compiler consumes resolved multi-source speech evidence.

---

## Worked Examples

### 1. Importing a clean SRT

See [`caption_segment`](#1-caption_segment) and
[`caption_source_summary`](#2-caption_source_summary) above:
`sources/captions/001_original.srt` imported as 40 cues, `cap_000000`
through `cap_000039`, rolled up by `cap_000041`. `gap_count: 3` reflects
three natural pauses in dialogue; `overlap_count: 0` — a well-formed
single-speaker SRT.

### 2. Importing an auto-generated VTT

```json
{
  "id": "cap_000050",
  "type": "caption_segment",
  "t_start_ms": 2000,
  "t_end_ms": 5000,
  "producer": {"name": "caption-import:vtt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "text": "so today we're gonna talk about",
    "language": "en",
    "source_format": "vtt",
    "source_file": "sources/captions/003_auto.vtt",
    "caption_index": 3,
    "speaker_hint": null,
    "styling_removed": true,
    "is_translation": false,
    "translated_from_language": null,
    "provenance": {
      "import_method": "cli:caption-import",
      "imported_at": "2026-07-08T16:15:00Z",
      "original_filename": "auto.vtt"
    }
  }
}
```

`styling_removed: true` because the source VTT carried `<c>`-tagged word
timing (common in auto-generated captions); `confidence` stays `null`
because auto-generated VTT does not itself declare a per-cue confidence
value — the importer does not invent one. See [`caption_language_note`
example above](#5-caption_language_note) for this same file's missing
declared-language flag.

### 3. Importing translated subtitles

```json
{
  "id": "cap_000090",
  "type": "caption_segment",
  "t_start_ms": 1000,
  "t_end_ms": 4200,
  "producer": {"name": "caption-import:vtt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "text": "Bon retour dans l'émission.",
    "language": "fr",
    "source_format": "vtt",
    "source_file": "sources/captions/002_fr_translated.vtt",
    "caption_index": 0,
    "speaker_hint": null,
    "styling_removed": false,
    "is_translation": true,
    "translated_from_language": "en",
    "provenance": {
      "import_method": "cli:caption-import",
      "imported_at": "2026-07-08T16:12:00Z",
      "original_filename": "fr_translated.vtt"
    }
  }
}
```

`is_translation: true` paired with `translated_from_language: "en"`
(validation rule 6) — this cue is the translated counterpart of
`cap_000000` (Example 1) but is not linked to it by id; Phase 1.10 does
not introduce a cross-file "translation of" pointer field, since that
would require yet another referential-integrity surface for a relationship
that is usually already implied by `source_file` naming and file-level
metadata. A future phase could add one if needed.

### 4. Captions disagreeing with Whisper

```json
{
  "id": "cap_000000",
  "type": "caption_segment",
  "t_start_ms": 12000,
  "t_end_ms": 15000,
  "producer": {"name": "caption-import:srt", "version": "1.0"},
  "confidence": null,
  "payload": {
    "text": "the mitochondria is the powerhouse",
    "language": "en",
    "source_format": "srt",
    "source_file": "sources/captions/001_original.srt",
    "caption_index": 8,
    "speaker_hint": null,
    "styling_removed": false,
    "is_translation": false,
    "translated_from_language": null,
    "provenance": {
      "import_method": "cli:caption-import",
      "imported_at": "2026-07-08T16:10:00Z",
      "original_filename": "original.srt"
    }
  }
}
```

Compare against `ts_000008` (`speech_events.jsonl`, Phase 1.9 §4's
example): same time range, same original (uncorrected) text. Both records
persist, unmodified, in their own tracks. Neither import nor validation
flags this as an error — a human reviewer (Phase 1.9) can later reference
**both** `cap_000000` and `ts_000008` in a single review event's
`source_event_ids` if a judgment spanning both sources is needed; Phase
1.10 itself makes no such judgment.

### 5. Human correction of a caption segment

```json
{
  "id": "rv_000009",
  "type": "review_correction",
  "t_start_ms": 12000,
  "t_end_ms": 15000,
  "producer": {"name": "human:jk", "version": "1.0"},
  "confidence": 1.0,
  "payload": {
    "source_event_ids": ["cap_000000"],
    "supersedes_event_ids": [],
    "reviewer_id": "jk",
    "reviewer_label": null,
    "review_state": "corrected",
    "reason": "same transcription error as the Whisper output; singular form",
    "original_payload": {"payload.text": "the mitochondria is the powerhouse"},
    "corrected_payload": {"payload.text": "the mitochondrion is the powerhouse"},
    "reviewed_at": "2026-07-08T16:20:00Z"
  }
}
```

A standard Phase 1.9 `review_correction` — no new review machinery,
`source_event_ids` simply points at a `caption_events` id instead of a
`speech_events` id. `caption_events.jsonl` is never modified;
`sources/captions/001_original.srt` is never modified.

---

## Summary: What Phase 1.10 Delivers

1. **A caption track** (`tracks/caption_events.jsonl`) using the existing
   event envelope — no schema break — kept **separate** from
   `speech_events.jsonl` rather than merged into it.
2. **An archive layout** (`sources/captions/*` + `.sha256` sidecars, plus
   a proposed manifest `caption_sources` list) mirroring the existing
   `sources/source.mp4` embedding pattern.
3. **Five event types**: `caption_segment`, `caption_source_summary`,
   `caption_gap`, `caption_overlap`, `caption_language_note`.
4. **A closed `source_format` enum** (`srt`, `vtt`, `embedded`), with
   "sidecar vs. embedded" and "translation" expressed as orthogonal
   boolean/string fields rather than additional formats.
5. **Provenance via `producer.name = "caption-import:<source_format>"`**
   plus a payload `provenance` object — a zero-schema-change import
   attribution, following the pattern Phase 1.8/1.9 already established.
6. **Validation rules**: path safety, format/translation-field
   consistency, overlap/gap referential integrity, archive-hash
   integrity, label sanitization, producer-prefix enforcement, and
   cross-track immutability.
7. **An explicit non-merge policy with Whisper**: `caption_events` and
   `speech_events` coexist as independent evidence; nothing auto-prefers
   or auto-merges them; disagreement is preserved, not hidden.
8. **Zero new review machinery**: Phase 1.9's `review_events` already
   support referencing caption ids via `source_event_ids` — captions are
   reviewable exactly like any other track's evidence.
9. **Zero change to locking behavior**: reuses Phase 1.7.5's
   `lock`/`verify-lock`/`lock-status`/`--force` exactly as they exist,
   including the requirement to explicitly pass `--force` to re-lock
   after import.
10. **Design-only**: no UI, no ML, no CLUBIN, no runtime code, no
    behavior change.
