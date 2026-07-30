# Phase 3.13 — Keyframe Evidence Retrieval CLI

## Summary

Phase 3.12 rendered a package's stored keyframes as a static HTML
contact sheet — evidence you *look at*. Phase 3.13 makes the same
evidence **retrievable as data**: a small set of read-only primitives
(and CLI commands) that return the keyframe records ingest already
stored, addressed by id, by time range, by nearest timestamp, or as a
shallow summary.

## Core rule

> **Show visual evidence. Do not interpret visual evidence.**

This is a **visual evidence retriever, not a visual understanding
system.** It never runs visual AI, never captions a frame, never infers
scene meaning / object identity / intent / emotion, never runs OCR or
motion analysis, and never extracts a new frame. It only reads keyframe
records ingest already recorded and hands them back verbatim.

## What it does

New module `src/clu_latent/keyframe_retrieval.py`:

- `load_keyframe_events(package)` — load the `keyframes` track as plain
  dicts, ordered by `t_start_ms` then id. An absent track yields `[]`
  (not an error); a declared-but-missing/oversized/malformed track, or a
  record with an unsafe image path, raises `KeyframeRetrievalError`.
- `get_keyframe_by_id(package, event_id)` — one record, or `None`.
- `query_keyframes_by_time_range(package, start_ms, end_ms)` — every
  keyframe whose `[t_start_ms, t_end_ms]` overlaps the range, in order.
  An inverted range raises; an empty result does not.
- `get_nearest_keyframe(package, time_ms)` — the keyframe closest to a
  timestamp (distance measured to the frame's interval, zero when
  inside). Ties break deterministically: smallest distance, then
  earliest `t_start_ms`, then lexicographically smallest id.
- `summarize_keyframe_retrieval(package)` — count, first/last timestamp,
  the ordered event ids, and any keyframe image files missing from disk.

New CLI commands under `clulatent keyframes`:

```
clulatent keyframes get PACKAGE kf_000014
clulatent keyframes query-time PACKAGE --start-ms 10000 --end-ms 16000
clulatent keyframes nearest PACKAGE --time-ms 14500
clulatent keyframes retrieval-summary PACKAGE
```

Each keyframe is shown with its timestamp, id, package-relative image
path (kept visible), and whether the image file is present on disk.

## What it deliberately does NOT do

- No visual AI, no frame captioning, no OCR, no motion/scene/object
  analysis, no intent/emotion inference.
- No FFmpeg, no new frame extraction, no image decoding, no ML
  dependency.
- No package mutation: it never writes a manifest, track, receipt,
  index, or lock file.
- No network access; no dense-data dump (only the small stored keyframe
  records, never raw image bytes). Output is bounded
  (`DEFAULT_MAX_KEYFRAMES`).

## Safety properties

- Every stored image path is checked through `resolve_in_package`
  (containment / symlink rejection). A record whose path escapes the
  package causes the whole retrieval to refuse cleanly, rather than
  returning unsafe data.
- Console output is escaped/sanitised via `safe_console_text`.
- Package/track-level failures always surface as a clean
  `KeyframeRetrievalError` / exit-1 CLI error, never a traceback.

## Degraded-input handling

- **Missing id** → `get` prints "no result" (exit 0).
- **Empty / non-overlapping time range** → clean empty result (exit 0).
- **Inverted time range** → clean error (exit 1).
- **Empty keyframes track / no track** → zeroed-out summary, `nearest`
  returns nothing (exit 0).
- **Missing keyframe image file** → reported in the summary and per-
  record, not a crash.
- **Missing/oversized/malformed track, unsafe image path, unreadable
  package** → clean `KeyframeRetrievalError` / exit-1 error.

## Files

- `src/clu_latent/keyframe_retrieval.py` — retrieval primitives.
- `src/clu_latent/cli.py` — the `keyframes get`, `query-time`,
  `nearest`, and `retrieval-summary` commands.
- `tests/test_keyframe_retrieval.py` — tests for id/time/nearest/summary
  retrieval, tie determinism, degraded inputs, path-safety, read-only /
  no-receipt behaviour, and clean failures.

## Trust position

Adapters produce evidence, not truth; generated does not mean canonical.
This phase makes CLULatent's stored keyframe evidence retrievable. It
does **not** make CLULatent understand visuals.
