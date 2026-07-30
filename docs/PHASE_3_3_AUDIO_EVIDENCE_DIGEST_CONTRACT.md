# Phase 3.3 — Audio Evidence Digest Contract

Status: **design/spec document only — no adapter, model, or runtime
code**. Base: Phase 3.2 Audio Evidence Lane Design is frozen (tag
`phase-3.2-audio-evidence-lane-design-freeze`). Builds on the audio
lane catalog defined there
(`docs/AUDIO_EVIDENCE_LANES.md`), the shared `EventEnvelope`, and the
Phase 3.0 static report viewer's "bounded, safe, read-only" pattern
(`docs/PHASE_3_0_STATIC_PACKAGE_REPORT_VIEWER.md`).

This phase does not implement any digest generator, retrieval API,
CLI command, or model. It defines the **audio evidence digest
contract**: how CLULatent packages should someday expose audio
evidence at multiple resolutions so that an LLM (or any other bounded
consumer) can reason about a package's audio without ever needing — or
being handed — the raw signal.

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

A package may eventually hold enormous amounts of dense, low-level
audio measurement (per-frame energy curves, spectral series, and so
on). None of that belongs in front of an LLM by default. The digest
contract exists so that:

- Full-resolution evidence is always **stored** (Level 0/1), never
  discarded, never summarized-and-thrown-away.
- A consumer is **shown** a small, bounded, ranked digest by default
  (Level 4/5).
- Deeper detail is available **on request**, by time range or by
  evidence id, never pushed unprompted.

## Scope of this phase

Implemented:

- This document.
- `docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md` — the living reference for
  the multi-resolution pyramid, the three new digest-layer record
  shapes (`audio_feature_series`, `audio_digest_segment`,
  `audio_llm_context_packet`), salience/ranking rules, bounding rules,
  and retrieval contract.
- A Phase 3.3 roadmap bullet in `README.md`.
- `tests/test_phase_3_3_audio_evidence_digest_contract.py` — stable,
  keyword-based checks that the docs exist and hold the required
  content.

Not implemented this phase (explicitly out of scope):

- No digest generator, no summarizer, no LLM call, no embedding model.
- No new or changed entry in `ANALYSIS_LANE_NAMES`
  (`src/clu_latent/analysis_lanes.py`) — no validation-code change.
- No retrieval CLI command, no `clulatent audio-digest …` command.
- No audio adapter, no stem separation run, no ML dependency added to
  `pyproject.toml`.
- No semantic truth generation.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## The multi-resolution audio evidence pyramid

Six levels, from raw signal to LLM-ready digest. Every level above
Level 0 is derived evidence — never truth — and every level's records
still use the existing shared `EventEnvelope` where they are
event-shaped (Levels 2–5).

| Level | Name | What it holds | Consumed by |
|---|---|---|---|
| 0 | **Source audio** | The original media file (or its extracted audio track), package-relative, untouched. | Adapters only |
| 1 | **Dense metrics** | Regularly-sampled numeric arrays: loudness curve, spectral centroid curve, and similar — `audio_feature_series` records. | Adapters, deep-retrieval tools |
| 2 | **Events** | Discrete, timestamped lane events — the Phase 3.2 catalog (`audio_energy_events`, `audio_transient_events`, `audio_rhythm_events`, `audio_pitch_events`, `audio_stem_events`, `audio_instrument_candidate_events`, `audio_texture_events`, `audio_compression_events`, `audio_spatial_events`, `audio_perceptual_effect_events`). | Reviewers, digest builders |
| 3 | **Segments** | Merged, human/LLM-readable spans grouping related Level 2 events — `audio_digest_segment` records. | Digest builders, reports |
| 4 | **Summaries** | Short, qualified, evidence-linked natural-language-ish summaries of one or more segments. | Reports, LLM context packets |
| 5 | **LLM context packets** | Bounded, ranked, retrieval-pointer-bearing packets safe to hand directly to an LLM — `audio_llm_context_packet` records. | LLM agents |

Each level is built **from** the level below it, never invented
independently — a Level 5 packet must be traceable back through Level
4/3/2 to the Level 1/0 evidence that supports it (see
`linked_event_ids` / `linked_series_refs` in
`docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md`).

### Level 0 — Source audio

The original media file or its extracted audio track. Referenced by a
package-relative path only (mirroring every other CLULatent source
reference) — never re-encoded, never mutated by digest tooling.

### Level 1 — Dense metrics (`audio_feature_series`)

Regularly-sampled numeric arrays (e.g. a loudness curve sampled every
20ms across an entire track). Too large and too low-level for direct
LLM consumption. Stored as a package-relative dense-array file, with a
small, bounded `audio_feature_series` descriptor event pointing at it
by path — **never inlined as a raw array in an event payload**.

### Level 2 — Events

The Phase 3.2 audio lane events themselves: discrete, timestamped,
bounded, envelope-shaped evidence records. This level already exists
as a design (Phase 3.2); Phase 3.3 does not change it.

### Level 3 — Segments (`audio_digest_segment`)

A `audio_digest_segment` groups a time range's worth of related Level
2 events (and, where relevant, Level 1 series windows) into one
reviewable, summarizable unit — e.g. "a rising-tension window from
12.0s to 18.5s, evidenced by four linked lane events."

### Level 4 — Summaries

A short, qualified, evidence-linked description attached to one or
more segments. Summaries use only the allowed, hedged vocabulary (see
below) and always carry forward the segment's `linked_event_ids`.
Phase 3.3 does not define a new record shape for summaries alone —
a summary is a `payload.summary` field carried by a
`audio_digest_segment` or `audio_llm_context_packet`.

### Level 5 — LLM context packets (`audio_llm_context_packet`)

A bounded, ranked, retrieval-oriented packet: a small set of the most
salient segments/summaries for a package or time range, explicit
caveats, and pointers (event ids, series refs, time ranges) an agent
can use to retrieve more detail — never the detail itself.

## Allowed language

- "candidate effect"
- "linked evidence suggests"
- "compression/limiting evidence"
- "rhythmic density increase"
- "tension-like buildup candidate"

(This extends, and must stay consistent with, the Phase 3.2 vocabulary
in `docs/AUDIO_EVIDENCE_LANES.md`.)

## Forbidden claims

- "proves intent"
- "manipulation"
- "makes viewer afraid"
- "CLULatent understands audio"
- "semantic audio truth"
- "definitely exact instrument/source"

These extend the Phase 3.2 forbidden-claims list and apply to every
digest level, including LLM context packets — a bounded packet is not
an excuse to be less conservative than a raw event.

## Caveats and evidence-not-truth warnings

Every `audio_llm_context_packet` must carry an explicit, non-optional
caveat field restating that its contents are evidence, not truth, and
that low-salience or omitted evidence still exists in the package and
can be retrieved on request. See
`docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md` for the exact field shape.

## Retrieval

Two retrieval axes are defined by this contract, for a future
read-only tool to implement:

1. **By time range** — given `t_start_ms`/`t_end_ms`, return the
   Level 1–3 records overlapping that range.
2. **By evidence id** — given one or more event/segment ids, return
   those records (and, optionally, the series windows they reference).

Retrieval is always read-only and always bounded (a future
implementation would reuse the same bounded-reader pattern
`report.py` already uses for tracks/receipts). No retrieval mechanism
is implemented this phase.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_phase_3_3_audio_evidence_digest_contract.py` suite, with
no regression in existing report/demo/adapter/validation/locking/
review/audio-lane-design tests.
