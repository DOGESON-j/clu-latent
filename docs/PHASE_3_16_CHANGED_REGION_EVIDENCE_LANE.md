# Phase 3.16 — Changed Region Evidence Lane

## Summary

Phase 3.16 adds a non-semantic **changed-region evidence lane**: for
every Phase 3.15 visual change candidate, it localizes *where inside
the frame* the strongest pixel difference occurred between that
candidate's linked source/target keyframe images. Phase 3.15 answers
"did adjacent keyframes change?" — Phase 3.16 answers "which region of
the frame changed most?" Neither answers "what changed?"

## Core rule

> **Changed-region evidence, not semantic interpretation.**

This is a **region localizer, not a scene understanding system.** It
never runs visual AI, never detects objects/faces/people/text/logos,
never runs OCR, never captions a frame, and never infers actions,
intent, emotion, or scene meaning. It only reports which fixed grid
cell(s) of two already-stored keyframe images differ most, numerically,
using a bounded grayscale-thumbnail per-cell mean-absolute-difference.

## What it does

New module `src/clu_latent/changed_region.py`:

- A bounded, fixed grid (`GRID_ROWS` x `GRID_COLS`, 8x8) is laid over
  both a visual change candidate's source and target keyframe images,
  each downscaled first to a small, fixed thumbnail size so compute
  cost never depends on the source keyframe resolution.
- Per-cell mean-absolute-difference is computed on grayscale pixel
  data; the strongest cell (or a small, contiguous, above-threshold
  cluster, via `SELECTED_CELL_RATIO`) is selected and scaled back into
  a pixel bounding box against the actual source image dimensions.
- `classify_change_scope()` buckets the result into `localized`,
  `distributed`, `global`, or `unknown` using the region's own
  normalized delta (not a diluted whole-frame average, which would
  misclassify a strongly localized change as `unknown`).
- `validate_changed_region_event` / `validate_changed_region_track` /
  `validate_changed_region_receipt(s)` — non-raising `(errors,
  warnings)` shape validators: closed payload key whitelist, pixel
  bounding box within image bounds, normalized bounding box within
  `0..1`, fixed grid shape, bounded metrics, a supported `strength` and
  `change_scope` value, and the fixed, exact caveat pair below. Accept
  optional `known_visual_change_ids` / `known_keyframe_ids` sets to
  cross-check that a record's linked ids actually exist elsewhere in
  the package — used by `clulatent validate`, skipped when validating
  one record in isolation.
- A forbidden-language check (`FORBIDDEN_CHANGED_REGION_PHRASES`)
  applied to the free-text `method` and `created_by` payload fields, as
  defense-in-depth against a hand-edited record smuggling a semantic
  claim into this lane. `caveats` is checked by exact equality against
  the fixed pair instead, since substring-scanning that fixed text
  itself would trip on words it uses in negation (e.g. "objects",
  "text", "intent").

New module `src/clu_latent/changed_region_writer.py`:

- `analyze_changed_regions(package, ...)` — reads the already-computed
  `visual_change_candidates` track (Phase 3.15's output, via
  `visual_change_retrieval.load_visual_change_events`) as its source of
  truth, not the raw `keyframes` track directly. Computes one
  `changed_region_candidate` per visual change candidate and
  **replaces** the whole `changed_region_candidates` track in one call
  (a recomputation, not an append). Refuses if the track already exists
  unless `force=True`. Atomic, lock-aware, rollback-on-failure writes
  (track, then manifest, then receipt).
- Writes a receipt to `receipts/changed_region.jsonl` for every write
  (unless `--no-receipt`), recording both `strength_counts` and
  `change_scope_counts`.

New module `src/clu_latent/changed_region_retrieval.py` (read-only,
mirrors `visual_change_retrieval.py`):

- `load_changed_region_events(package)` — every record, ordered by
  `t_start_ms` then id. An absent track yields `[]` (not an error).
- `get_changed_region_event_by_id(package, event_id)` — one record, or
  `None`.
- `query_changed_regions_by_time_range(package, start_ms, end_ms)` —
  overlap query; an inverted range raises.
- `query_changed_regions_by_visual_change_id(package, visual_change_id)`
  — every region record linked to one visual change candidate.
- `summarize_changed_regions(package)` — count, first/last event id and
  timestamp, a count of records per strength bucket, and a count of
  records per `change_scope` bucket.

New CLI commands under `clulatent changed-regions`:

```
clulatent changed-regions analyze PACKAGE [--force] [--no-receipt]
clulatent changed-regions summary PACKAGE
clulatent changed-regions get PACKAGE cr_000000
clulatent changed-regions query-time PACKAGE --start-ms 0 --end-ms 5000
clulatent changed-regions query-visual-change PACKAGE vc_000000
```

`analyze` is the only command in this group that writes to a package
(track, manifest, receipt), and requires `clulatent visual-change
analyze` to have already run. `summary`, `get`, `query-time`, and
`query-visual-change` are strictly read-only and never create a
receipt.

## Record shape

Track: `tracks/changed_region_candidates.jsonl`.

```json
{
  "id": "cr_000000",
  "type": "changed_region_candidate",
  "t_start_ms": 0,
  "t_end_ms": 2000,
  "payload": {
    "visual_change_id": "vc_000000",
    "source_keyframe_id": "kf_000000",
    "target_keyframe_id": "kf_000001",
    "source_image_path": "media/keyframes/kf_000000.jpg",
    "target_image_path": "media/keyframes/kf_000001.jpg",
    "region": {"x": 80, "y": 40, "width": 160, "height": 80, "coordinate_system": "pixel"},
    "normalized_region": {"x": 0.25, "y": 0.2, "width": 0.5, "height": 0.4, "coordinate_system": "normalized_0_1"},
    "grid": {"rows": 8, "cols": 8, "selected_cells": [[2, 2], [2, 3]]},
    "metrics": {
      "region_mean_absolute_difference": 41.2,
      "region_normalized_delta": 0.16,
      "frame_normalized_delta": 0.05,
      "region_to_frame_ratio": 3.2
    },
    "change_scope": "localized",
    "strength": "medium",
    "caveats": [
      "Changed-region evidence, not semantic interpretation.",
      "Does not identify objects, people, text, actions, intent, or scene meaning."
    ],
    "created_by": "clulatent 0.x.x",
    "method": "pillow_grayscale_grid_mean_abs_diff"
  }
}
```

## Allowed vs. forbidden claims

Allowed: "changed region candidate", "strongest changed region", "grid
cell change", "bounding box of visual difference", "localized change",
"global change", "region delta score", "linked visual change
candidate", "linked source/target keyframes".

Forbidden (enforced by `FORBIDDEN_CHANGED_REGION_PHRASES` on free-text
fields): "person", "face", "object", "car", "weapon", "text", "logo",
"action", "intent", "emotion", "scene meaning", and similar semantic
claims — "the clip shows...", "the model understands...".

## What it deliberately does NOT do

- No visual AI, no object/face/people/text/logo detection, no OCR, no
  captions, no scene/action/intent/emotion inference.
- No network access, no external model dependency.
- No dense-data dump: only the small stored region/grid/metric
  records, never raw image bytes, difference masks, or full pixel
  maps, are returned or printed.
- Read-only commands (`summary`, `get`, `query-time`,
  `query-visual-change`) never mutate a package or create a receipt.

## Safety properties

- Every stored image path is checked through `resolve_in_package`
  before being handed back by a retrieval call; an unsafe path causes a
  clean refusal, not silently-returned unsafe data.
- `analyze` is guarded by the package integrity lock and an operation
  lock, and performs ordered atomic writes (track, then manifest, then
  receipt) with best-effort rollback on failure.
- `analyze` refuses to overwrite an existing `changed_region_candidates`
  track unless `--force` is passed, and even then only touches that
  track plus the manifest — no other track or file is modified.
- `validate_changed_region_track` is wired into `clulatent validate`,
  including cross-track checks that every `visual_change_id`,
  `source_keyframe_id`, and `target_keyframe_id` a changed-region
  record links to actually exists in the package's other tracks.
  `receipts/changed_region.jsonl`, if present, gets the same bounded
  parse. Absence of either file is never an error.
- Console output is escaped/sanitised via `safe_console_text`, and
  bounded (`DEFAULT_MAX_CHANGED_REGION_EVENTS`).

## Optional dependency

Pillow is required to *compute* changed-region metrics (`analyze`),
but not to read already-computed records (`summary`, `get`,
`query-time`, `query-visual-change`). Install it via the `visual`
extra: `pip install -e ".[visual]"`. If Pillow is unavailable,
`analyze` fails cleanly with a message pointing at the extra, rather
than raising an import error.

## Degraded-input handling

- **Missing id** → `get` returns `None` / prints "no result" (exit 0).
- **Empty / non-overlapping time range** → clean empty result (exit 0).
- **Inverted time range** → clean error (exit 1).
- **No changed-region track yet** → zeroed-out summary (exit 0).
- **No linked visual change candidate** → `query-visual-change` returns
  an empty result (exit 0), not an error.
- **No `visual_change_candidates` track yet** → `analyze` raises
  `ChangedRegionComputeError` (clean CLI exit 1) — this lane has a hard
  dependency on `clulatent visual-change analyze` having already run.
- **Missing keyframe image file** → `analyze` raises
  `ChangedRegionComputeError`.
- **Unsafe stored image path** → retrieval raises
  `ChangedRegionRetrievalError`.
- **Existing track, no `--force`** → `analyze` raises
  `ChangedRegionWriteError` (clean CLI exit 1).
- **Corrupted/hand-edited track, or a dangling `visual_change_id` /
  keyframe id link** → `clulatent validate` reports the specific
  validation error.

## Files

- `src/clu_latent/changed_region.py` — schema, bounded grid-based
  metric computation, validators.
- `src/clu_latent/changed_region_writer.py` — lock-aware `analyze`
  writer and receipts.
- `src/clu_latent/changed_region_retrieval.py` — read-only retrieval.
- `src/clu_latent/validate.py` — wires changed-region track and
  receipt validation, including cross-track link checks, into
  `clulatent validate`.
- `src/clu_latent/cli.py` — the `changed-regions analyze`, `summary`,
  `get`, `query-time`, and `query-visual-change` commands.
- `tests/test_changed_region.py` — schema/validator tests, bounded-
  metric tests, forbidden-language tests, e2e analyze/summary/get/
  query-time/query-visual-change tests, receipt tests, cross-track
  validation tests, and clean-failure tests.

## Trust position

Adapters produce evidence, not truth; generated does not mean
canonical. This phase makes it possible to say *where* visual change
was strongest inside a frame, using only already-stored keyframe
images and already-computed visual change candidates. It does **not**
make CLULatent understand what changed.
