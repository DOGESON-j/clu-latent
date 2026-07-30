# Phase 3.7 — Audio Digest Package Validation Integration

Status: **`clulatent validate` (`validate_package()`) now recognizes the
audio digest track and receipt log when present — still no audio
adapter, model, or runtime wiring**. Base: Phase 3.6 Audio Digest CLI
Commands is frozen (tag `phase-3.6-audio-digest-cli-commands-freeze`).
Builds directly on `src/clu_latent/audio_digest.py` (Phase 3.4),
`src/clu_latent/audio_digest_writer.py` (Phase 3.5), and
`src/clu_latent/audio_digest_cli` commands (Phase 3.6), mirroring the
existing Phase 2.9 analysis-lane validation integration in
`src/clu_latent/validate.py` almost exactly.

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Audio digest records are package evidence. They must be validated when
present. They must remain evidence, not truth. Dense data must stay
referenced, not dumped into normal LLM context. Phase 3.7 does not
change what a valid record looks like (Phase 3.4) or how one gets
written (Phase 3.5) — it makes `validate_package()` actually look at
`tracks/audio_digest_events.jsonl` and `receipts/audio_digest.jsonl`
when they exist, the same way it already looks at every other
manifest-declared track and receipt log.

## What this phase implements

In `src/clu_latent/validate.py`:

- A new per-track conditional block, alongside the existing
  `REVIEW_EVENTS_TRACK_NAME` and `SUPPORTED_ANALYSIS_LANES` blocks: if
  a manifest track is named `audio_digest_events`
  (`audio_digest_writer.AUDIO_DIGEST_TRACK_NAME`), its already-parsed
  raw records are passed to `audio_digest.validate_audio_digest_track`.
  This reuses the per-track loop's existing generic checks — schema
  identity, `record_count`, `sorted_by`, and the source-duration
  tolerance check (`EVENT_DURATION_TOLERANCE_MS`) — for free; it adds
  only the audio-digest-specific rules: correct dispatch by record
  type, per-type payload shape/bounds, safe `data_path` containment,
  forbidden dense-array payload keys, and duplicate id detection
  (already implemented by `validate_audio_digest_track` in Phase 3.4).
- A new `receipts/audio_digest.jsonl` block, structured exactly like
  the existing Phase 2.9 `receipts/analyze.jsonl` block: optional,
  bounded JSONL parse, then a shape check via the new
  `audio_digest.validate_audio_digest_receipt(s)` functions.

In `src/clu_latent/audio_digest.py`:

- `validate_audio_digest_receipt(record, *, limits=DEFAULT_LIMITS)` —
  validates one raw `receipts/audio_digest.jsonl` entry: `operation`,
  `tool_name`, `tool_version`, and `output_track` are required bounded
  strings; `operation` must be audio-digest related; `status` must be
  `"success"` or `"failure"`; `output_track` must equal the one
  canonical `tracks/audio_digest_events.jsonl` path
  (`constants.AUDIO_DIGEST_TRACK_FILE`); `event_count` must be a sane
  non-negative integer; optional `failure_details`/`warnings`/
  `record_type_counts` are shape-checked if present. Adapted from, not
  a reuse of, `analysis_lanes.validate_analysis_receipt` — the two
  receipt shapes differ (`output_track` is a single string here, not a
  list, and there is no `adapter_name` field).
- `validate_audio_digest_receipts(records, *, limits=DEFAULT_LIMITS)` —
  batch wrapper, same convention as `validate_analysis_receipts`. An
  empty list is valid.

## Records are optional, but validated when present

A package with no audio digest track is unaffected — `validate_package`
never requires one, exactly like every analysis lane. Once a package
has one, every record in it is checked to the same Phase 3.4 standard
a CLI-appended record already meets, whether that record arrived via
`clulatent audio-digest append(-file)` or was hand-edited afterward.

## Manifest consistency

- If the manifest declares an `audio_digest_events` track but its file
  is missing from disk, this is already a hard failure via the
  existing generic per-track check (`track file listed in manifest is
  missing`) — no new code was needed for this case.
- If `tracks/audio_digest_events.jsonl` exists on disk but is not
  declared in the manifest, it is silently ignored, following the
  exact same convention already used for every other track type:
  `validate_package()`'s per-track loop only ever iterates
  `manifest.tracks`, so an undeclared track file is never inspected by
  any validator, audio-digest or otherwise.

## Safety behavior

- `validate_package()` remains fully read-only: it never mutates
  `manifest.json`, any track file, `receipts/audio_digest.jsonl`, or
  `index/search.sqlite`, and never creates a report file.
- Every new check runs against the already-bounded JSONL read
  (`security.jsonl.iter_jsonl_bounded`) already used for every other
  track/receipt file in this function — an oversized or malformed
  audio digest track/receipt file is caught the same strict way any
  other track/receipt file already is.
- Dense arrays (`values`, `samples`, `frames`, `raw`, `dense_values`,
  `series_values`, `embedding`, `embeddings`) remain rejected at the
  payload level by `audio_digest.validate_audio_digest_track`, exactly
  as in Phase 3.4 — this phase adds no new dense-data exception.

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated LLM summaries and no semantic truth generation.
  Validation only checks that records already on disk are shaped
  safely — it never generates or infers audio evidence.
- No claim that CLULatent understands audio.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.
- No index rebuild and no new receipt/report written by `validate`
  itself.

## Relation to Phase 3.4, Phase 3.5, and Phase 3.6

Phase 3.4 (`docs/PHASE_3_4_AUDIO_DIGEST_SCHEMA_PRIMITIVES.md`) defined
the non-raising `(errors, warnings)` record validators. Phase 3.5
(`docs/PHASE_3_5_AUDIO_DIGEST_WRITER_RECEIPTS.md`) defined the
lock-aware writer that calls those validators before committing a
batch. Phase 3.6 (`docs/PHASE_3_6_AUDIO_DIGEST_CLI_COMMANDS.md`) put a
CLI front door on both of those. Phase 3.7 closes the loop at the
package level: `clulatent validate` now recognizes audio digest
tracks/receipts exactly the way it already recognizes review and
analysis-lane tracks/receipts (Phase 2.0, Phase 2.9), reusing the same
generic per-track checks and the same optional-receipt-file pattern
rather than inventing new validation machinery.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_validate_audio_digest.py` suite, with no regression in
existing report/demo/adapter/validation/locking/review/analysis-lane/
audio-digest/audio-digest-writer/audio-digest-CLI tests.
