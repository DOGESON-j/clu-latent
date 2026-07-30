# Phase 3.5 — Audio Digest Writer Receipts

Status: **safe append-only writer for already-validated audio digest
records — still no audio adapter, model, or runtime wiring**. Base:
Phase 3.4 Audio Digest Schema Primitives is frozen (tag
`phase-3.4-audio-digest-schema-primitives-freeze`). Builds directly on
`src/clu_latent/audio_digest.py` (Phase 3.4) and mirrors the structure
of `src/clu_latent/analysis_writer.py` (Phase 2.7).

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Phase 3.4 validated that a *record* is shaped safely. Phase 3.5 makes
it possible to actually commit already-validated records into a
package, with the same receipted, lockable, rollback-safe guarantees
every other CLULatent track write already has. This writer stores
validated audio digest evidence. It does not generate audio evidence.
It does not run any audio analysis. It does not claim CLULatent
understands audio.

## What this phase implements

- `src/clu_latent/audio_digest_writer.py` — the module. Exposes:
  - `AudioDigestWriteError` — the single exception type every public
    function in this module raises on failure.
  - `AudioDigestWriteResult` — dataclass returned by
    `append_audio_digest_events`/`append_audio_digest_event`.
  - `AudioDigestReceipt` — dataclass (with `to_dict()`) describing one
    write operation; includes `timestamp`, `operation`, `status`,
    `tool_name`/`tool_version`, `output_track`, `event_count`,
    `record_type_counts`, `linked_evidence`, `warnings`,
    `failure_details`, and a fixed evidence-not-truth reminder string.
  - `build_audio_digest_track_path(package_root)` — package-relative,
    symlink-safe path to `tracks/audio_digest_events.jsonl`.
  - `append_audio_digest_events(package_path, events, *, tool_name,
    tool_version, linked_evidence=None, write_receipt=True,
    force_stale_lock=False, limits=DEFAULT_LIMITS)` — validates the
    whole batch with Phase 3.4's `validate_audio_digest_track`, refuses
    empty batches, refuses any invalid event, refuses duplicate ids
    (against the existing track and within the new batch), then
    atomically writes the track file, the manifest's track descriptor,
    and a receipt, in that order, with rollback on any later failure.
  - `append_audio_digest_event(package_path, event, **kwargs)` —
    singular wrapper around `append_audio_digest_events`.
  - `write_audio_digest_receipt(package_path, receipt, *,
    force_stale_lock=False, limits=DEFAULT_LIMITS)` — appends a
    standalone receipt (e.g. to record a failure) without touching the
    track or manifest.
- `tests/test_audio_digest_writer.py` — direct tests of the module.
- This document.
- A Phase 3.5 roadmap bullet in `README.md`.

## Track and receipt files

- `tracks/audio_digest_events.jsonl` (`AUDIO_DIGEST_TRACK_FILE`,
  `constants.py`) — one shared, mixed-type track holding all three
  Phase 3.4 digest record types (`audio_feature_series`,
  `audio_digest_segment`, `audio_llm_context_packet`), matching how
  `validate_audio_digest_track` already validates a mixed-type
  iterable in one pass.
- `receipts/audio_digest.jsonl` (`AUDIO_DIGEST_RECEIPTS_FILE`,
  `constants.py`) — append-only receipt log, one entry per write
  operation (success or failure).

Both paths are already covered by `lock.py`'s generic
`tracks/*.jsonl`/`receipts/*.jsonl` globs, so no `lock.py` change was
needed.

## Records must already be validated

This writer does not perform record-shape validation itself beyond
re-running Phase 3.4's `validate_audio_digest_track` as a safety net —
it is the caller's responsibility to construct records that already
conform to `audio_digest.py`'s validators. If any record in a batch
fails validation, is a duplicate id, or the batch is empty, the writer
raises `AudioDigestWriteError` and does not touch the package at all.

## Package safety

- Refuses a missing or non-directory package root.
- Refuses unsafe paths (absolute paths, parent-traversal, symlink
  escapes) via the existing `security.paths.resolve_in_package`
  containment check.
- Refuses to write against a package with a valid integrity lock
  (`lock/package.lock.json`), same as `analysis_writer.py`.
- Acquires the short-lived operation lock
  (`lock/package.operation.lock.json`) around every write, guarding
  against concurrent writers.
- Writes happen in a fixed order — track file, then manifest, then
  receipt — with each earlier file's original bytes captured before
  being overwritten, so a failure at any later step rolls back every
  earlier change. A failed validation never touches the package at
  all.
- Never touches source tracks, analysis-lane tracks, the search index,
  or generated reports. Only the audio digest track, the audio digest
  track's manifest descriptor, and the audio digest receipt file are
  ever written.

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated LLM summaries and no semantic truth generation.
  This writer commits records a caller already built; it never
  generates or infers audio evidence itself.
- No retrieval CLI command, no `clulatent audio-digest …` command, no
  wiring into `validate.py`'s top-level package validation beyond the
  generic track-file schema check every track already gets.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## Relation to Phase 3.4

Phase 3.4 (`docs/PHASE_3_4_AUDIO_DIGEST_SCHEMA_PRIMITIVES.md`) defined
the non-raising `(errors, warnings)` validators for the three digest
record shapes. Phase 3.5 is the first place those validators are
actually invoked before a real write: `append_audio_digest_events`
calls `validate_audio_digest_track` internally and turns any
validation errors into a single raised `AudioDigestWriteError`,
following the same raising-writer-over-non-raising-validator
convention already used by `analysis_writer.py` over
`analysis_lanes.py`.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_audio_digest_writer.py` suite, with no regression in
existing report/demo/adapter/validation/locking/review/audio-lane/
audio-digest-contract/audio-digest-schema tests.
