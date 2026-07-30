# Phase 3.6 — Audio Digest CLI Commands

Status: **CLI exposure of already-existing validation/writer primitives
only — still no audio adapter, model, or runtime wiring**. Base: Phase
3.5 Audio Digest Writer Receipts is frozen (tag
`phase-3.5-audio-digest-writer-receipts-freeze`). Builds directly on
`src/clu_latent/audio_digest.py` (Phase 3.4) and
`src/clu_latent/audio_digest_writer.py` (Phase 3.5), mirroring the
`clulatent analysis ...` command group (Phase 2.8) almost exactly.

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Phase 3.4 validated that a digest record is shaped safely. Phase 3.5
made it possible to commit already-validated records into a package.
Phase 3.6 is the first place a human can drive both of those from a
terminal, without writing Python: it does not add any new validation
rule or writer behavior — it is a thin, safe front door onto the code
that already existed.

## What this phase implements

- `clulatent audio-digest types` — prints the supported audio digest
  record type names (`audio_digest.AUDIO_DIGEST_RECORD_TYPES`):
  `audio_feature_series`, `audio_digest_segment`,
  `audio_llm_context_packet`.
- `clulatent audio-digest validate-file <jsonl-file>` — reads a JSONL
  file (or a JSON array) of candidate digest records, validates every
  record with `audio_digest.validate_audio_digest_track`, and prints a
  PASS/FAIL summary. Never requires a package (an optional `--package`
  only tightens `data_path` checking to full filesystem containment).
  Never writes anything — no track, manifest, or receipt file is
  created, even when `--package` is given.
- `clulatent audio-digest append <package> --event-json '<json>'` —
  parses one JSON object, then calls
  `audio_digest_writer.append_audio_digest_events` with it as a
  single-record batch: validates, writes
  `tracks/audio_digest_events.jsonl`, updates the manifest's track
  descriptor, and writes a receipt to `receipts/audio_digest.jsonl`
  (unless `--no-receipt` is given).
- `clulatent audio-digest append-file <package> <jsonl-file>` — same,
  for a whole batch loaded from a JSONL file or JSON array. All records
  are validated together before anything is written.
- `tests/test_cli_audio_digest.py` — direct tests of the commands.
- This document.
- A Phase 3.6 roadmap bullet in `README.md`.

Both `append` and `append-file` support `--tool-name`, `--tool-version`
(recorded on the receipt), `--linked-evidence-json` (optional, bounded,
JSON-serializable metadata recorded on the receipt only, never written
into the track itself), `--receipt/--no-receipt`, and
`--force-stale-lock` — the same option shapes already used by
`clulatent analysis append`/`append-file`.

## Commands write only validated digest records

Every command in this group ultimately calls either
`audio_digest.validate_audio_digest_track` (read-only) or
`audio_digest_writer.append_audio_digest_events` (which itself
re-validates before writing). No command in this file generates,
infers, or invents a digest record — every record a user passes to
`append`/`append-file` must already be shaped exactly as Phase 3.4
requires, or the whole call is refused and nothing is written.

## Safety behavior

- `validate-file` never mutates a package or creates any file, with or
  without `--package`.
- `append`/`append-file` refuse cleanly (nonzero exit, no traceback,
  package untouched) for: invalid JSON, an invalid record, a missing
  events file, a missing/non-directory package, a package with a valid
  integrity lock, a duplicate id (within the batch or against the
  existing track), and an unsupported digest type.
- `append-file` rejects an empty file and refuses to partially write —
  every record in the batch is validated before the track, manifest,
  or receipt is touched.
- All error text is passed through `security.console.safe_console_text`
  before being printed, so a hostile value embedded in a candidate
  record (e.g. an ANSI escape sequence smuggled into `type`) cannot
  alter terminal formatting or emit raw control sequences.
- These commands never touch source tracks, analysis-lane tracks, the
  search index, or generated reports — only
  `tracks/audio_digest_events.jsonl`, its manifest track descriptor,
  and `receipts/audio_digest.jsonl`.

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated LLM summaries and no semantic truth generation.
  These commands write records a caller already built; they never
  generate or infer audio evidence themselves.
- No claim that CLULatent understands audio.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## Relation to Phase 3.4 and Phase 3.5

Phase 3.4 (`docs/PHASE_3_4_AUDIO_DIGEST_SCHEMA_PRIMITIVES.md`) defined
the non-raising `(errors, warnings)` validators. Phase 3.5
(`docs/PHASE_3_5_AUDIO_DIGEST_WRITER_RECEIPTS.md`) defined the
lock-aware, rollback-safe writer that calls those validators before
committing a batch. Phase 3.6 adds no new validation rule and no new
writer behavior — `clulatent audio-digest validate-file` and
`clulatent audio-digest append`/`append-file` are direct CLI callers of
those two modules' existing public functions, exactly the same
relationship `clulatent analysis ...` (Phase 2.8) has to
`analysis_lanes.py` (Phase 2.6) and `analysis_writer.py` (Phase 2.7).

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_cli_audio_digest.py` suite, with no regression in existing
report/demo/adapter/validation/locking/review/analysis-CLI/audio-digest/
audio-digest-writer tests.
