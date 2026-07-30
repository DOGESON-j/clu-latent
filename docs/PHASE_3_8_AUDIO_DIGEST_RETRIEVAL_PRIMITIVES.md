# Phase 3.8 — Audio Digest Retrieval Primitives

Status: **read-only retrieval helpers for already-validated audio
digest evidence only — still no audio adapter, model, or runtime
wiring**. Base: Phase 3.7 Audio Digest Package Validation Integration
is frozen (tag
`phase-3.7-audio-digest-package-validation-integration-freeze`).
Builds directly on `src/clu_latent/audio_digest.py` (Phase 3.4),
`src/clu_latent/audio_digest_writer.py` (Phase 3.5), the
`clulatent audio-digest ...` CLI group (Phase 3.6), and the
package-level validation wired into `validate.py` (Phase 3.7).

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Phase 3.4 validated that a digest record is shaped safely. Phase 3.5
made it possible to commit already-validated records into a package.
Phase 3.6 put a CLI front door on both. Phase 3.7 made package-level
validation aware of the audio digest track. Phase 3.8 is the first
place that evidence becomes *retrievable* on its own terms — by
record id, time range, record type, linked evidence id, or
salience/recommended-for-context flag — without a caller
re-implementing JSONL parsing or re-deriving Phase 3.4's validation
rules.

## What this phase implements

`src/clu_latent/audio_digest_retrieval.py`:

- `load_audio_digest_events(package_root)` — loads and validates every
  record in `tracks/audio_digest_events.jsonl`. Returns `[]` if the
  package has no audio digest track declared (absence is not an
  error). Raises `AudioDigestRetrievalError` for a missing package, an
  unreadable manifest, a manifest-declared track file that is
  missing/oversized/malformed, or a track whose records fail Phase 3.4
  validation.
- `get_audio_digest_event_by_id(package_root, event_id)` — the one
  record with that id, or `None`.
- `query_audio_digest_by_time_range(package_root, start_ms, end_ms)` —
  every record whose `[t_start_ms, t_end_ms]` overlaps the given
  range.
- `query_audio_digest_by_type(package_root, record_type)` — every
  record of that `type`.
- `query_audio_digest_by_linked_evidence_id(package_root, evidence_id)`
  — every record whose own id matches, or whose payload lists it in
  `linked_event_ids` / `linked_feature_series_ids` /
  `linked_digest_segment_ids`.
- `query_audio_digest_by_salience(package_root, *, min_salience=None,
  recommended_for_llm_context=None)` — `audio_digest_segment` records
  matching a minimum `payload.salience` and/or an exact
  `payload.recommended_for_llm_context` value.
- `select_audio_digest_llm_context(package_root, *, start_ms=None,
  end_ms=None, max_events=20)` — a small, bounded set of records safe
  to hand to an LLM by default: prefers `audio_llm_context_packet`
  records (Level 5) overlapping the range; only when none exist does
  it fall back to `audio_digest_segment` records (Level 3), sorted by
  salience descending. Never returns `audio_feature_series` (Level 1)
  records.
- `summarize_audio_digest_retrieval_result(events)` — a shallow,
  non-generative index of an already-retrieved list: record-type
  counts, ids, and the min/max time range covered. No prose, no
  inference.

## Records are only ever handed back already validated

Every retrieval function ultimately calls `load_audio_digest_events`,
which re-validates the whole track with
`audio_digest.validate_audio_digest_track` (the same function Phase
3.7 wires into `validate_package`) before returning anything. A caller
of this module can never be handed a record with an unsupported type,
a raw dense array, forbidden truth/intent/manipulation language, or an
unsafe `data_path` — even if such a record was hand-edited onto disk
outside the Phase 3.5 writer. If the on-disk track fails that check,
every retrieval function refuses cleanly rather than returning partial
or unsafe data.

## Safety behavior

- Read-only: nothing in this module writes a track file, a manifest,
  a receipt, or touches `index/search.sqlite`.
- `select_audio_digest_llm_context` never returns `audio_feature_series`
  records — dense per-frame data stays referenced by `data_path`, out
  of normal context, exactly as the Phase 3.3 contract requires.
- Every result is bounded: `select_audio_digest_llm_context` never
  returns more than `max_events` records; every query function returns
  only records already present (and already size/shape-bounded by
  Phase 3.4) in the track.
- An empty result is never an error — a package with no audio digest
  track, or a query that matches nothing, both return an empty list
  cleanly.

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated summaries. `summarize_audio_digest_retrieval_result`
  only counts and indexes fields already on disk — it never writes new
  text and never infers meaning.
- No claim that CLULatent understands audio.
- No CLI command exposing these helpers (a natural follow-on for a
  later phase, not this one).
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.
- No package mutation, no receipt, no index rebuild.

## Relation to Phase 3.4, Phase 3.5, Phase 3.6, and Phase 3.7

Phase 3.4 defined the non-raising `(errors, warnings)` record
validators. Phase 3.5 defined the lock-aware writer that calls those
validators before committing a batch. Phase 3.6 put a CLI front door
on both. Phase 3.7 made `validate_package` re-run that same validation
at the package level. Phase 3.8 reuses that exact validation
(`audio_digest.validate_audio_digest_track`) as the gate every
retrieval function passes records through before returning them — no
new validation rule is added anywhere in this phase.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_audio_digest_retrieval.py` suite, with no regression in
existing report/demo/adapter/validation/locking/review/analysis-lane/
audio-digest/audio-digest-writer/audio-digest-CLI/validate-audio-digest
tests.
