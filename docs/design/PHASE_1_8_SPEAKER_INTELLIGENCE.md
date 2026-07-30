# CLULatent Phase 1.8: Speaker Intelligence Layer

**Status**: Design-only. No implementation. No dependencies. Schemas and architectural decisions only.

**Motivation**: CLULatent Phase 1.7 produces `speech_segment` events from Whisper, which includes transcribed text but not speaker identity. Real-world multimedia analysis requires representing:
- Who spoke when
- Possible speaker changes
- Overlapping/concurrent speech
- Uncertain or ambiguous speaker labels
- Confidence scores for speaker assignments
- Traceable links back to source audio/speech events
- Non-fatal warnings when speaker analysis fails, skips, or is uncertain

This document defines the schemas, event types, and validation rules for representing speaker intelligence within CLULatent's existing event envelope system. No implementation is provided; this is a roadmap for future phases.

---

## Table of Contents

1. [Core Design Principles](#core-design-principles)
2. [Event Types & Track Files](#event-types--track-files)
3. [Event Schemas](#event-schemas)
4. [Uncertainty & Confidence](#uncertainty--confidence)
5. [Validation Rules](#validation-rules)
6. [Source Event Linking](#source-event-linking)
7. [Receipts & Non-Fatal Warnings](#receipts--non-fatal-warnings)
8. [Future Adapter Strategies](#future-adapter-strategies)
9. [Non-Goals](#non-goals)
10. [Implementation Timeline](#implementation-timeline)

---

## Core Design Principles

### 1. Untrusted Model Output

All speaker labels, identities, and speaker-change predictions come from external ML models (pyannote, WhisperX, SpeechBrain, etc.) or user annotation. These are treated as **untrusted producer output**, the same way faster-whisper's timestamps are normalized in Phase 1.7.1:
- Speaker labels are never claimed to be "real" identities unless supplied by a trusted user.
- Neutral labels (`speaker_0`, `speaker_1`, etc.) are preferred over inferred names.
- Confidence must always be present and truthful.
- Overlapping speech is represented explicitly, never hidden or arbitrarily assigned to one speaker.

### 2. Existing Event Envelope Only

Every speaker intelligence event uses the **canonical CLULatent event envelope** (`event.py`, `EventEnvelope`):
```python
{
  "id": "string (e.g., 'sp_000000')",
  "type": "string (speaker_label | speaker_turn | speaker_change | overlapping_speech | speaker_count_estimate)",
  "t_start_ms": int (>= 0),
  "t_end_ms": int (>= 0, >= t_start_ms),
  "producer": {"name": string, "version": string},
  "confidence": float | null (when present, in [0.0, 1.0]),
  "payload": {
    "speaker_id": string,      # neutral label or user-supplied identity
    "source_event_ids": [string],  # links to speech_segment/audio_event ids when applicable
    ... (type-specific fields)
  }
}
```

No new top-level envelope schema. No new fields outside `payload`. No semantic_events. Leverages existing duration bounds, path resolution, and validation infrastructure.

### 3. Traceability & Auditability

Every speaker event must include:
- A `source_event_ids` array linking it back to the speech/audio events it was derived from (where applicable).
- The producer name and version (e.g., `"pyannote"`, `"0.1.0"`).
- A confidence score (even if `null`, to signal uncertainty was explicitly considered).
- Optional `notes` field for non-standard explanations (e.g., "inferred from context", "user-supplied", "skipped due to timeout").

This ensures:
- Future readers can understand *which* model/user labeled *what*, *when*, and *how confident* it was.
- Traceability is maintained even if the same package is re-analyzed with a different speaker model.
- Non-fatal problems (timeouts, low-confidence runs, skipped segments) are auditable via receipts.

---

## Event Types & Track Files

### New Track: `speaker_events.jsonl`

A new canonical track file at `tracks/speaker_events.jsonl` (sorted by `t_start_ms`, like all CLULatent tracks) will contain all speaker-related events. The manifest will declare:

```json
{
  "name": "speaker_events",
  "file": "tracks/speaker_events.jsonl",
  "record_count": int,
  "sorted_by": "t_start_ms",
  "schema_id": "clulatent.track.event_envelope",
  "schema_version": "0.1.0"
}
```

### Event Types

| Type | Purpose | Sorted By |
|------|---------|-----------|
| `speaker_label` | Assigns a speaker identity to a continuous time span. May overlap with other speaker labels if simultaneously speaking. | `t_start_ms` |
| `speaker_turn` | Marks a time span when a single speaker is speaking (no overlaps). Used when speakers take distinct turns. | `t_start_ms` |
| `speaker_change` | Instantaneous event at a single point in time marking a detected speaker change. Usually instantaneous or very short. | `t_start_ms` |
| `overlapping_speech` | Marks a time span where 2+ speakers are actively speaking. Includes a list of participating speaker ids and confidence for each. | `t_start_ms` |
| `speaker_count_estimate` | Aggregate estimate of the total number of distinct speakers in the entire source, or in a large time window. | `t_start_ms` |

---

## Event Schemas

### 1. `speaker_label`

**Purpose**: Assign a speaker identity to a continuous time span. This is the primary event type for speaker diarization output.

**Example Payload**:
```json
{
  "id": "sp_000001",
  "type": "speaker_label",
  "t_start_ms": 1000,
  "t_end_ms": 5000,
  "producer": {"name": "pyannote.audio", "version": "3.0.1"},
  "confidence": 0.94,
  "payload": {
    "speaker_id": "speaker_0",
    "source_event_ids": ["ts_000000", "ts_000001"],
    "is_real_identity": false,
    "notes": "inferred from acoustic features; may contain overlaps with adjacent labels"
  }
}
```

**Required Payload Fields**:
- `speaker_id` (string): Neutral label (`speaker_0`, `speaker_1`, ...) or user-supplied identity string. **Never** infer real-world names or faces from speech characteristics; if user supplies a name, it must be explicitly marked as user-provided.
- `source_event_ids` (array of strings, optional): List of `speech_segment` or `audio_event` ids this label is based on. Empty if the speaker label is from an external input.
- `is_real_identity` (boolean): `false` unless the user explicitly confirmed this speaker is a known/named person and accepted the identity risk.
- `notes` (string, optional): Explanation for unusual cases (e.g., "model skipped this region", "insufficient audio", "likely overlapping").

**Semantics**:
- Multiple `speaker_label` events with the same `speaker_id` but disjoint time ranges represent the same speaker re-appearing at different times.
- Overlapping `speaker_label` events for *different* `speaker_id` values are allowed and indicate possible simultaneous speech (use `overlapping_speech` type for explicit marking).
- Duration bounds (Phase 1.7.1) apply: `t_start_ms` and `t_end_ms` must be within `source_duration_ms ± EVENT_DURATION_TOLERANCE_MS`.

---

### 2. `speaker_turn`

**Purpose**: Explicitly mark a time span where a single speaker is speaking (no overlaps, no ambiguity about who has the floor). Used when turn-taking is the primary concern (e.g., a lecture, interview, or dialogue with minimal crosstalk).

**Example Payload**:
```json
{
  "id": "sp_000002",
  "type": "speaker_turn",
  "t_start_ms": 5100,
  "t_end_ms": 12000,
  "producer": {"name": "whisperx", "version": "1.5.0"},
  "confidence": 0.88,
  "payload": {
    "speaker_id": "speaker_1",
    "source_event_ids": ["ts_000002", "ts_000003", "ts_000004"],
    "turn_index": 2,
    "notes": null
  }
}
```

**Required Payload Fields**:
- `speaker_id` (string): The speaker taking this turn.
- `source_event_ids` (array of strings): `speech_segment` ids contributing to this turn (in time order).
- `turn_index` (integer, optional): 0-based index of this turn within the source. Useful for turn-sequence analysis.
- `notes` (string, optional): Explanation if the turn boundary is uncertain or reconstructed.

**Semantics**:
- `speaker_turn` events are non-overlapping by definition (same speaker id, ordered by `t_start_ms`).
- If there are overlaps, they should be marked explicitly with `overlapping_speech` type instead.
- Validation must enforce that no two `speaker_turn` events overlap, regardless of speaker.

---

### 3. `speaker_change`

**Purpose**: Instantaneous or very short event marking a detected speaker change (transition point from one speaker to another).

**Example Payload**:
```json
{
  "id": "sp_000005",
  "type": "speaker_change",
  "t_start_ms": 12000,
  "t_end_ms": 12000,
  "producer": {"name": "pyannote.audio", "version": "3.0.1"},
  "confidence": 0.91,
  "payload": {
    "speaker_from": "speaker_0",
    "speaker_to": "speaker_1",
    "source_event_ids": []
  }
}
```

**Required Payload Fields**:
- `speaker_from` (string): Speaker id before the change.
- `speaker_to` (string): Speaker id after the change.
- `source_event_ids` (array of strings, optional): Ids of events used to detect this change (may be empty if change is inferred).

**Semantics**:
- `t_start_ms == t_end_ms` (instantaneous).
- If the model has uncertainty about *where* the change occurs, use a small non-zero duration (e.g., `[11990, 12010]`) with lower confidence.
- Validation must enforce `t_start_ms == t_end_ms` for this type, or allow a configurable tolerance for "gradual" changes.

---

### 4. `overlapping_speech`

**Purpose**: Explicitly mark a time span where 2+ speakers are actively speaking simultaneously. Used to represent and preserve ambiguity rather than arbitrarily assigning speech to one speaker.

**Example Payload**:
```json
{
  "id": "sp_000006",
  "type": "overlapping_speech",
  "t_start_ms": 8000,
  "t_end_ms": 9500,
  "producer": {"name": "pyannote.audio", "version": "3.0.1"},
  "confidence": null,
  "payload": {
    "speakers": [
      {"speaker_id": "speaker_0", "confidence": 0.75},
      {"speaker_id": "speaker_1", "confidence": 0.72}
    ],
    "source_event_ids": ["ts_000005", "ts_000006"],
    "total_speakers": 2
  }
}
```

**Required Payload Fields**:
- `speakers` (array of objects):
  - `speaker_id` (string): Speaker participating in the overlap.
  - `confidence` (float, optional): Confidence that this speaker is active during this window.
- `source_event_ids` (array of strings): Speech/audio events that were determined to overlap.
- `total_speakers` (integer): Count of speakers in this overlapping segment.
- `notes` (string, optional): Explanation (e.g., "low SNR, speaker identities uncertain").

**Semantics**:
- The top-level `confidence` may be `null` if the overall overlap is uncertain.
- Use this type instead of multiple overlapping `speaker_label` events when you want to explicitly signal overlap detection (rather than letting overlaps be inferred).
- Validation must ensure all `source_event_ids` actually overlap in time.

---

### 5. `speaker_count_estimate`

**Purpose**: Aggregate estimate of how many distinct speakers appear in the source (or in a large time window). Not a time-localized event, but a summary statistic.

**Example Payload**:
```json
{
  "id": "sp_000007",
  "type": "speaker_count_estimate",
  "t_start_ms": 0,
  "t_end_ms": 120000,
  "producer": {"name": "pyannote.audio", "version": "3.0.1"},
  "confidence": 0.65,
  "payload": {
    "estimated_count": 4,
    "estimated_range": [3, 5],
    "method": "clustering-based",
    "source_event_ids": [],
    "notes": "low confidence due to variable audio quality and background noise"
  }
}
```

**Required Payload Fields**:
- `estimated_count` (integer): Best-estimate number of distinct speakers.
- `estimated_range` (array of 2 integers, optional): `[min, max]` range if count is uncertain.
- `method` (string, optional): How the count was estimated (e.g., `"clustering"`, `"model-ensemble"`, `"user-provided"`).
- `source_event_ids` (array of strings): Empty unless this estimate is based on specific events.
- `notes` (string, optional): Explanation for uncertainty or special cases.

**Semantics**:
- `t_start_ms` and `t_end_ms` typically span the entire source or a large window.
- Can appear multiple times if different regions have different estimated counts (e.g., a 2-speaker intro, 4-speaker discussion).
- Confidence reflects uncertainty in the count.

---

## Uncertainty & Confidence

### Confidence Scoring

Every speaker event must consider confidence:

- **`confidence` field**: Always present in the top-level event (may be `null`, but the field exists).
  - Range: `[0.0, 1.0]` when numeric.
  - `null`: Confidence was not computed or is not meaningful for this event type (rare; prefer `0.5` for true uncertainty).
  - `1.0`: Very high confidence.
  - `0.0`: No confidence; event is speculative.

- **Payload-level confidence**: Individual items (e.g., speakers in an `overlapping_speech` event) may have their own confidence scores.

### Uncertainty Representation

1. **Low speaker label confidence**: Use lower `confidence` value. Include `notes` field explaining why (e.g., "short segment", "noisy audio", "acoustic ambiguity").

2. **Ambiguous speaker identity**: Do **not** guess. Use `speaker_0`, `speaker_1`, etc. If the model cannot decide, mark `is_real_identity: false` and set confidence lower.

3. **Overlapping/concurrent speech**: Explicitly mark with `overlapping_speech` type. Do not arbitrarily assign all speech to one speaker.

4. **Missing or uncertain boundaries**: If speaker start/end times are uncertain, use the best estimate and document in `notes`. Phase 1.7.1 duration bounds will catch truly out-of-range events.

### No False Precision

- Do not invent speaker identities.
- Do not claim confidence higher than the model's own output.
- Do not hide overlaps or ambiguities.

---

## Validation Rules

### Runtime Validation (Phase 1.8 or later)

All speaker events must pass these checks during `clulatent validate`:

1. **Envelope compliance**: Must parse as valid `EventEnvelope` (Pydantic validation).
   - Checked at parse time; negative timestamps and `t_end_ms < t_start_ms` are already caught.

2. **Duration bounds** (Phase 1.7.1 rules):
   - `t_start_ms` and `t_end_ms` must be within `source_duration_ms ± EVENT_DURATION_TOLERANCE_MS`.
   - Applies to all speaker event types equally.

3. **Speaker-specific rules**:
   - `speaker_label`: May overlap with other speaker labels; no restriction.
   - `speaker_turn`: Must not overlap with any other `speaker_turn` (same or different speaker).
   - `speaker_change`: Must have `t_start_ms == t_end_ms` (instantaneous, unless a configurable tolerance is enabled).
   - `overlapping_speech`: All `source_event_ids` must refer to existing `speech_segment` or `audio_event` records; these events must overlap in time.
   - `speaker_count_estimate`: No strict time-overlap requirement; typically spans the whole source.

4. **Source event linking**:
   - If `source_event_ids` is present and non-empty, all referenced ids must exist in canonical tracks.
   - Cross-track references are allowed (e.g., speaker event pointing to `speech_segment` or `audio_event`).
   - Timestamps of source events should fall within the speaker event's time range (or within tolerance).

5. **Speaker label stability**:
   - Within a package, the same `speaker_id` should have consistent meaning.
   - Validation can warn (not fail) if a `speaker_id` appears in very disjoint time ranges or with conflicting metadata.
   - Real identity (`is_real_identity`) should not change for the same `speaker_id`.

6. **Confidence validity**:
   - If present and numeric, must be in `[0.0, 1.0]`.
   - Already enforced by `EventEnvelope` Pydantic validator.

### Permissive Parsing (Reindex & Query)

Like `reindex.py` for other track types, future `reindex` operations on speaker events must:
- Skip malformed individual records with a warning, not fail the whole package.
- Still enforce hard limits (line size, record count, resource usage).
- Preserve all valid records.

---

## Source Event Linking

### `source_event_ids` Field

Every speaker event that derives from or relates to other events must include a `source_event_ids` array:

```json
"source_event_ids": [
  "ts_000015",  # a speech_segment event
  "ts_000016",
  "ae_000003"   # an audio_event
]
```

**Semantics**:
- List of ids from `speech_events.jsonl` (speech_segment: `ts_*`) or `audio_events.jsonl` (audio_event: `ae_*`, `ns_*`, `sl_*`).
- Order is not significant (but should be time-sorted for clarity).
- Empty if the event is not based on other tracked events (e.g., user-supplied annotation).

**Traceability Benefits**:
- A reader can drill down: "Which audio segments led to this speaker label?"
- A diff tool can compare speaker analysis across re-ingests: "Did this segment's speaker change?"
- A UI can highlight corresponding regions in audio/transcript.

### Validation of Links

During validation (`Phase 1.8` or later):
- All ids in `source_event_ids` must resolve to existing events in the package.
- The speaker event's time range should overlap with or be contained in the source events' time range (with tolerance).
- Mismatched ids can cause validation failure or warning (depending on strictness).

---

## Receipts & Non-Fatal Warnings

### New Receipt Fields

Extend `ReceiptLog.add()` (already updated in Phase 1.7.1 for `warnings`):

```python
receipts.add(
    operation="analyze_speakers",  # e.g., "analyze_speakers" for a future speaker analysis step
    status="success" | "partial" | "failure",
    source_path=str,
    output_path=str,
    errors=[...],
    warnings=[...],  # Non-fatal issues: timeouts, skipped segments, low confidence
    tool_path=str,  # Path to the speaker model/tool
    timed_out=bool,  # Did the operation hit a timeout?
    stderr_tail=str,  # Bounded stderr from the tool
    limits_snapshot={...},  # Resource limits in force
)
```

### Example Warnings

- `"speaker_label[sp_000012] skipped: source segment ts_000045 is too short (50ms < 100ms minimum)"`
- `"speaker_label[sp_000013] low confidence (0.31): assigned to speaker_0 but may be speaker_1"`
- `"overlapping_speech[sp_000014] partial: 3 speakers detected, but only 2 high-confidence (>0.7)"`
- `"speaker_count_estimate[sp_000015] timeout: diarization model exceeded 30s limit, using best-effort estimate"`

### Partial Success

A package can be built successfully even if speaker analysis:
- Times out partway through (status: `"partial"`).
- Has low confidence (but still creates events with appropriate confidence scores).
- Skips certain segments or speakers (documented in warnings).

The end result is still a valid `.clulatent` package with receipts showing what succeeded and what didn't.

---

## Future Adapter Strategies

This section documents how future implementations might integrate external speaker models **without committing to any of them now**.

### pyannote.audio (Diarization & Segmentation)

**Model**: Transformer-based speaker diarization (DIART for streaming, standard for offline).
**Outputs**: Speaker labels (speaker_0, speaker_1, ...) with time ranges.

**Adapter Strategy**:
- Parse pyannote's segment output (speaker id, start, end, confidence).
- Map each segment to a `speaker_label` event.
- Link `source_event_ids` to the `speech_segment` events that contribute to this speaker label.
- If pyannote detects overlaps, create an `overlapping_speech` event with all participating speakers.
- Handle timeout/resource limits gracefully (Phase 1.7 subprocess security rules apply).

**Phase 1.8 Non-Goals**:
- No automatic model download from HuggingFace.
- No GPU selection or CUDA dependency in base package.
- No speaker embedding storage (future phases may add speaker vectors/metadata).

---

### WhisperX (Alignment & Speaker Labels)

**Model**: Whisper + force-alignment + optional diarization (requires pyannote separately).
**Outputs**: Speech segments with speaker ids (if diarization enabled) and aligned word-level timings.

**Adapter Strategy**:
- Use WhisperX's alignments to refine `speech_segment` timestamps from Phase 1.7 Whisper output.
- If WhisperX includes speaker labels, create `speaker_label` events alongside `speech_segment` events.
- Use WhisperX's confidence for both transcript and speaker assignments.
- Handle language-specific alignment models (load from local cache, not auto-download).

**Phase 1.8 Non-Goals**:
- No modification of existing `speech_segment` events (create new track or side-by-side metadata).
- No automatic language model download.

---

### Demucs (Source Separation, Optional Future)

**Model**: Meta's source separation model (vocals, drums, bass, other).
**Outputs**: Separated audio tracks for each source.

**Adapter Strategy** (Phase 2+, not 1.8):
- Not a "speaker intelligence" tool per se, but isolating vocals enables cleaner speaker diarization.
- Future `source_separation_summary` event type (not in Phase 1.8) could record which sources were extracted.
- Store vocal-only audio in package (not in Phase 1.8; would be a new media type).

**Phase 1.8 Non-Goals**:
- Do not attempt source separation.
- No new media types.
- No storage for separated audio streams.

---

### SpeechBrain (Multi-task, Optional Future)

**Model**: Hugging Face / Meta SpeechBrain (speaker verification, speech enhancement, etc.).
**Outputs**: Speaker embedding similarity, enhancement metadata.

**Adapter Strategy** (Phase 2+, not 1.8):
- Use speaker embeddings to cluster speakers (beyond pyannote's acoustic clustering).
- Store speaker embedding metadata in a future `speaker_metadata` payload field (not now).
- Link to `speaker_label` events for comparison/verification.

**Phase 1.8 Non-Goals**:
- Do not compute speaker embeddings.
- No speaker verification or matching.

---

### Generic Future Adapters

For any future speaker model or tool:

1. **Parse the model's output** into neutral speaker ids (`speaker_0`, `speaker_1`, ...).
2. **Create corresponding events** (speaker_label, speaker_turn, etc.) using the event envelope.
3. **Include confidence** and source_event_ids for traceability.
4. **Handle errors gracefully**: timeouts, resource limits, low-confidence runs should write warnings, not crash.
5. **Validate speaker labels** against duration bounds and cross-references before finalizing.
6. **Document** the adapter's assumptions in receipt notes and the package manifest.

No implementation details are bound in Phase 1.8; future phases are free to choose whichever models make sense.

---

## Non-Goals

Explicitly **out of scope** for Phase 1.8 and likely all of Phase 1:

- **Semantic events**: No speaker roles, roles (e.g., "host", "guest"), emotions, or intent inference. Only speaker identity and timing.
- **Face recognition**: CLULatent is audio-centric. Video processing and face recognition are out of scope.
- **Real-world identity recognition**: No automatic name matching, database lookups, or social media integration.
- **Cloud services**: No automatic uploads to speaker verification APIs or commercial diarization services.
- **Automatic model downloads**: No network access or Hugging Face integration in base package. Users provide local models or weights.
- **CLUBIN**: No CLUBIN speaker database or identity authority.
- **Speaker embedding storage**: No speaker vector/metadata in Phase 1.8 (future phases may add).
- **Source separation or voice enhancement**: These are pre-processing steps, not speaker intelligence.
- **Multi-language speaker identification**: Beyond Whisper's language detection; no language-specific speaker models.
- **Real-time streaming**: CLULatent is offline/batch. No streaming speaker diarization.

---

## Implementation Timeline

### Phase 1.8: Schema & Design ✓ (This Document)

- Define event types and schemas.
- Propose track structure and validation rules.
- Document adapter strategies for future ML tools.
- Add design notes to README/SECURITY.md.
- Run test suite to confirm no breakage.
- Create design-only commit, no runtime changes.

### Phase 1.9 (Future): Pyannote Integration

- Add optional `[speaker]` dependency (pyannote.audio, torch).
- Implement `analyze_speakers()`/`diarize_audio()` function (lazy-import pattern, Phase 1.7B/C style).
- Add `--diarize` flag to `clulatent ingest`.
- Create `speaker_events.jsonl` track in output packages.
- Update receipts to include speaker analysis metadata.
- Tests for speaker label creation, overlaps, confidence handling.

### Phase 2.0+ (Future): Multi-Model Support

- Add WhisperX, SpeechBrain, or other adapters (behind feature flags or separate subcommands).
- Support speaker embedding comparison and verification.
- Extend validation rules for cross-speaker consistency.
- Add speaker-aware search/query in `query` command.

---

## Summary: What Phase 1.8 Delivers

1. **Schemas for 5 speaker event types**: speaker_label, speaker_turn, speaker_change, overlapping_speech, speaker_count_estimate.
2. **Track structure**: speaker_events.jsonl with full event envelope integration.
3. **Validation rules**: Duration bounds, source linking, turn non-overlap, instantaneous changes.
4. **Receipts**: Non-fatal warnings for timeouts, low confidence, skipped segments.
5. **Future adapter roadmap**: Strategies for pyannote, WhisperX, Demucs, SpeechBrain.
6. **Design-only**: No code changes, no dependencies, no behavior changes.

Packages built with Phase 1.7 will still pass validation. Phase 1.9+ can add speaker analysis without breaking existing packages or requiring re-ingest.

---

## Appendix: Example Package Structure (Post-Phase 1.9)

```
sample.clulatent/
├── manifest.json                     # Updated with speaker_events track
├── sources/
│   └── source.mp4
├── media/
│   └── keyframes/
│       ├── 0000.jpg
│       ├── 1000.jpg
│       └── ...
├── tracks/
│   ├── keyframes.jsonl
│   ├── audio_events.jsonl            # silence, non_silent_audio
│   ├── speech_events.jsonl           # speech_segment (Whisper)
│   └── speaker_events.jsonl          # NEW: speaker_label, speaker_turn, overlapping_speech
├── index/
│   └── search.sqlite
├── lock/
│   ├── package.lock.json
│   └── package.lock.sha256
├── receipts/
│   └── ingest.jsonl                  # NEW: analyze_speakers operation(s)
└── README                            # User notes
```

---

## References

- CLULatent Phase 1 documentation
- CLULatent Phase 1.7.1 Duration bounds spec
- pyannote.audio GitHub: https://github.com/pyannote/pyannote-audio
- WhisperX GitHub: https://github.com/m-bain/whisperx
- SpeechBrain GitHub: https://github.com/speechbrain/speechbrain
- Demucs GitHub: https://github.com/facebookresearch/demucs
