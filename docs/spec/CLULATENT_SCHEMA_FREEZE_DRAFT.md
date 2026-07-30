# CLULatent Package Schema — Freeze Draft (Phase 1.11)

**Status**: Draft specification. Design/spec only. This document does not
change runtime code, validation behavior, or locking behavior. It is the
first attempt to write down, in one place, what a *valid CLULatent package*
is — consolidating the format as it exists today (Phases 1.0–1.7.5) plus
the reserved shape of the design-only phases (1.8 speakers, 1.9 review,
1.10 captions).

**Schema version described**: `clulatent_version = "0.1.0"`,
`EventEnvelope schema_id = "clulatent.track.event_envelope"`,
`schema_version = "0.1.0"`.

**What "freeze draft" means here**: this is the candidate to stabilize
before Phase 2. It is explicitly labeled *draft* because two things still
need to happen before it can be called frozen: (1) the design-only tracks
(speaker/review/caption) must either ship or be formally deferred, and (2)
schema-backed validation must exist to *enforce* what this document merely
*describes* (see [Validation Tiers](#11-validation-tiers) and [Phase 2
Readiness](#15-phase-2-readiness)).

Where this document and the running code disagree, **the code is
authoritative** and this draft has a bug to fix — the whole point of the
freeze is to make the two match.

---

## Table of Contents

1. [Package Layout](#1-package-layout)
2. [Canonical vs Derived Files](#2-canonical-vs-derived-files)
3. [manifest.json](#3-manifestjson)
4. [The Shared EventEnvelope](#4-the-shared-eventenvelope)
5. [Shared Rules](#5-shared-rules)
6. [Implemented Track Families](#6-implemented-track-families)
7. [Reserved / Design-Only Track Families](#7-reserved--design-only-track-families)
8. [Receipts](#8-receipts)
9. [Locking](#9-locking)
10. [Resource Limits](#10-resource-limits)
11. [Validation Tiers](#11-validation-tiers)
12. [Schema Versioning Policy](#12-schema-versioning-policy)
13. [Non-Goals](#13-non-goals)
14. [Examples](#14-examples)
15. [Phase 2 Readiness](#15-phase-2-readiness)

---

## 1. Package Layout

A CLULatent package is a plain directory (conventionally suffixed
`.clulatent`) — human-inspectable by design, not an opaque archive. Phase 1
is **single-source**: exactly one source media file per package.

```txt
example.clulatent/
  manifest.json          # canonical — the package's root descriptor
  sources/
    source.mp4           # canonical — embedded byte-for-byte source media
    source.sha256        # canonical — sidecar digest of the source media
    captions/            # (reserved, Phase 1.10) archived caption sources + hashes
  media/
    keyframes/           # derived — extracted keyframe images (kf_*.jpg)
  tracks/
    keyframes.jsonl      # canonical — one EventEnvelope per line
    audio_events.jsonl   # canonical
    speech_events.jsonl  # canonical (present when --vad/--transcribe used)
    semantic_events.jsonl# reserved — not produced in Phase 1
  index/
    search.sqlite        # derived — rebuildable via `clulatent reindex`
  receipts/
    ingest.jsonl         # canonical — append-only operation audit log
  lock/
    package.lock.json    # integrity lock manifest (when locked)
    package.lock.sha256  # digest of package.lock.json's exact bytes
    package.operation.lock.json  # transient operation lock (write guard only)
```

Directory names are fixed constants (`sources/`, `media/`,
`media/keyframes/`, `tracks/`, `index/`, `receipts/`, `lock/`). Every path
written into `manifest.json` is **package-relative and POSIX-style** (see
[§5](#5-shared-rules)); nothing in a package is ever an absolute or
OS-specific path.

---

## 2. Canonical vs Derived Files

The single most important distinction in the format. **Canonical** files
are the source of truth; **derived** files can always be rebuilt from
canonical ones and must never be trusted as authority.

### Canonical (source of truth; covered by the integrity lock)

- `manifest.json` — the package descriptor.
- `sources/source.<ext>` — the embedded source media, stored byte-for-byte
  (`storage_mode = "embedded"`).
- `sources/source.sha256` — the digest sidecar for the source media.
- `tracks/*.jsonl` — every perception track (`keyframes`, `audio_events`,
  `speech_events`, and future tracks).
- `receipts/*.jsonl` — the append-only operation log.
- **(Reserved, Phase 1.10)** archived caption source files under
  `sources/captions/` and their `.sha256` sidecars, *if that design is
  adopted*.

### Derived (rebuildable; not authority; excluded from the lock by default)

- `index/search.sqlite` — a search index rebuilt from the canonical tracks
  by `clulatent reindex`. `manifest.index.canonical` is hard-coded `false`.
- `media/keyframes/*.jpg` — extracted keyframe images. Derived today; a
  future phase could "promote" specific frames to canonical, but as of this
  draft they are derived (and never hashed by the lock).
- Temporary operation lock files (`lock/package.operation.lock.json`,
  `<output>.oplock.json`) — transient write guards, not integrity artifacts.
- Any cache the tool may create.

**Rule**: a consumer that finds a derived file missing or stale should be
able to regenerate it from canonical data alone. A consumer must never
resolve a contradiction between canonical and derived data in favor of the
derived file.

---

## 3. manifest.json

`manifest.json` is the package's root descriptor and is itself canonical.
It is read as **untrusted, size-bounded** input (`max_manifest_bytes`,
default 1 MiB) and validated against a strict schema (`extra = "forbid"`
on every object — unknown keys are a hard error). Top-level shape as
implemented today:

| Field | Type | Notes |
|---|---|---|
| `clulatent_version` | `string` | Package schema/format version (`"0.1.0"`). Distinct from `tool.version`. |
| `package_id` | `string` | Stable id for this package. |
| `created_at` | `string` | ISO-8601 creation timestamp. |
| `status` | `"complete" \| "partial" \| "failed"` | Only `"complete"` passes validation. |
| `tool` | `{name, version}` | The producing tool (`clulatent` + version). |
| `source` | `SourceInfo` | The single embedded source (see below). |
| `timebase` | `{unit: "ms", type: "integer"}` | Fixed: all timestamps are integer milliseconds. |
| `tracks` | `[TrackDescriptor]` | One descriptor per present track file. |
| `media` | `{keyframes: {dir, method, interval_ms, count}}` | Derived-media bookkeeping. |
| `index` | `{file, status: "derived", canonical: false}` | The derived search index pointer. |
| `receipts` | `{file}` | Pointer to `receipts/ingest.jsonl`. |

`SourceInfo` (as implemented): `filename`, `stored_path`, `sha256`,
`duration_ms (≥0)`, `container_format`, `width (≥0)`, `height (≥0)`,
`fps (float|null)`, `video_codec (str|null)`, `audio_codec (str|null)`,
`bitrate (int|null)`, `has_audio (bool)`, `storage_mode = "embedded"`,
`phase_1_single_source = true`.

`TrackDescriptor`: `name`, `file`, `schema_id`, `schema_version`,
`record_count (≥0)`, `sorted_by`. Today every track sets
`schema_id = "clulatent.track.event_envelope"`,
`schema_version = "0.1.0"`, `sorted_by = "t_start_ms"`.

**Reserved (design-only) manifest additions** — described but not emitted
in Phase 1: a `caption_sources` list (Phase 1.10) parallel to `tracks`,
recording each archived caption file's `stored_path`, `sha256_path`,
`source_format`, `language`, and translation metadata. Adding it is a
non-breaking, additive manifest change (see [§12](#12-schema-versioning-policy)).

---

## 4. The Shared EventEnvelope

Every record in every `tracks/*.jsonl` file is exactly one line of JSON
matching a single, universal envelope. There is one envelope schema for
the whole format; new perception types add only a new `type` string and a
new `payload` shape — **never** a new top-level container.

```python
{
  "id": "string",              # unique within its track file
  "type": "string",            # discriminates payload shape (e.g. "keyframe")
  "t_start_ms": int,           # >= 0, integer milliseconds
  "t_end_ms": int,             # >= 0 and >= t_start_ms
  "producer": {                # who/what emitted this record
    "name": "string",
    "version": "string"
  },
  "confidence": float | null,  # in [0.0, 1.0] when present; null = "not stated"
  "payload": { ... }           # type-specific object (may be empty {})
}
```

Enforced today by the `EventEnvelope` Pydantic model (`event.py`), with
`extra = "forbid"` at both the envelope and `producer` level:

- `t_start_ms`, `t_end_ms` have `ge=0`.
- A model validator rejects `t_end_ms < t_start_ms`.
- A model validator rejects `confidence` outside `[0.0, 1.0]` (when
  non-null).
- Any unknown top-level or `producer` key is a hard error.

`schema_id`/`schema_version` are not stored per-record; they live once per
track in the manifest's `TrackDescriptor`, so a consumer validates records
against the envelope shape named there without the shape being hardcoded.

---

## 5. Shared Rules

Rules that apply across every track, regardless of `type`:

1. **Integer millisecond timestamps.** `timebase` is fixed at
   `{unit: "ms", type: "integer"}`. There are no floating-point or
   frame-number timelines anywhere in the canonical data.

2. **`t_end_ms >= t_start_ms`.** Enforced by the envelope. Instantaneous
   events are represented with `t_end_ms == t_start_ms` (e.g. a
   whole-package note anchored at `[0, 0]`). "Explicitly instantaneous"
   means the equality is intentional, not a bug; individual track families
   may *tighten* this (e.g. Phase 1.10's `caption_segment` requires
   `t_end_ms > t_start_ms` strictly — a zero-duration cue is meaningless).

3. **Duration-bound validation with tolerance.** Every event's
   `t_start_ms`/`t_end_ms` must fall within
   `source.duration_ms ± EVENT_DURATION_TOLERANCE_MS`
   (default **1000 ms**, Phase 1.7.1). The tolerance is deliberately
   nonzero — a container's probed duration and a decoder/model's internal
   notion of duration can legitimately differ by a few ms — but small, so
   a segment ending tens of seconds past the source is still caught.
   Ingest clamps/skips out-of-bounds model output before it becomes
   canonical; validate fails a package that still contains a truly
   out-of-bounds event.

4. **Confidence semantics.** `confidence` is optional (`null` = "not
   stated") and, when present, is a float in `[0.0, 1.0]`. Its *meaning*
   depends on the producer: for a model it is a probability-like score;
   for a human reviewer (Phase 1.9) it is self-reported certainty; for an
   imported caption (Phase 1.10) it is usually `null` (subtitle files
   rarely carry per-cue confidence). Confidence values from different
   producer kinds are **never** blended or compared as if commensurable.

5. **Producer naming.** `producer.name` identifies the emitter and follows
   reserved prefixes so a single filter can separate producer classes
   across every track:
   - Machine/tool producers use bare names: `ffmpeg`, `ffprobe`,
     `ffmpeg_silencedetect`, `silero-vad`, `faster-whisper`.
   - **(Reserved, Phase 1.9)** human review producers use
     `human:<reviewer_id>`.
   - **(Reserved, Phase 1.10)** caption import producers use
     `caption-import:<source_format>`.
   `producer.version` records the tool/model/protocol version responsible.

6. **`source_event_ids` / `supersedes_event_ids` semantics.** *(Reserved,
   introduced by the Phase 1.9/1.10 designs; not present in today's
   implemented tracks.)*
   - `source_event_ids`: ids of the event(s) a record refers to or judges.
     Referenced ids should resolve to existing events in *some* canonical
     track when the referent is expected to exist; a dangling reference is
     a validation error in the tiers that check it.
   - `supersedes_event_ids`: ids of prior records this one replaces. A
     record must not list its own id (no self-supersession) and the
     supersedes graph must be acyclic. "Superseded" is a *derived*,
     read-time state, never a stored assertion.
   These fields live inside `payload`, not on the envelope — they add no
   new top-level envelope key.

7. **Package-relative POSIX paths.** Every path stored in the package
   (`source.stored_path`, `tracks[].file`, `media.keyframes.dir`,
   `index.file`, `receipts.file`, and per-record `payload.path` values) is
   relative and POSIX-style. On read, each is resolved through
   `security.paths.resolve_in_package`, which rejects absolute paths, `..`
   traversal, and symlink escapes. In strict validation a containment or
   symlink violation is a hard failure.

8. **Bounded JSONL.** Track and receipt files are read line-by-line with
   hard caps: `max_jsonl_line_bytes` (default 1 MiB per line),
   `max_records_per_track` (default 1,000,000). Oversized lines/tracks
   fail cleanly instead of exhausting memory. An empty (zero-byte) track
   file is valid. `validate` is the *strict* reader (a malformed or
   non-envelope record is an error); `reindex` is the *tolerant* reader
   (it skips bad records to rebuild what it can).

9. **Safe text / console rendering.** Any package-derived string rendered
   to a terminal (transcript text, filenames, reviewer labels, notes,
   caption text) passes through the existing `safe_console_text`
   sanitization first, so control sequences / terminal-injection payloads
   embedded in untrusted content cannot reach the terminal raw. Free-text
   fields are additionally length-bounded and rejected if they contain NUL
   or raw control characters — the same lexical class of check applied to
   path strings.

---

## 6. Implemented Track Families

These tracks are produced by the current tool. Each row lists the
`type`(s), id prefix, `producer.name`, and payload shape as actually
emitted (`tracks.py`).

### `tracks/keyframes.jsonl` — implemented, always produced

| `type` | id prefix | producer | payload |
|---|---|---|---|
| `keyframe` | `kf_` | `ffmpeg` | `{path, frame_index, width, height}` |

`path` points at a derived image under `media/keyframes/`. Default
interval `KEYFRAME_INTERVAL_MS = 1000`, method `ffmpeg_interval`.

### `tracks/audio_events.jsonl` — implemented, produced when the source has audio

| `type` | id prefix | producer | payload |
|---|---|---|---|
| `audio_track_present` | `aud_000000` | `ffprobe` | `{message}` |
| `silence` | `sil_` | `ffmpeg_silencedetect` | `{}` |
| `non_silent_audio` | `nsil_` | `ffmpeg_silencedetect` | `{}` |

Phase 1.7A signal-level analysis — **non-ML, always computed** for any
source with an audio stream (no opt-in). Thresholds:
`SILENCE_NOISE_DB = -30.0`, `SILENCE_MIN_DURATION_S = 0.5`.

### `tracks/speech_events.jsonl` — implemented, opt-in only

| `type` | id prefix | producer | payload | flag |
|---|---|---|---|---|
| `speech_activity` | `va_` | `silero-vad` | `{}` | `--vad` (Phase 1.7B) |
| `speech_segment` | `ts_` | `faster-whisper` | `{text, language}` | `--transcribe` (Phase 1.7C) |

**ML, opt-in only.** Neither runs unless the user explicitly passes the
flag; the ML dependencies are not required for a default ingest. This file
is simply absent when neither flag is used.

### `tracks/semantic_events.jsonl` — reserved, NOT active

The constant `SEMANTIC_EVENTS_TRACK_FILE` exists, but **no semantic events
are produced in Phase 1**. The name is reserved so that a future semantic
extraction phase slots in without a schema break. A Phase 1 package does
not contain this file, and validation must not require it.

---

## 7. Reserved / Design-Only Track Families

These tracks are **specified in design docs but not implemented**. No
current tool writes them; validation does not yet enforce their rules.
They are listed here so the freeze reserves their names, id prefixes, and
producer conventions.

| Track | Phase / doc | Status | Producer convention | Key event types |
|---|---|---|---|---|
| `tracks/speaker_events.jsonl` | 1.8 — `PHASE_1_8_SPEAKER_INTELLIGENCE.md` | Design-only | model (e.g. `pyannote.audio`) | speaker labels, speaker turns, overlapping speech, speaker-count estimate |
| `tracks/review_events.jsonl` | 1.9 — `PHASE_1_9_HUMAN_REVIEW_CORRECTION.md` | Design-only | `human:<reviewer_id>` | `review_status`, `review_approval`, `review_rejection`, `review_correction`, `review_override`, `human_note`, `review_session_summary` |
| `tracks/caption_events.jsonl` | 1.10 — `PHASE_1_10_CAPTION_SUBTITLE_IMPORT.md` | Design-only | `caption-import:<source_format>` | `caption_segment`, `caption_source_summary`, `caption_gap`, `caption_overlap`, `caption_language_note` |

All three reuse the shared `EventEnvelope` unchanged. All three are
**additive**: a Phase 1 package without them is fully valid; a future
package with them remains valid to a Phase-1-era reader that ignores
unknown tracks (see [§12](#12-schema-versioning-policy)). Their detailed
payload schemas and validation rules live in their respective design docs
and are **not restated as normative here** — this draft only reserves
their existence and shape at a high level.

---

## 8. Receipts

`receipts/ingest.jsonl` is an **append-only audit log** of what operations
did to the package. Receipts are canonical (covered by the lock) but are
**not** track events — they do not use the `EventEnvelope`. One JSON object
per line, one record per operation.

Fields per record (as implemented in `ReceiptLog.add`):

- `id` (uuid4), `timestamp` (ISO-8601 UTC).
- `tool` `{name, version}` — **producer metadata** for the operation.
- `operation` (e.g. `"ingest"`), `status`
  (`"success" | "partial" | "failure"`) — the **operation outcome**.
- `source_path`, `output_path`, `source_hash`, `files_created[]`.
- `ffmpeg_command_success`, `ffprobe_command_success`.
- `errors[]` vs `warnings[]` — **the fatal/non-fatal split**: `errors`
  are problems that failed (or degraded) the operation; `warnings` are
  non-fatal problems the operation succeeded *despite* (e.g. Phase 1.7.1
  out-of-duration transcription segments that were clamped or skipped
  rather than aborting the whole ingest).
- Subprocess security trail: `tool_path` (the resolved binary that ran),
  `timed_out`, `stdout_truncated`, `stderr_truncated`, `stderr_tail`
  (bounded tail), `limits_snapshot` (the `Limits` in force).

**Trust posture**: model/tool output recorded in receipts (and in tracks)
is **untrusted until validated**. A receipt says *what happened*, not
*that the result is correct*. Locking proves bytes are unchanged; receipts
record provenance; neither asserts semantic correctness. Human review
(Phase 1.9) is the mechanism for asserting correctness, and even that is
just another recorded, challengeable event.

---

## 9. Locking

Two **independent** lock kinds (Phase 1.7.5). They must not be conflated.

### Integrity lock — durable tamper-evidence

- `lock/package.lock.json` — a `PackageLock` manifest listing each
  canonical file with its `sha256`, `size_bytes`, and `modified_time_ns`.
  Fields: `lock_version`, `package_id`, `created_at`, `tool {name,
  version}`, `policy {include_index, strict_extra_files, hash_algorithm:
  "sha256"}`, `files[] {path, sha256, size_bytes, modified_time_ns}`.
  Serialized deterministically (`sort_keys=True`) so its bytes are
  reproducible.
- `lock/package.lock.sha256` — the SHA-256 of `package.lock.json`'s exact
  bytes, so tampering with the lock manifest itself is detectable.
  `verify-lock` treats a lock-hash mismatch as its own distinct error.

**Files hashed**: `manifest.json`, the source media file,
`sources/source.sha256`, every `tracks/*.jsonl`, every `receipts/*.jsonl`.
`index/search.sqlite` is **excluded by default** (derived/rebuildable) and
only included with `--include-index`. `media/keyframes/*.jpg` is never
hashed in this phase.

**`verify-lock`** recomputes digests and reports `checked` / `changed` /
`missing` / `extra` / `errors` plus `lock_hash_valid`. `LockStatus` ∈
`{unlocked, locked, lock-invalid, lock-partial}`. In `--strict` mode,
untracked "extra" files (e.g. a `review_events.jsonl` added after lock
time) are reported — this is the intended signal that content was added
after the freeze, not a bug.

**Lock replacement requires explicit `--force`** (Phase 1.7.5 re-lock
guard). `clulatent lock` refuses by default if the package is already
locked; the operator must pass `--force` to replace an existing integrity
lock. There is deliberately **no `unlock` command** — a lock is evidence
of "what the package looked like at time T", and silently rewriting it
would undermine the point. To edit a locked package, work on a copy with
`lock/` removed and re-lock the copy.

`--chmod-readonly` is a best-effort convenience nudge against *accidental*
edits, explicitly **not** a security boundary.

### Operation lock — cooperative write guard, NOT integrity

- `lock/package.operation.lock.json` (or `<output>.oplock.json` during
  ingest, since the target doesn't exist yet). Records pid, hostname,
  operation, creation time, package path, tool version. Created around a
  single write operation (`ingest`, `lock`, `reindex`) and removed when it
  finishes. Its only job is to stop two writers racing on the same package.
- It is **not** created or checked by any read-only command, is **not** an
  integrity artifact, and proves nothing about file contents.
- A stale operation lock (dead pid) can be cleared with
  `--force-stale-lock` — a **separate flag** from the integrity lock's
  `--force`. The two must never be conflated.

### Signing is future work

The integrity lock is a **local hash manifest**: it detects *change*, not
*authorship*. It is not DRM, not encryption, not a cryptographic identity
signature. A future phase could sign `package.lock.json`'s hash (detached
GPG/minisign or a signing-authority scheme) *on top of* everything here
without changing this format.

---

## 10. Resource Limits

Every bound is centralized in `security/limits.py` (`Limits`), the single
source of truth. Defaults as of this draft:

| Limit | Default | Purpose |
|---|---|---|
| `max_source_file_bytes` | 2 GiB | Cap embedded source size. |
| `max_media_duration_ms` | 2 hours | Cap source duration. |
| `max_manifest_bytes` | 1 MiB | Bounded manifest read. |
| `max_jsonl_line_bytes` | 1 MiB | Bounded per-line JSONL read. |
| `max_records_per_track` | 1,000,000 | Cap records per track. |
| `max_sidecar_bytes` | 4 KiB | Cap `source.sha256` sidecar. |
| `ffprobe_timeout_s` | 30 s | Subprocess timeout. |
| `ffmpeg_timeout_s` | 900 s | Subprocess timeout. |
| `stderr_tail_bytes` | 64 KiB | Bounded stderr kept in receipts. |
| `max_stdout_capture_bytes` | 16 MiB | Live capture safety cap. |
| `max_stderr_capture_bytes` | 8 MiB | Live capture safety cap. |

A conformant reader treats these as *defaults*, configurable via a
`Limits` instance, and records the active snapshot in receipts
(`limits_snapshot`).

---

## 11. Validation Tiers

Validation is layered. This freeze names the tiers so future work has a
target; only Tier 1 exists today.

- **Tier 1 — Current validation (implemented).** `clulatent validate`,
  read-only: manifest parses and matches the Pydantic schema; `status ==
  "complete"`; all declared paths are relative POSIX and resolve safely
  inside the package; every track record parses as a valid `EventEnvelope`;
  duration bounds hold; JSONL size/count limits hold; the derived index /
  receipts cross-check against canonical data. It never mutates anything.

- **Tier 2 — Future schema validation.** Validate records against the
  `schema_id`/`schema_version` declared per track (per-`type` payload
  schemas), so a track claiming `speech_segment` records is checked to
  actually carry `{text, language}`, etc. Today the envelope is enforced
  but per-`type` payloads are not schema-checked.

- **Tier 3 — Future strict mode.** Escalate today's warnings to errors,
  enforce `strict_extra_files` (no untracked files alongside locked ones),
  require declared-but-optional metadata, and reject reserved-but-unknown
  types unless explicitly allowed.

- **Tier 4 — Future review-aware validation.** Enforce the Phase 1.9/1.10
  cross-track rules: referential integrity of `source_event_ids` /
  `supersedes_event_ids`, acyclic supersedes graph, `original_payload`
  drift detection, producer-prefix rules (`human:` only in review tracks,
  `caption-import:` only in caption tracks), archive-hash integrity for
  imported captions, and the additive-only immutability guarantee.

---

## 12. Schema Versioning Policy

- **Package schema version** (`clulatent_version`, currently `"0.1.0"`):
  the version of the overall package layout / manifest shape. Distinct
  from `tool.version` — a CLI patch does not necessarily change the
  package shape, and a package-shape change does not necessarily require a
  CLI version bump.
- **Track schema version** (`TrackDescriptor.schema_id` +
  `schema_version`, currently `clulatent.track.event_envelope` /
  `"0.1.0"`): the version of the envelope/payload contract for a given
  track. Versioned per-track so one track family can evolve without
  forcing a global bump.
- **Forward compatibility & unknown tracks**: a reader encountering a
  `tracks[]` entry whose `name`/`type` it does not recognize must **ignore
  it, not fail** (outside strict mode). New track families are additive by
  construction; a Phase-1-era reader stays valid against a package that
  adds `speaker_events` / `review_events` / `caption_events`.
- **Reserved event types**: `semantic_events` and the design-only
  speaker/review/caption types are reserved. A reader must not repurpose
  these names, and must not require them to be present.
- **Breaking vs non-breaking changes**:
  - *Non-breaking (minor `schema_version` bump)*: adding a new track
    family, adding a new `type`, adding an **optional** payload field,
    adding an optional manifest section (e.g. `caption_sources`).
  - *Breaking (major bump)*: removing/renaming a field or track, changing
    a field's type or units, tightening a previously-permitted value,
    changing timestamp units, or changing the envelope's required keys.
  Breaking changes require a documented migration (see
  [§15](#15-phase-2-readiness)).

---

## 13. Non-Goals

Explicitly **out of scope** for this schema freeze:

- **No CLUBIN binary format.** This freeze specifies the plain,
  human-inspectable directory format only. A compiled binary
  (`CLUBIN`) consumed by a future Rust runtime is a separate, later
  artifact and is not defined here.
- **No cloud service.** The format is local-file-only. No network fetch,
  upload, or remote validation is part of the spec.
- **No semantic-interpretation freeze.** `semantic_events` is reserved but
  its meaning/schema is deliberately **not** frozen here — perception
  first, semantics later.
- **No cryptographic identity / signatures yet.** Locking is
  tamper-evidence, not authorship proof; signing is explicitly future
  work ([§9](#9-locking)).
- **No UI / editor spec.** How a human *views* or *edits* a package
  (inspector, review UI) is not specified. This document is the on-disk
  contract only.
- **No face recognition / real-identity recognition.** Nothing in this
  format identifies real people. Speaker labels (Phase 1.8) are opaque,
  within-package cluster ids; reviewer ids (Phase 1.9) are opaque,
  self-declared handles. No biometric identity is defined or implied.

---

## 14. Examples

### 14.1 Minimal package tree

```txt
clip.clulatent/
  manifest.json
  sources/
    source.mp4
    source.sha256
  media/
    keyframes/
      kf_000000.jpg
      kf_000001.jpg
  tracks/
    keyframes.jsonl
    audio_events.jsonl
  index/
    search.sqlite
  receipts/
    ingest.jsonl
```

(No `speech_events.jsonl` because `--vad`/`--transcribe` were not used; no
`lock/` because the package was not locked.)

### 14.2 Keyframe event (`tracks/keyframes.jsonl`)

```json
{
  "id": "kf_000003",
  "type": "keyframe",
  "t_start_ms": 3000,
  "t_end_ms": 3000,
  "producer": {"name": "ffmpeg", "version": "6.1"},
  "confidence": null,
  "payload": {"path": "media/keyframes/kf_000003.jpg", "frame_index": 3, "width": 1280, "height": 720}
}
```

### 14.3 Speech event (`tracks/speech_events.jsonl`)

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

### 14.4 Receipt line (`receipts/ingest.jsonl`)

```json
{
  "id": "0f9c1e2a-...-e4",
  "timestamp": "2026-07-08T16:10:00+00:00",
  "tool": {"name": "clulatent", "version": "0.1.0"},
  "operation": "ingest",
  "status": "success",
  "source_path": "video.mp4",
  "output_path": "clip.clulatent",
  "source_hash": "sha256:...",
  "files_created": ["manifest.json", "tracks/keyframes.jsonl", "tracks/audio_events.jsonl"],
  "ffmpeg_command_success": true,
  "ffprobe_command_success": true,
  "errors": [],
  "warnings": ["speech_segment[4] clamped: t_end_ms exceeded source duration by 240ms"],
  "tool_path": "/usr/bin/ffmpeg",
  "timed_out": false,
  "stdout_truncated": false,
  "stderr_truncated": false,
  "stderr_tail": null,
  "limits_snapshot": {"ffmpeg_timeout_s": 900.0, "max_jsonl_line_bytes": 1048576}
}
```

### 14.5 Lock summary (`lock/package.lock.json`, abbreviated)

```json
{
  "lock_version": "0.1.0",
  "package_id": "clip-2026-07-08-...",
  "created_at": "2026-07-08T16:12:00+00:00",
  "tool": {"name": "clulatent", "version": "0.1.0"},
  "policy": {"include_index": false, "strict_extra_files": false, "hash_algorithm": "sha256"},
  "files": [
    {"path": "manifest.json", "sha256": "...", "size_bytes": 2048, "modified_time_ns": 1751990000000000000},
    {"path": "sources/source.mp4", "sha256": "...", "size_bytes": 5242880, "modified_time_ns": 1751990000000000000},
    {"path": "sources/source.sha256", "sha256": "...", "size_bytes": 72, "modified_time_ns": 1751990000000000000},
    {"path": "tracks/keyframes.jsonl", "sha256": "...", "size_bytes": 4096, "modified_time_ns": 1751990000000000000},
    {"path": "receipts/ingest.jsonl", "sha256": "...", "size_bytes": 1024, "modified_time_ns": 1751990000000000000}
  ]
}
```

`lock/package.lock.sha256` holds the SHA-256 of the exact bytes above.

### 14.6 Future review event reference (RESERVED — Phase 1.9, not emitted today)

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
    "review_state": "corrected",
    "original_payload": {"payload.text": "the mitochondria is the powerhouse"},
    "corrected_payload": {"payload.text": "the mitochondrion is the powerhouse"},
    "reviewed_at": "2026-07-08T16:20:00Z"
  }
}
```

References `ts_000008` (Example 14.3) without mutating it. See
`PHASE_1_9_HUMAN_REVIEW_CORRECTION.md` for the normative schema.

### 14.7 Future caption event reference (RESERVED — Phase 1.10, not emitted today)

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
    "is_translation": false,
    "translated_from_language": null
  }
}
```

Independent evidence covering the same span as `ts_000008` — the two are
**not** auto-merged. See `PHASE_1_10_CAPTION_SUBTITLE_IMPORT.md` for the
normative schema.

---

## 15. Phase 2 Readiness

Before this draft can become a true frozen schema and Phase 2 can begin,
the following must exist. These are the gaps between "described" and
"enforced/shipped":

1. **Human review implementation.** Phase 1.9 is design-only. A real
   `review_events.jsonl` writer (`clulatent review add …`, append-only,
   operation-locked) must exist so reviewed truth is producible, not just
   specifiable.

2. **Correction / review validation.** Tier 4 validation
   ([§11](#11-validation-tiers)) must be implemented: referential
   integrity, acyclic supersedes graph, `original_payload` drift
   detection, producer-prefix enforcement, and additive-only immutability.
   A review layer that cannot be validated cannot be trusted.

3. **Schema-backed validation.** Tier 2 must exist: per-`type` payload
   schemas checked against the `schema_id`/`schema_version` declared in
   each `TrackDescriptor`, so the format enforces what this document
   describes rather than relying on producers to behave. This is what
   turns the draft into a *freeze*.

4. **Package migration story.** A defined path for
   `clulatent_version`/track `schema_version` upgrades: how a `0.1.0`
   package is read (or migrated) by a later reader, what a breaking bump
   requires, and a `clulatent migrate` (or equivalent) command. Versioning
   policy ([§12](#12-schema-versioning-policy)) is stated; the executable
   migration path is not yet built.

5. **(Optional) Minimal inspector / viewer.** A read-only way to see a
   package's tracks, timeline, and (once implemented) review state — even
   a terminal `timeline`/`inspect` enrichment — so humans can actually
   review and trust content before Phase 2 builds on it. Not strictly
   required to freeze the schema, but likely required to *use* the review
   layer meaningfully.

Until items 1–4 exist, this document remains a **draft**: an accurate
description of the format as-built plus the reserved shape of what is
coming, but not yet a schema the tooling can fully enforce.
