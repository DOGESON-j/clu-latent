# Phase 3.2 — Audio Evidence Lane Design

Status: **design/spec document only — no adapter, model, or runtime
code**. Base: Phase 3.1 Report Demo and Screenshot Kit is frozen (tag
`phase-3.1-report-demo-and-screenshot-kit-freeze`). Builds on the
Phase 2.5/2.6 analysis-lane foundations (`docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`,
`src/clu_latent/analysis_lanes.py`) and the shared `EventEnvelope`
(`id`, `type`, `t_start_ms`, `t_end_ms`, `producer`, `confidence`,
`payload`).

This phase does not implement any audio adapter, does not run stem
separation, does not add an ML dependency, and does not change
`ANALYSIS_LANE_NAMES` or any validation code path. It defines the
**next-generation audio evidence lane catalog** as a design surface,
the same way Phase 2.5 defined the original lane catalog before Phase
2.6 gave it schema code. Wiring these lanes into
`src/clu_latent/analysis_lanes.py` (and reconciling names with the
existing `audio_energy_events` / `audio_transient_events` /
`audio_texture_events` / `rhythm_events` / `music_events` /
`stereo_events` entries already in `ANALYSIS_LANE_NAMES`) is left to a
future implementation phase.

## Core principle (unchanged, restated for audio)

Adapters produce **evidence, not truth**. This is doubly important for
audio, where signal features are easy to compute and easy to
over-interpret:

- **Audio evidence is not truth.**
- **Audio evidence is not intent.**
- CLULatent must not claim that audio "manipulates," "controls,"
  "scares," or "means" something.
- CLULatent describes **measurable acoustic evidence** and **qualified
  perceptual-effect candidates** — never certainty about a listener's
  emotional response, a creator's intent, or a definitive real-world
  sound identity.

### Forbidden claims

A CLULatent audio lane, adapter, report, or doc must never assert:

- "this audio is trying to manipulate you"
- "this proves intent"
- "this makes the viewer afraid"
- "this is definitely a gunshot"
- "this is definitely a specific instrument"
- "CLULatent understands audio"
- "semantic audio truth"

### Required qualified language

Every acoustic observation that edges toward interpretation must be
phrased as a **candidate**, using hedged, evidence-linked language such
as:

- "tension-like buildup candidate"
- "urgency-like pacing candidate"
- "impact-like transient"
- "compression/limiting evidence"
- "high-frequency emphasis"
- "dynamic range reduction"
- "rhythmic density increase"
- "stem candidate"
- "instrument-like candidate"

## Why audio matters, and why transcript is not enough

A transcript captures *words*. It does not capture *how the audio
sounds*: loudness trajectory, transient impacts, rhythmic pacing,
spectral brightness, dynamic-range compression, stereo movement, or
recurring perceptual patterns like a build-and-release arc. Two clips
with an identical transcript can carry very different acoustic
evidence — a calm narration versus a compressed, bass-heavy, rapidly
gaining-loudness bed under the same words. For CLULatent's evidence
model to be useful for media review, it needs a first-class way to
record *that difference*, without asserting what it means to a
listener. See `docs/AUDIO_EVIDENCE_LANES.md` for the full rationale,
lane catalog, and future-adapter notes.

## Scope of this phase

Implemented:

- This document.
- `docs/AUDIO_EVIDENCE_LANES.md` — the living reference for the audio
  evidence lane catalog (rationale, all ten lanes, payload sketches,
  examples, future adapters).
- A Phase 3.2 roadmap bullet in `README.md`.
- `tests/test_phase_3_2_audio_evidence_lane_design.py` — stable,
  keyword-based checks that the docs exist and hold the required
  content (evidence-not-truth language, all ten lane names, compression
  evidence, perceptual-effect candidates with measurable evidence,
  forbidden-claim language, future-adapter framing).

Not implemented this phase (explicitly out of scope):

- No new or renamed entry in `ANALYSIS_LANE_NAMES`
  (`src/clu_latent/analysis_lanes.py`) — no validation-code change.
- No audio adapter (FFmpeg/librosa/Essentia/aubio/Basic
  Pitch/Demucs/YAMNet/PANNs/OpenL3 or any other).
- No stem separation run, no model download, no ML dependency added to
  `pyproject.toml`.
- No semantic truth generation.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## Required design lanes

Ten lanes make up the next-generation audio evidence catalog. Every
lane's events use the existing shared `EventEnvelope` — no new
envelope container schema is introduced.

### 1. `audio_energy_events`

Loudness, RMS, peak, silence/non-silence, and energy-change evidence.

### 2. `audio_transient_events`

Impact-like, click-like, hit-like, onset-like sharp sound events.

### 3. `audio_rhythm_events`

Tempo, beat candidates, rhythmic density, repeated pulses, and pacing
changes.

### 4. `audio_pitch_events`

Pitch contours, melody candidates, tonal movement, and pitch
stability/instability.

### 5. `audio_stem_events`

Stem-separation evidence such as vocals/drums/bass/other candidates.
Separated stems are **evidence with artifact and bleed risk**, never a
clean ground-truth decomposition — a stem-separation adapter's output
is itself untrusted until reviewed, same as any other analysis lane.

### 6. `audio_instrument_candidate_events`

Instrument-like candidates. Certainty is never claimed; a label like
"instrument-like candidate: piano-like" is a hypothesis, not an
identification.

### 7. `audio_texture_events`

Noise-like, crowd-like, hum-like, hiss-like, music-like, speech-like,
and room-tone-like textures.

### 8. `audio_compression_events`

Clipping, limiting, dynamic range reduction, true peak, spectral
cutoff, bitrate/codec hints, transient smearing, noise floor, and other
compression/processing artifact evidence.

### 9. `audio_spatial_events`

Stereo width, left/right dominance, panning movement, mono/stereo
collapse, and other spatial changes.

### 10. `audio_perceptual_effect_events`

Qualified perceptual-effect candidates such as tension-like buildup,
calm-like passage, urgency-like pacing, impact-emphasis candidate,
suspense-like quieting, and release/drop candidate. **Every
perceptual-effect event must include measurable evidence fields and,
where possible, link to the lower-level lane events that support it.**
No pure vibes: a perceptual-effect candidate without linked,
measurable evidence is not a valid record under this design.

See `docs/AUDIO_EVIDENCE_LANES.md` for the full payload sketch, rules,
and worked example for each lane.

## Envelope and field requirements (all ten lanes)

- Every event fits the existing `EventEnvelope`: `id`, `type`,
  `t_start_ms`, `t_end_ms`, `producer`, `confidence`, `payload`.
- All timestamps are **integer milliseconds**.
- `confidence`, when present, is bounded to `[0.0, 1.0]` (the same rule
  `analysis_lanes._check_confidence` already enforces for every other
  lane).
- `producer` always identifies the tool/adapter (`name`, `version`) —
  no anonymous or implicit producer.
- Payloads are bounded and safe: short, JSON-serializable, no
  unbounded blobs, no path traversal, no real-person identity fields
  (the existing `FORBIDDEN_IDENTITY_FIELDS` / `IDENTITY_FLAG_FIELDS`
  rules in `analysis_lanes.py` apply to audio lanes exactly as they do
  to every other lane).
- Labels are conservative, hedged, and use the qualified vocabulary
  above.
- No event may assert identity, emotion certainty, intent, or truth.
- Every `audio_perceptual_effect_events` record should populate
  `payload.linked_event_ids` pointing at the lower-level lane events
  (energy, rhythm, compression, pitch, ...) that the candidate is
  derived from, wherever such lower-level evidence exists.

## Example: perceptual-effect candidate

```json
{
  "id": "ape_000001",
  "type": "perceptual_effect_candidate",
  "t_start_ms": 12000,
  "t_end_ms": 18500,
  "producer": {"name": "audio-effect-adapter", "version": "0.1.0"},
  "confidence": 0.72,
  "payload": {
    "label": "tension-like buildup",
    "evidence": {
      "loudness_rising": true,
      "spectral_centroid_rising": true,
      "rhythmic_density_increased": true,
      "dynamic_range_reduced": true
    },
    "linked_event_ids": ["energy_000012", "rhythm_000006", "comp_000004"]
  }
}
```

This record makes no claim about listener emotion or creator intent —
only that four measurable acoustic signals co-occur in this window,
each traceable to a lower-level lane event, and that the co-occurrence
pattern matches a "tension-like buildup" candidate label an adapter
proposed with 0.72 confidence.

## Future adapters (illustrative, not a commitment)

Every tool below is named only as a **possible future adapter
candidate**. None is required, none is wired into core, and none is
implemented by this phase. See `docs/AUDIO_EVIDENCE_LANES.md` for the
per-tool notes on which lanes it could plausibly feed.

FFmpeg/ffprobe, librosa, Essentia, aubio, Basic Pitch, Demucs, and —
later, strictly as optional model evidence — YAMNet, PANNs, and
OpenL3.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_phase_3_2_audio_evidence_lane_design.py` suite, with no
regression in existing report/demo/adapter/validation/locking/review
tests.
