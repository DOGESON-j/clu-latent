# Phase 3.15 — Visual Change Evidence Lane

## Summary

Phase 3.15 adds a non-semantic **visual change evidence lane**: bounded,
numeric pixel-difference candidates computed between adjacent stored
keyframes (already extracted by earlier phases). It records *where a
package's frames look visually different over time*, and never what
that difference means.

## Core rule

> **Visual change evidence, not semantic interpretation.**

This is a **pixel-difference measurer, not a scene understanding
system.** It never runs visual AI, never detects objects/faces/people,
never runs OCR, never captions a frame, and never infers scene meaning,
intent, or emotion. It only measures how much two already-stored
keyframe images differ, numerically, using a bounded grayscale-thumbnail
mean-absolute-difference plus an average-hash Hamming distance.

## What it does

New module `src/clu_latent/visual_change.py`:

- Bounded, non-semantic metrics computed between adjacent keyframe
  images: `mean_absolute_difference` (0-255, on a 64x64 grayscale
  downscale), `normalized_delta` (0-1), and `perceptual_hash_distance`
  (0-64, 8x8 average-hash Hamming distance).
- `strength_for_normalized_delta()` buckets `normalized_delta` into
  `low` / `medium` / `high` against fixed thresholds (0.10 / 0.30).
- `compute_visual_change_events(package)` loads keyframes via
  `keyframe_retrieval.load_keyframe_events`, requires at least two
  keyframes, and produces one `visual_change_candidate` record per
  adjacent pair (capped at `DEFAULT_MAX_VISUAL_CHANGE_PAIRS`).
- `validate_visual_change_event` / `validate_visual_change_track` /
  `validate_visual_change_receipt(s)` — non-raising `(errors, warnings)`
  shape validators, enforcing a closed payload key whitelist, bounded
  metric ranges, a supported `strength` value, and the fixed, exact
  caveat pair below.
- A forbidden-language check (`FORBIDDEN_VISUAL_CHANGE_PHRASES`) applied
  to the free-text `method` and `created_by` payload fields, as
  defense-in-depth against a hand-edited record smuggling a semantic
  claim into this lane. `caveats` is checked by exact equality against
  the fixed pair instead, since substring-scanning that fixed text
  itself would trip on words like "intent" used in negation.

New module `src/clu_latent/visual_change_writer.py`:

- `analyze_visual_change(package, ...)` — computes visual change events
  fresh from the currently stored keyframes and **replaces** the whole
  `visual_change_candidates` track in one call (this is a
  recomputation, not an append). Refuses if the track already exists
  unless `force=True`. Atomic, lock-aware, rollback-on-failure writes
  (track, then manifest, then receipt).
- Writes a receipt to `receipts/visual_change.jsonl` for every write
  (unless `--no-receipt`).

New module `src/clu_latent/visual_change_retrieval.py` (read-only,
mirrors `keyframe_retrieval.py`):

- `load_visual_change_events(package)` — every record, ordered by
  `t_start_ms` then id. An absent track yields `[]` (not an error).
- `get_visual_change_event_by_id(package, event_id)` — one record, or
  `None`.
- `query_visual_change_by_time_range(package, start_ms, end_ms)` —
  overlap query; an inverted range raises.
- `summarize_visual_change(package)` — count, first/last event id and
  timestamp, and a count of records per strength bucket.

New CLI commands under `clulatent visual-change`:

```
clulatent visual-change analyze PACKAGE [--force] [--no-receipt]
clulatent visual-change summary PACKAGE
clulatent visual-change get PACKAGE vc_000000
clulatent visual-change query-time PACKAGE --start-ms 0 --end-ms 5000
```

`analyze` is the only command in this group that writes to a package
(track, manifest, receipt). `summary`, `get`, and `query-time` are
strictly read-only and never create a receipt.

## Record shape

Track: `tracks/visual_change_candidates.jsonl` (deliberately **not**
`visual_change_events` — that name is already used by the unrelated
Phase 2.15 ffmpeg-`scdet` analysis lane).

```json
{
  "id": "vc_000000",
  "type": "visual_change_candidate",
  "t_start_ms": 0,
  "t_end_ms": 2000,
  "payload": {
    "source_keyframe_id": "kf_000000",
    "target_keyframe_id": "kf_000001",
    "source_image_path": "media/keyframes/kf_000000.jpg",
    "target_image_path": "media/keyframes/kf_000001.jpg",
    "metrics": {
      "mean_absolute_difference": 12.4,
      "normalized_delta": 0.048,
      "perceptual_hash_distance": 3
    },
    "strength": "low",
    "caveats": [
      "Visual change evidence, not semantic interpretation.",
      "Does not identify objects, people, actions, intent, or scene meaning."
    ],
    "created_by": "clulatent 0.x.x",
    "method": "pillow_grayscale_thumbnail_mean_abs_diff"
  }
}
```

## Allowed vs. forbidden claims

Allowed: "visual change candidate", "frame difference score", "possible
boundary", "low/medium/high visual delta", "linked keyframes".

Forbidden (enforced by `FORBIDDEN_VISUAL_CHANGE_PHRASES` on free-text
fields): "a person enters", "the scene shows...", "the clip means...",
"tension", "intent", "emotion", "the model understands the scene".

## What it deliberately does NOT do

- No visual AI, no object/face/people detection, no OCR, no captions,
  no scene/action/intent/emotion inference.
- No network access, no external model dependency.
- No dense-data dump: only the small stored metric records, never raw
  image bytes or pixel arrays, are returned or printed.
- Read-only commands (`summary`, `get`, `query-time`) never mutate a
  package or create a receipt.

## Safety properties

- Every stored image path is checked through `resolve_in_package`
  before being handed back by a retrieval call; an unsafe path causes a
  clean refusal, not silently-returned unsafe data.
- `analyze` is guarded by the package integrity lock and an operation
  lock, and performs ordered atomic writes (track, then manifest, then
  receipt) with best-effort rollback on failure.
- `analyze` refuses to overwrite an existing `visual_change_candidates`
  track unless `--force` is passed, and even then only touches that
  track plus the manifest — no other track or file is modified.
- `validate_visual_change_track` is wired into `clulatent validate`,
  and `receipts/visual_change.jsonl`, if present, gets the same bounded
  parse. Absence of either file is never an error.
- Console output is escaped/sanitised via `safe_console_text`, and
  bounded (`DEFAULT_MAX_VISUAL_CHANGE_EVENTS`).

## Optional dependency

Pillow is required to *compute* visual change metrics (`analyze`), but
not to read already-computed records (`summary`, `get`, `query-time`).
Install it via the `visual` extra: `pip install -e ".[visual]"`. If
Pillow is unavailable, `analyze` fails cleanly with a message pointing
at the extra, rather than raising an import error.

## Degraded-input handling

- **Missing id** → `get` returns `None` / prints "no result" (exit 0).
- **Empty / non-overlapping time range** → clean empty result (exit 0).
- **Inverted time range** → clean error (exit 1).
- **No visual change track yet** → zeroed-out summary (exit 0).
- **Fewer than two keyframes / no keyframes track** → `analyze` raises
  `VisualChangeComputeError` (clean CLI exit 1).
- **Missing keyframe image file** → `analyze` raises
  `VisualChangeComputeError`.
- **Unsafe stored image path** → retrieval raises
  `VisualChangeRetrievalError`.
- **Existing track, no `--force`** → `analyze` raises
  `VisualChangeWriteError` (clean CLI exit 1).
- **Corrupted/hand-edited track** → `clulatent validate` reports the
  specific validation error.

## Files

- `src/clu_latent/visual_change.py` — schema, bounded metric
  computation, validators.
- `src/clu_latent/visual_change_writer.py` — lock-aware `analyze`
  writer and receipts.
- `src/clu_latent/visual_change_retrieval.py` — read-only retrieval.
- `src/clu_latent/validate.py` — wires visual change track and receipt
  validation into `clulatent validate`.
- `src/clu_latent/cli.py` — the `visual-change analyze`, `summary`,
  `get`, and `query-time` commands.
- `tests/test_visual_change.py` — schema/validator tests, bounded-
  metric tests, forbidden-language tests, e2e analyze/summary/get/
  query-time tests, receipt tests, and clean-failure tests.

## Trust position

Adapters produce evidence, not truth; generated does not mean
canonical. This phase makes visual change between stored keyframes
*measurable and retrievable*. It does **not** make CLULatent understand
visuals.
