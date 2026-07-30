# Phase 2.5: Analysis Lanes / Tracking Adapter Design

Status: **design/spec document only — no code changes**. Builds on the
frozen Phase 2.4 Portable Package Export Rules
(`docs/PHASE_2_4_PORTABLE_PACKAGE_EXPORT_RULES.md`, tag
`phase-2.4-portable-package-export-rules-freeze`), and on the shared
event envelope, track, receipt, review, and locking foundations already
implemented through Phase 2.2. This phase does not implement any new
lane, adapter, runtime detector, or model dependency — it defines the
**lane catalog**, **trust ladder**, **adapter boundary**, and **future
track shapes** so later phases can add analysis without violating the
core rules that already govern CLULatent packages.

> How do future visual, motion, object, OCR, audio, rhythm, music, and
> stereo analysis results enter a `.clulatent` package without becoming
> core dependencies, without being auto-trusted, and without breaking
> portability or human review?

## Scope of this phase

Implemented:

- This document, defining analysis lanes and tracking adapters as a
  design surface against the *existing* package format (shared
  `EventEnvelope`, `manifest.tracks` / `TrackDescriptor`, append-only
  receipts, review events, integrity locks, Phase 2.4 portability
  rules).
- A short Phase 2.5 roadmap note in `README.md`, cross-referencing this
  document.

Not implemented this phase (explicitly out of scope):

- No CLUBIN implementation.
- No Studio UI.
- No runtime trackers (object, face, motion, or otherwise).
- No OCR runtime.
- No new AI model dependency in core or optional extras.
- No cloud service.
- No identity recognition (face, speaker real-name, biometric, or
  person-matching).
- No semantic truth generation (no auto-claims about “what the video
  means,” roles, emotions, plot, or intent).
- No source-track mutation (keyframes, audio, speech, semantic, or any
  future lane track is never rewritten by an adapter after write).
- No unlock/relock workflow (Phase 2.3’s copy → review → validate →
  re-lock path remains the only safe locked-package edit process).
- No `clulatent analyze …` command, no adapter plugin registry, no new
  track files on disk, no manifest schema change.

## Core principles

These are binding for every lane defined below. They restating and
extend rules already true of speech/VAD (Phase 1.7), speaker
intelligence design (Phase 1.8), review (Phases 1.9 / 2.0–2.2), and
portability (Phase 2.4).

### 1. CLULatent core remains model-agnostic

The core package format and CLI must not require any specific detector,
tracker, OCR engine, ASR system, music model, or embedding model to
open, validate, lock, reindex, or review a package. Core understands:

- directory layout and `manifest.json`
- the shared `EventEnvelope`
- duration bounds, path containment, receipts, locks
- review append + read-time resolution

Core does **not** understand “OpenCV contours,” “Tesseract boxes,”
“Demucs stems,” or any vendor-specific intermediate format. Vendor
details live only inside optional adapters (future) and inside
`producer` / payload provenance fields once written.

### 2. External tools are adapters, not core dependencies

An **adapter** is a future, optional bridge that:

1. Invokes (or reads output from) an external tool.
2. Maps that tool’s output into one or more CLULatent event records
   using the shared envelope.
3. Writes those records into a declared lane track (or emits them for
   a future writer to append).
4. Records a receipt (success / partial / failure + warnings).

Adapters must never be imported by default core paths. Following the
Phase 1.7 lazy-ML pattern already used for VAD/transcription: optional
features stay opt-in; a missing adapter tool fails that operation with
a clear receipt, not a broken core install.

### 3. Detected does not mean trusted

A detector finding a scene cut, bounding box, OCR string, beat, or
speaker-adjacent audio peak is **evidence of detection**, not proof of
truth. Detection confidence (when present) is producer-reported and
untrusted the same way faster-whisper timestamps are untrusted until
clamped/validated (Phase 1.7.1). Downstream tools and humans must treat
lane events as **proposals** until review and validation say otherwise.

### 4. Generated does not mean canonical

Generating events (running an adapter, writing JSONL lines into a
working directory, or staging partial output) does **not** make them
package-canonical. Canonical status is a package-lifecycle property,
not a property of “the model produced it.” Staged / temporary /
adapter-cache output must never be treated as part of the portable
package until it has been validated and declared in `manifest.tracks`.

### 5. Canonical means validated, bounded, receipted, reviewable, and lockable

A lane track (or set of lane events) is **canonical package data** only
when all of the following hold:

| Requirement | Meaning |
|---|---|
| **Validated** | Records parse as `EventEnvelope`; track-local type/payload rules pass; package-level `validate` rules pass (presence, hashes, path containment, duration bounds). |
| **Bounded** | Every `t_start_ms` / `t_end_ms` is within source `duration_ms ± EVENT_DURATION_TOLERANCE_MS`; payload paths (if any) are relative POSIX and package-contained. |
| **Receipted** | The operation that produced or imported the track left an append-only receipt (success / partial / failure + warnings). Missing receipt does not silently upgrade trust. |
| **Reviewable** | Events have stable `id`s that Phase 2.0–2.2 review can reference (`review approve|reject|correct|override|status|note`). Adapters must not invent anonymous, unreferenceable rows. |
| **Lockable** | Once present as a manifest-declared `tracks/*.jsonl`, the file is included in the integrity lock’s generic track hash set (same as `review_events.jsonl` after Phase 2.0) and can be frozen under `clulatent lock`. |

Until those conditions are met, output is **generated / provisional**,
not canonical.

### 6. All lanes obey Phase 2.4 portability rules

Every future lane track is just another `tracks/*.jsonl` entry. Phase
2.4 already states that analysis lanes become portable automatically
when declared in `manifest.tracks`. Concretely, every lane must:

- Use **relative POSIX paths only** for any payload path
  (`media/...`, never absolute, never `..`, never symlink escapes).
- Remain valid after `cp -r` / archive / re-extract (content hashes,
  not mtimes).
- Never require adapter binaries, GPUs, or cloud services to *read*
  or *validate* the package after the fact.
- Never store process-scoped junk (`package.operation.lock.json`,
  adapter temp dirs, model caches) inside the portable export.
- Treat `index/search.sqlite` as derived only — lane events reindex
  like any other track; the index is never the source of truth.

## Architecture

### Analysis lane

An **analysis lane** is a coherent family of event types that describe
one perceptual axis of the source (scenes, motion, objects, OCR, audio
energy, rhythm, etc.). A lane is specified by:

1. One or more **event `type` strings** (envelope `type` field).
2. A preferred **track file** (usually one `tracks/<lane>_events.jsonl`,
   sometimes shared when types are tightly coupled).
3. Payload field contracts (required / optional).
4. Cross-lane linking rules (what may appear in
   `cross_lane_link_events`).
5. Review and validation expectations.

Lanes are **orthogonal by default**: a package may have zero, one, or
many lanes present. Absence of a lane is not an error.

### Tracking adapter

A **tracking adapter** (or more generally **analysis adapter**) is the
future optional implementation that *fills* a lane. “Tracking” here
covers object/proposal tracking *and* the broader sense of external
analysis that “tracks” signals over time. Adapter responsibilities:

| Layer | Owns |
|---|---|
| Core | Envelope, paths, bounds, validate, review, lock, reindex, receipts shape |
| Adapter | Tool invocation, tool-specific config, mapping tool output → envelope, producer name/version, confidence honesty, partial-failure warnings |
| Human / review | Trust upgrades, corrections, rejections, notes (Phase 2.0–2.2) |

Adapters never become the authority for package validity. `clulatent
validate` remains the authority for structural trust; humans remain the
authority for semantic trust via review events.

### Trust ladder

```txt
raw tool output
    → adapter-mapped events (generated, provisional)
        → validated + bounded + receipted track write (package-resident)
            → reviewable (stable ids; may still be untrusted)
                → reviewed (approve / correct / override — still additive)
                    → lockable integrity snapshot (bytes frozen at time T)
```

Moving right on this ladder never mutates earlier source events. Review
is always additive (`tracks/review_events.jsonl`). Locking never
rewrites tracks.

## Shared conventions for every lane

All lane events use the existing shared envelope (`event.py`):

```json
{
  "id": "scn_000001",
  "type": "scene_boundary",
  "t_start_ms": 12000,
  "t_end_ms": 12000,
  "producer": { "name": "adapter:pyscenedetect", "version": "0.6.x" },
  "confidence": 0.91,
  "payload": { }
}
```

### Common rules

1. **Envelope only.** No new top-level container. Lane-specific fields
   live exclusively in `payload`.
2. **Stable ids.** Prefer lane-prefixed monotonic ids
   (`scn_…`, `mot_…`, `obj_…`, `ocr_…`, `aud_…`, `rhy_…`, `lnk_…`).
   Ids must be unique within the package’s track set for review
   targeting.
3. **Producer honesty.** `producer.name` should identify the adapter
   or human process (`adapter:opencv`, `adapter:tesseract`,
   `human:<id>`), not claim “CLULatent core.”
4. **Confidence.** Optional (`null` = not supplied). When present,
   must be in `[0.0, 1.0]`. Low confidence is allowed and preferred
   over inventing certainty.
5. **Duration bounds.** Same `EVENT_DURATION_TOLERANCE_MS` rule as
   speech segments.
6. **No absolute paths / no external model weights in the package.**
   Optional derived media (crop thumbs, OCR region images) must live
   under package-relative paths if stored at all; model weights never
   ship inside the package.
7. **Sorted tracks.** Lane tracks sort by `t_start_ms`, same as
   existing tracks.
8. **Empty is valid.** A declared lane track with zero records is
   valid (matches existing empty `speech_events` / `semantic_events`
   precedent).
9. **Forward compatibility.** Readers that do not understand a lane
   `type` must ignore unknown types without failing package open
   (schema freeze draft §12 policy), while still counting them as
   present for generic validate/lock/reindex.

### Recommended track files (future; not created this phase)

| Track file | Primary lane types |
|---|---|
| `tracks/scene_events.jsonl` | scene / cut / shot boundary events |
| `tracks/visual_change_events.jsonl` | non-cut visual change / flash / transition |
| `tracks/motion_events.jsonl` | motion magnitude / camera motion summaries |
| `tracks/object_proposal_events.jsonl` | detection proposals / boxes / masks refs |
| `tracks/object_tracking_events.jsonl` | multi-frame tracklets / trajectories |
| `tracks/ocr_events.jsonl` | text detections and optional line groups |
| `tracks/audio_energy_events.jsonl` | loudness / RMS / energy envelopes |
| `tracks/audio_transient_events.jsonl` | onsets / attacks / spikes |
| `tracks/audio_texture_events.jsonl` | texture / timbre / noise-class windows |
| `tracks/audio_signature_events.jsonl` | fingerprint / embedding-window signatures |
| `tracks/rhythm_events.jsonl` | beat / tempo / meter proposals |
| `tracks/music_events.jsonl` | music segment / non-speech music activity |
| `tracks/stereo_events.jsonl` | stereo balance / width / L-R events |
| `tracks/cross_lane_link_events.jsonl` | explicit links between events in different lanes |

Exact track names may be refined at implementation time, but **event
type families below are the stable design surface**. Implementations
must not collapse unrelated families into `semantic_events` as a junk
drawer — `semantic_events` remains reserved for a future *semantic*
layer, not a dumping ground for raw detector output.

## Lane catalog

Each subsection is design-only: field lists are contracts for future
implementation, not live schemas in code today.

---

### 1. `scene_events`

**Purpose:** Shot / scene boundary detection — “the cut points and
shot spans of the video,” not narrative “scene meaning.”

**Representative types:**

| `type` | Role |
|---|---|
| `scene_boundary` | Instant or short-span cut/fade/dissolve marker |
| `scene_span` | Contiguous shot/scene interval between boundaries |
| `scene_analysis_summary` | Package- or window-level detector summary |

**Payload sketch (`scene_boundary` / `scene_span`):**

```json
{
  "boundary_kind": "cut | fade | dissolve | wipe | unknown",
  "shot_index": 12,
  "method": "content_detector | threshold | histogram | adapter_specific",
  "source_event_ids": [],
  "notes": "optional free text; never identity claims"
}
```

**Rules:**

- `boundary_kind` is a **structural** label, not a story label.
- Do not invent “Act 2 begins” or plot semantics here.
- Optional future adapters: FFmpeg scene filter, PySceneDetect, OpenCV
  histogram differencing (see adapter list below).

---

### 2. `visual_change_events`

**Purpose:** Perceptual visual change that is **not necessarily a shot
boundary** (flash frames, lighting shifts, large composition changes,
UI overlay appear/disappear).

**Representative types:**

| `type` | Role |
|---|---|
| `visual_change` | Time-bounded change interval or peak |
| `visual_change_peak` | Instantaneous high-delta marker |

**Payload sketch:**

```json
{
  "change_metric": "frame_diff | histogram | optical_proxy | unknown",
  "magnitude": 0.0,
  "region": null,
  "linked_scene_event_ids": [],
  "notes": "optional"
}
```

**Rules:**

- High magnitude ≠ scene cut. Cross-link to `scene_events` only via
  explicit `cross_lane_link_events` or payload ids, never by silently
  rewriting scene tracks.
- `region`, if present, uses normalized coordinates or package-relative
  mask paths — never absolute filesystem paths.

---

### 3. `motion_events`

**Purpose:** Motion energy / camera motion / activity windows, not
named-object identity.

**Representative types:**

| `type` | Role |
|---|---|
| `motion_window` | Interval of elevated motion |
| `camera_motion` | pan / tilt / zoom / shake *proposals* |
| `motion_summary` | Aggregate motion stats for a window |

**Payload sketch:**

```json
{
  "motion_kind": "global | local | camera | unknown",
  "camera_motion_guess": "pan | tilt | zoom | shake | static | unknown",
  "magnitude": 0.0,
  "region": null,
  "notes": "optional"
}
```

**Rules:**

- `camera_motion_guess` is a **proposal**, not cinematic truth.
- No person identity, no “subject is running toward camera” narrative
  claims without a separate future semantic layer + review.

---

### 4. `object_proposal_events`

**Purpose:** Per-frame or short-span **detection proposals** (boxes,
optionally mask refs, class labels as *model strings*).

**Representative types:**

| `type` | Role |
|---|---|
| `object_proposal` | One detection hypothesis |
| `object_proposal_group` | Optional grouping of proposals at one time |

**Payload sketch:**

```json
{
  "label": "person | car | text_region | unknown | model_class_string",
  "bbox_norm": [0.1, 0.2, 0.4, 0.6],
  "mask_path": null,
  "track_candidate_id": null,
  "is_identity": false,
  "source_frame_event_ids": ["kf_000012"],
  "notes": "optional"
}
```

**Rules:**

- **`is_identity` defaults false and must stay false** for automatic
  detectors. Real-world person/brand identity is out of scope and
  forbidden as an automatic claim.
- Class labels are producer vocabulary (`person`, `dog`, COCO class
  names, etc.), not validated ontology truth.
- Prefer linking to keyframe events via `source_frame_event_ids` when
  derived from keyframes.

---

### 5. `object_tracking_events`

**Purpose:** Multi-frame **tracklets** that associate proposals over
time. Still not identity recognition.

**Representative types:**

| `type` | Role |
|---|---|
| `object_track` | One tracklet spanning time |
| `object_track_update` | Optional sparse sample along a track |
| `object_track_break` | Explicit loss-of-track marker |

**Payload sketch (`object_track`):**

```json
{
  "track_id": "trk_000003",
  "label": "unknown | model_class_string",
  "proposal_event_ids": ["obj_000010", "obj_000011"],
  "bbox_norm_start": [0.1, 0.2, 0.4, 0.6],
  "bbox_norm_end": [0.12, 0.22, 0.41, 0.61],
  "is_identity": false,
  "notes": "optional"
}
```

**Rules:**

- `track_id` is a **local package label** (`trk_N`), never a real-world
  identity database key.
- Track continuity is provisional; breaks must be representable
  (`object_track_break`) rather than silently glued.
- No face recognition, re-id gallery, or biometric matching.

---

### 6. `ocr_events`

**Purpose:** Detected on-screen text as evidence, not as caption truth
and not as a replacement for `caption_events` (Phase 1.10) or
`speech_events`.

**Representative types:**

| `type` | Role |
|---|---|
| `ocr_token` | Word/token detection |
| `ocr_line` | Line grouping |
| `ocr_block` | Block/region grouping |
| `ocr_frame_summary` | Per-frame or window OCR summary |

**Payload sketch (`ocr_line`):**

```json
{
  "text": "detected string",
  "language_guess": null,
  "bbox_norm": [0.05, 0.8, 0.95, 0.95],
  "source_frame_event_ids": ["kf_000020"],
  "is_caption_candidate": false,
  "notes": "optional"
}
```

**Rules:**

- OCR text is **untrusted string evidence**. It is not automatically a
  caption, subtitle, transcript, or semantic fact.
- Do not auto-merge OCR into `caption_events` or `speech_events`. Any
  future merge is a separate, reviewable operation.
- No PII harvesting mission: adapters should not specially target
  personal data; packages remain subject to existing security/console
  sanitization when rendered.

---

### 7. `audio_energy_events`

**Purpose:** Time-varying loudness / energy, complementing existing
silence detection in `audio_events` without replacing it.

**Representative types:**

| `type` | Role |
|---|---|
| `audio_energy_window` | Windowed energy / RMS / LUFS-like summary |
| `audio_energy_peak` | Local energy peak marker |

**Payload sketch:**

```json
{
  "metric": "rms | peak | lufs_approx | unknown",
  "value": 0.0,
  "channel": "mono | left | right | mid | side | unknown",
  "notes": "optional"
}
```

**Rules:**

- Coexists with Phase 1.7 silence events; do not delete or rewrite
  `audio_events.jsonl` silence records.
- Metric units must be documented in payload/`notes` or a future
  schema_id bump — never implied as calibrated broadcast loudness
  unless the adapter explicitly says so.

---

### 8. `audio_transient_events`

**Purpose:** Onsets, attacks, clicks, and short impulsive events.

**Representative types:**

| `type` | Role |
|---|---|
| `audio_transient` | Onset / attack / impulse marker |
| `audio_transient_cluster` | Dense onset cluster window |

**Payload sketch:**

```json
{
  "transient_kind": "onset | attack | click | unknown",
  "strength": 0.0,
  "channel": "mono | left | right | unknown",
  "notes": "optional"
}
```

**Rules:**

- Transients are signal features, not music-theory “notes” and not
  speech word boundaries (those stay in speech/caption lanes).

---

### 9. `audio_texture_events`

**Purpose:** Timbre / texture / broadband character of audio windows
(noise-like, harmonic-ish, speech-like *as a texture cue*, music-like
*as a texture cue*) without asserting semantic genre or content truth.

**Representative types:**

| `type` | Role |
|---|---|
| `audio_texture_window` | Window labeled with texture features |

**Payload sketch:**

```json
{
  "texture_tags": ["noisy", "harmonic", "speech_like"],
  "features": {},
  "notes": "optional; tags are producer vocabulary"
}
```

**Rules:**

- `speech_like` / `music_like` tags are **texture hypotheses**, not
  transcript or genre claims.
- Free-form `features` objects are adapter-private; core must not
  require understanding them to validate the envelope.

---

### 10. `audio_signature_events`

**Purpose:** Compact fingerprints or embedding-window signatures for
similarity / dedup / future search — **not** identity of people.

**Representative types:**

| `type` | Role |
|---|---|
| `audio_signature` | Fingerprint or embedding for a window |
| `audio_signature_match` | Optional proposed match to another window/event |

**Payload sketch:**

```json
{
  "signature_kind": "fingerprint | embedding | unknown",
  "algorithm": "adapter_specific_name",
  "signature_ref": "media/signatures/.... | inline_omitted",
  "dim": null,
  "match_event_ids": [],
  "notes": "optional"
}
```

**Rules:**

- Signatures are **content hashes of signal behavior**, not biometric
  speaker identity and not face identity.
- Large binary blobs should be package-relative media files + path
  refs, not multi-megabyte inline JSON, when implemented.
- No cloud lookup service is required or assumed to interpret a
  signature.

---

### 11. `rhythm_events`

**Purpose:** Beat, tempo, and meter **proposals**.

**Representative types:**

| `type` | Role |
|---|---|
| `beat` | Single beat instant |
| `tempo_estimate` | BPM / tempo window |
| `meter_estimate` | Meter proposal (e.g. 4/4 guess) |
| `rhythm_summary` | Aggregate rhythm stats |

**Payload sketch (`tempo_estimate`):**

```json
{
  "bpm": 120.0,
  "meter_guess": "4/4 | unknown",
  "method": "adapter_specific",
  "notes": "optional"
}
```

**Rules:**

- Tempo/meter are estimates; they remain untrusted until reviewed if a
  human needs them as truth.
- Do not assert song identity, chart metadata, or copyright claims.

---

### 12. `music_events`

**Purpose:** Music activity / music segments as **activity evidence**,
not genre taxonomy truth or track identification.

**Representative types:**

| `type` | Role |
|---|---|
| `music_segment` | Interval proposed as music-dominant |
| `music_activity` | Sparse music-activity marker |
| `music_absence` | Explicit non-music window (optional) |

**Payload sketch:**

```json
{
  "music_confidence": 0.0,
  "source_event_ids": [],
  "notes": "optional"
}
```

**Rules:**

- No automatic song title / artist identification.
- No claim that music_segment implies licensed-safe reuse.
- Separation stems (if ever produced by an adapter such as Demucs)
  would be *media products* + receipts, not automatic semantic truth;
  stem storage is not designed in this phase beyond “must be
  package-relative if stored.”

---

### 13. `stereo_events`

**Purpose:** Stereo field behavior — balance, width, left/right
energy asymmetry.

**Representative types:**

| `type` | Role |
|---|---|
| `stereo_balance` | L/R balance window |
| `stereo_width` | Width / correlation proxy |
| `stereo_event` | Generic stereo anomaly or change |

**Payload sketch:**

```json
{
  "balance": 0.0,
  "width": 0.0,
  "channel_layout": "stereo | mono | unknown",
  "notes": "optional"
}
```

**Rules:**

- Mono sources may omit this lane entirely.
- Values are adapter-defined scales unless a future schema freezes
  units; document in producer/notes.

---

### 14. `cross_lane_link_events`

**Purpose:** Explicit, reviewable links between events in different
lanes (or between a lane event and an existing core track event such
as a keyframe or speech segment). This is the **only** first-class
place for multi-lane association claims.

**Representative types:**

| `type` | Role |
|---|---|
| `cross_lane_link` | Directed or undirected association |
| `cross_lane_link_group` | N-ary group of related event ids |
| `cross_lane_link_retraction` | Explicit retraction pointer (optional; prefer review reject when possible) |

**Payload sketch (`cross_lane_link`):**

```json
{
  "link_kind": "supports | co_occurs | derived_from | conflicts | unknown",
  "from_event_ids": ["scn_000004"],
  "to_event_ids": ["mot_000010", "ocr_000003"],
  "rationale": "optional short producer note",
  "is_identity_claim": false,
  "notes": "optional"
}
```

**Rules:**

- Links are **claims about association**, still untrusted until
  reviewed.
- `is_identity_claim` must be `false` for automatic adapters. Identity
  claims are out of scope for this phase’s entire design surface.
- Prefer linking by stable event ids over copying foreign payloads.
- Do not silently rewrite the linked events when creating a link.
- Human disagreement with a link uses Phase 2.0–2.2 review against the
  link event’s id (reject / correct / note), not destructive deletion.

**Why a separate lane?**

Without `cross_lane_link_events`, adapters tend to either (a) embed
ad-hoc foreign ids inconsistently inside every payload, or (b) invent
a “merged truth” track. Explicit links keep each lane pure and make
associations reviewable as first-class events.

## Adapter boundary (design)

### Adapter contract (future)

A future adapter implementation should expose, conceptually:

```txt
Adapter.name / Adapter.version
Adapter.lane_types() -> set[str]
Adapter.analyze(package_or_media_ref, config) -> AdapterResult

AdapterResult:
  events: list[EventEnvelope-compatible dicts]
  warnings: list[str]
  status: success | partial | failure
  receipt_fields: {...}
```

Mapping rules every adapter must obey:

1. **Emit envelope records only** — no private parallel schema as the
   package source of truth.
2. **Clamp or drop out-of-bounds times** before proposing write
   (same spirit as Phase 1.7.1 transcription clamping).
3. **Never mutate existing tracks** — only propose appends of new lane
   tracks or new records (implementation policy for writers is a later
   phase; this design forbids in-place mutation of prior events).
4. **Receipt everything** — timeouts, missing binaries, partial runs.
5. **No network by default** — no auto model download, no cloud API.
6. **No identity features** — no face re-id, no real-name speaker map,
   no biometric gallery.
7. **Portable outputs only** — relative paths, no host absolute paths
   in payloads, no operation-lock files, no adapter cache dirs left
   inside the package tree.

### Optional future adapters (not dependencies)

The following tools are named **only** as optional future adapter
candidates. None is required. None is wired into core. Presence on this
list is not an implementation commitment:

| Tool | Possible future lane roles (illustrative) |
|---|---|
| **FFmpeg** | scene proxies, audio extract, loudness filters, frame export helpers |
| **PySceneDetect** | `scene_events` |
| **OpenCV** | visual change, motion, simple proposals |
| **Tesseract OCR** | `ocr_events` |
| **WhisperX** | refined speech timing / optional diarization *bridge* (speaker lane remains Phase 1.8 design; not auto-semantic truth) |
| **pyannote.audio** | speaker-adjacent analysis (Phase 1.8); not identity |
| **MediaPipe** | pose/hand/object *proposals* only — not identity |
| **Demucs** | optional separation media + music/speech isolation helpers |
| **librosa** | energy, onset, rhythm features |
| **Essentia** | rhythm / music descriptors |
| **aubio** | onset / tempo |
| **YAMNet** | audio event *class proposals* (still untrusted labels) |
| **PANNs** | audio tagging proposals |
| **OpenL3** | audio/video embedding signatures |

Core install must remain usable without any of the above beyond what
Phase 1 already requires for basic ingest (FFmpeg for keyframes /
silence is an existing prototype dependency for `ingest`, not a
general analysis-lane mandate).

## Interaction with existing systems

### Review (Phases 2.0–2.2)

Every lane event with a stable id is a valid review target. Humans can
approve/reject/correct/override/note lane events without mutating
them. “Reviewed truth” remains a **read-time resolution** over
`review_events.jsonl`, never a rewritten detector track.

### Locking (Phase 1.7.5) and reviewed workflow (Phase 2.3)

- Writing lane tracks into a **locked** package must refuse the same
  way review writes refuse today (or operate only on an unlocked copy).
- Safe process for locked packages remains: copy → drop `lock/` → run
  future analysis/review → `validate` → `lock` as a new independent
  lock. No unlock/relock command is introduced here.

### Portability (Phase 2.4)

Lane tracks are ordinary canonical JSONL once declared. Export must:

- keep all declared lane tracks
- exclude `package.operation.lock.json` and adapter temp artifacts
- allow omitting `index/search.sqlite` (rebuild via `reindex`)
- keep integrity locks when present

A package must remain readable and validatable on a machine that does
not have the original adapter tools installed.

### Receipts

Future analysis operations should append receipts (name TBD, e.g.
`receipts/analyze.jsonl` or operation-specific files under
`receipts/`). Because `lock.py` already globs `receipts/*.jsonl`
generically, new receipt files are lock-covered without special-casing
— same observation Phase 2.3/2.4 made for hypothetical review/export
receipts.

Suggested receipt fields (align with existing ingest receipts):

```txt
operation, status, source_path, output_path, errors, warnings,
tool/producer, timed_out, limits_snapshot, lane_names, event_counts
```

## Validation expectations (future implementation)

When lanes are implemented, validation should extend — not replace —
current `validate_package` checks:

1. Envelope parse + time range + confidence range (already generic).
2. Duration bounds vs source (already generic).
3. Track file presence for every `manifest.tracks` entry.
4. Lane-local payload checks (bbox ranges, required fields, id
   uniqueness) as additive track validators.
5. Cross-lane link integrity: referenced ids should exist or be
   explicitly allowed as dangling-with-warning (implementation choice;
   hard-fail vs warn is deferred, but silent acceptance of typos
   without any signal is not acceptable).
6. Producer path rules: no absolute paths in payloads.

This phase does **not** add those validators to code.

## Non-goals

Restated explicitly, matching the Phase 2.5 implementation instruction:

- No CLUBIN implementation.
- No Studio UI.
- No runtime trackers.
- No OCR runtime.
- No new AI model dependency.
- No cloud service.
- No identity recognition.
- No semantic truth generation.
- No source-track mutation.
- No unlock/relock workflow.
- No automatic elevation of detections to “trusted” or “canonical”
  without validation + receipt (+ optional review/lock).
- No requirement that every package contain every lane.
- No merging of OCR into captions/speech by default.
- No face gallery, speaker real-name map, or biometric database.

## Relationship to prior phases

| Phase | Relationship |
|---|---|
| 1.7 speech/audio | Existing tracks remain; new audio lanes complement, do not rewrite silence/speech. |
| 1.8 speaker design | Speaker intelligence stays its own design lane; this doc does not subsume it. |
| 1.9 / 2.0–2.2 review | All new lane events are review targets; review remains additive. |
| 2.3 reviewed workflow | Locked packages still use copy-based safe edit; no new unlock path. |
| 2.4 portability | All lanes inherit portable path/hash/export rules unchanged. |
| 1.11 schema freeze | Lane tracks are future reserved families; forward-compat ignore-unknown-type policy still applies until a later freeze revises the draft. |

## What later phases still need to build

Not ordered as a commitment — only a backlog map:

1. Manifest/constants reservation for chosen track file names.
2. Per-lane payload validators and factories (mirroring `review.py`).
3. One thin adapter + CLI surface as a proof path (e.g. scene
   detection via an optional extra), with lazy imports.
4. Receipt operation names and analyze receipts.
5. Reindex indexing of new types (generic path may already suffice).
6. Optional derived media conventions (crops, masks, signature blobs).
7. Cross-lane link validation policy (strict vs warn).
8. Documentation updates to the schema freeze draft when first lane
   lands in code.

## Files changed

- `docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md` (new, this
  document).
- `README.md`: short Phase 2.5 roadmap note.

## Summary

Phase 2.5 freezes the **design language** for analysis lanes and
tracking adapters without shipping any of them. The core stays
model-agnostic; tools stay optional adapters; detection and generation
never auto-become trust or canonicity; canonicity requires validation,
bounds, receipts, reviewability, and lockability; and every lane must
travel under Phase 2.4’s portable package rules. Implementation of any
runtime lane remains a later, explicit phase.
