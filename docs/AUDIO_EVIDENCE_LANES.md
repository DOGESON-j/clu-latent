# Audio Evidence Lanes

This is the living reference for CLULatent's audio evidence lane
catalog, first designed in Phase 3.2
(`docs/PHASE_3_2_AUDIO_EVIDENCE_LANE_DESIGN.md`). It builds on the
trust model in `docs/TRUST_MODEL.md` and the original analysis-lane
design in `docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`.

This document is design/reference only. No adapter, model, or runtime
code is implemented here.

## Why audio matters

Media evidence review that looks only at frames and transcript text
misses most of what a viewer actually experiences. Loudness
trajectory, transient impacts, rhythmic pacing, pitch movement,
spectral brightness, dynamic-range compression, and stereo movement
are all part of how a piece of media reads — and none of it is visible
in a keyframe grid or a caption file.

## Why transcript is not enough

A transcript captures *words*, not *sound*. Two clips can share an
identical transcript while carrying very different acoustic evidence:
a calm, wide-dynamic-range narration versus a heavily compressed,
bass-emphasized, steadily-loudening bed under the same words. A
reviewer relying on transcript alone has no record of that difference.
CLULatent's audio evidence lanes exist to make that difference visible
and reviewable — as evidence, not as a claim about what it means.

## Why audio evidence must be qualified

**Audio evidence is not truth. Audio evidence is not intent.**
Acoustic signal processing is good at measuring things (loudness,
onsets, spectral centroid, dynamic range, stereo width). It is not
good at knowing what a human will feel, what a creator intended, or
what a specific sound definitively *is*. CLULatent's audio lanes
therefore separate two kinds of record:

1. **Measurable acoustic evidence** (lanes 1–9 below) — numbers and
   short, bounded feature summaries that a tool actually computed.
2. **Qualified perceptual-effect candidates** (lane 10) — hedged,
   evidence-linked hypotheses built *on top of* measurable evidence,
   never asserted on their own.

### Forbidden claims

None of the following may appear as an assertion in an audio lane
event, adapter, report, or doc:

- "this audio is trying to manipulate you"
- "this proves intent"
- "this makes the viewer afraid"
- "this is definitely a gunshot"
- "this is definitely a specific instrument"
- "CLULatent understands audio"
- "semantic audio truth"

### Required qualified language

Prefer hedged, evidence-linked phrasing:

- "tension-like buildup candidate"
- "urgency-like pacing candidate"
- "impact-like transient"
- "compression/limiting evidence"
- "high-frequency emphasis"
- "dynamic range reduction"
- "rhythmic density increase"
- "stem candidate"
- "instrument-like candidate"

## The lane catalog

Every lane below uses the existing shared `EventEnvelope` (`id`,
`type`, `t_start_ms`, `t_end_ms`, `producer`, `confidence`, `payload`)
— no new envelope container schema. `t_start_ms`/`t_end_ms` are always
integer milliseconds. `confidence`, when present, stays within
`[0.0, 1.0]`. `producer` always names a tool and version. Payloads are
short, bounded, JSON-serializable, and contain no real-person identity
field.

---

### 1. `audio_energy_events`

**Purpose:** Loudness, RMS, peak, silence/non-silence, and
energy-change evidence.

**Payload sketch:**

```json
{
  "metric": "rms | peak | lufs_approx | silence | unknown",
  "value": 0.0,
  "channel": "mono | left | right | mid | side | unknown",
  "notes": "optional"
}
```

**Rules:** metric units must be documented (payload or producer
notes); never implied as calibrated broadcast loudness unless the
adapter explicitly says so.

---

### 2. `audio_transient_events`

**Purpose:** Impact-like, click-like, hit-like, onset-like sharp sound
events.

**Payload sketch:**

```json
{
  "transient_kind": "impact_like | click_like | hit_like | onset_like | unknown",
  "strength": 0.0,
  "channel": "mono | left | right | unknown",
  "notes": "optional"
}
```

**Rules:** a transient is a signal feature ("impact-like transient"),
never a real-world sound identification ("this is a gunshot").

---

### 3. `audio_rhythm_events`

**Purpose:** Tempo, beat candidates, rhythmic density, repeated
pulses, and pacing changes.

**Payload sketch:**

```json
{
  "rhythm_kind": "beat_candidate | tempo_estimate | density_change | pulse | unknown",
  "bpm": null,
  "density": 0.0,
  "method": "adapter_specific",
  "notes": "optional"
}
```

**Rules:** tempo/beat values are estimates ("beat candidate"), not a
music-identification or copyright claim.

---

### 4. `audio_pitch_events`

**Purpose:** Pitch contours, melody candidates, tonal movement, and
pitch stability/instability.

**Payload sketch:**

```json
{
  "pitch_kind": "contour | melody_candidate | stability | instability | unknown",
  "hz_estimate": null,
  "confidence_note": "estimate only",
  "notes": "optional"
}
```

**Rules:** no musical-note or song identification; pitch estimates are
signal-level, not music-theory truth.

---

### 5. `audio_stem_events`

**Purpose:** Stem-separation evidence such as vocals/drums/bass/other
candidates.

**Payload sketch:**

```json
{
  "stem_label": "vocals_candidate | drums_candidate | bass_candidate | other_candidate",
  "artifact_risk": "low | medium | high | unknown",
  "bleed_risk": "low | medium | high | unknown",
  "stem_ref": "media/stems/... | inline_omitted",
  "notes": "optional"
}
```

**Rules:** a separated stem is **evidence with artifact and bleed
risk**, never a clean ground-truth decomposition. Stem media, if
stored, must be package-relative — no host absolute paths. Stem
separation output stays untrusted until reviewed, same as any other
analysis lane.

---

### 6. `audio_instrument_candidate_events`

**Purpose:** Instrument-like candidates.

**Payload sketch:**

```json
{
  "instrument_label": "piano_like | guitar_like | drum_like | synth_like | unknown",
  "source_event_ids": [],
  "notes": "optional; label is a producer vocabulary hypothesis"
}
```

**Rules:** never claim certainty. "instrument-like candidate:
piano-like" is a hypothesis, never an identification.

---

### 7. `audio_texture_events`

**Purpose:** Noise-like, crowd-like, hum-like, hiss-like, music-like,
speech-like, and room-tone-like textures.

**Payload sketch:**

```json
{
  "texture_tags": ["noise_like", "crowd_like", "hum_like", "hiss_like", "music_like", "speech_like", "room_tone_like"],
  "features": {},
  "notes": "optional; tags are producer vocabulary"
}
```

**Rules:** `speech_like`/`music_like` tags are texture hypotheses, not
transcript or genre claims. Free-form `features` objects are
adapter-private.

---

### 8. `audio_compression_events`

**Purpose:** Clipping, limiting, dynamic range reduction, true peak,
spectral cutoff, bitrate/codec hints, transient smearing, noise floor,
and other compression/processing artifact evidence.

**Payload sketch:**

```json
{
  "compression_kind": "clipping | limiting | dynamic_range_reduction | true_peak | spectral_cutoff | bitrate_hint | codec_hint | transient_smearing | noise_floor | unknown",
  "value": 0.0,
  "method": "adapter_specific",
  "notes": "optional"
}
```

**Rules:** this lane records measurable processing/artifact evidence
only — it never asserts why a clip was processed that way (no intent
claim).

---

### 9. `audio_spatial_events`

**Purpose:** Stereo width, left/right dominance, panning movement,
mono/stereo collapse, and other spatial changes.

**Payload sketch:**

```json
{
  "spatial_kind": "width | balance | panning_movement | mono_collapse | unknown",
  "value": 0.0,
  "channel_layout": "stereo | mono | unknown",
  "notes": "optional"
}
```

**Rules:** mono sources may omit this lane entirely; values are
adapter-defined scales unless a future schema freezes units.

---

### 10. `audio_perceptual_effect_events`

**Purpose:** Qualified perceptual-effect candidates: tension-like
buildup, calm-like passage, urgency-like pacing, impact-emphasis
candidate, suspense-like quieting, release/drop candidate.

**Payload sketch:**

```json
{
  "label": "tension-like buildup | calm-like passage | urgency-like pacing | impact-emphasis candidate | suspense-like quieting | release/drop candidate",
  "evidence": {},
  "linked_event_ids": []
}
```

**Rules:** every event in this lane must include measurable
`evidence` fields (booleans/numbers referencing what was actually
measured, e.g. `loudness_rising`, `spectral_centroid_rising`,
`rhythmic_density_increased`, `dynamic_range_reduced`) and, where
possible, `linked_event_ids` pointing at the lower-level lane events
(energy, rhythm, compression, pitch, ...) the candidate is derived
from. **No pure vibes** — a perceptual-effect candidate with no
measurable evidence is not a valid record under this design. This lane
never asserts listener emotion, creator intent, or manipulation.

**Example:**

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

---

## How open-source tools can plug in later, as adapters

Every tool named below is a **possible future adapter candidate**.
None is required, none is wired into CLULatent core, and this document
implements none of them. Adapters remain optional, non-core bridges —
core install and validation never require any of these:

| Tool | Possible future lane roles (illustrative) |
|---|---|
| **FFmpeg / ffprobe** | audio extraction, loudness filters, basic energy/silence evidence |
| **librosa** | energy, onset, rhythm, pitch features |
| **Essentia** | rhythm/music descriptors, spectral features |
| **aubio** | onset/tempo detection |
| **Basic Pitch** | pitch/melody candidate extraction |
| **Demucs** | stem-separation candidates (`audio_stem_events`) |
| **YAMNet** *(later, optional model evidence)* | audio event class proposals — still untrusted labels |
| **PANNs** *(later, optional model evidence)* | audio tagging proposals |
| **OpenL3** *(later, optional model evidence)* | audio/video embedding signatures |

These are future adapters, not core requirements. Anything they
produce enters a package the same way every other adapter result does:
as evidence, validated, bounded, receipted, reviewable, and lockable —
never auto-trusted.

## See also

- [`docs/TRUST_MODEL.md`](TRUST_MODEL.md) — the core evidence-not-truth
  principle this document restates for audio.
- [`docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`](PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md)
  — the original analysis-lane design and adapter boundary rules.
- [`docs/PHASE_3_2_AUDIO_EVIDENCE_LANE_DESIGN.md`](PHASE_3_2_AUDIO_EVIDENCE_LANE_DESIGN.md)
  — the phase that introduced this catalog.
