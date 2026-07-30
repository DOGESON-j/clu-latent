# Phase 3.18 — Package Reader / Parser API v0

## Summary

Phase 3.18 gives `.clulatent` an **official reader**. Until now a
`.clulatent` package was openable only by knowing its internal folder
convention (`manifest.json`, `tracks/*.jsonl`, `receipts/`, `lock/`,
`index/`, `media/`) and by calling a scattering of per-lane retrieval
modules (`keyframe_retrieval.py`, `visual_change_retrieval.py`,
`changed_region_retrieval.py`, `evidence_bundle_retrieval.py`,
`agent_review_retrieval.py`, `audio_digest_retrieval.py`). Each of those
modules independently re-implemented "find the manifest, look up the
track descriptor by name, resolve its file safely, read the JSONL".

`src/clu_latent/package_reader.py` wraps that existing structure in one
small, stable, read-only spine:

```python
from clu_latent.package_reader import open_package

reader = open_package("/path/to/movie.clulatent")
print(reader.summary())
for event in reader.query_time(0, 10000):
    print(event.id, event.track_name, event.timestamp_ms)
```

The caller never has to know that keyframes live in
`tracks/keyframes.jsonl` or that evidence bundles live in
`tracks/evidence_bundles.jsonl`. They ask the reader for a track by
**name**; the reader consults `manifest.json`'s declared track
descriptors and resolves the file safely.

This phase is deliberately **not** an SDK. It adds no new evidence lane,
no semantic interpretation, no network/model calls, and no new package
mutation. It is the smallest thing that makes `.clulatent` feel like a
real, openable format instead of a folder convention.

## Why this phase exists

- **`.clulatent` should be openable without insider knowledge.** A
  developer integrating CLULatent should be able to `open_package(...)`
  and inspect manifest, tracks, events, receipts, lock state, and
  validation status without reading the ingest/writer source to learn
  filenames.
- **Retrieval logic was duplicated per lane.** Every Phase 3.13–3.17
  retrieval module re-derived "manifest → track descriptor → safe file
  resolve → bounded JSONL read". The reader centralizes that spine so a
  future lane plugs into one place.
- **The format needs a stable read surface before it grows.** Later
  phases (CLUBIN compilation, cross-package queries, remote readers)
  need one contract for "how do I open and inspect a package" that does
  not change every time a lane is added.

## Reader vs validator — the important distinction

CLULatent already has a **validator** (`validate.py`,
`validate_package`). The reader does **not** replace or duplicate it.

| | Validator (`validate_package`) | Reader (`package_reader`) |
|---|---|---|
| Question it answers | *Is this package valid/canonical?* | *What is in this package? Give me structured access.* |
| Judges validity | Yes — returns pass/fail + errors | No — it exposes; it does not rule |
| Verifies source hash, sidecar, keyframe files | Yes | No |
| Reads tracks/events by name | No | Yes |
| Query by time, resolve event ids | No | Yes |
| Relationship | Authority on validity | Delegates to the validator via `reader.validate()` |

`reader.validate()` calls the existing `validate_package` and returns its
`ValidationReport` unchanged. The reader owns **structured access**; the
validator owns **judgement of validity**. The reader never re-implements
a validity rule.

## What "officially openable" means

A package is *officially openable* when a caller can, from Python and
without knowing the internal folder layout:

1. Open it by path (`open_package(path)`).
2. Read its manifest-level facts (`package_id`, `duration_ms`, `status`,
   `created_at`, source info).
3. List its tracks by name (`list_tracks()`, `has_track(name)`).
4. Read/iterate a track's events (`load_track(name)`,
   `iter_events(name)`).
5. Query events across tracks by time (`query_time(start, end)`).
6. Resolve an event by id across tracks (`get_event(id)`,
   `resolve_event_ref(ref)`).
7. Inspect receipts and lock status read-only (`receipts()`,
   `lock_status()`).
8. Ask the validator for validity (`validate()`).
9. Get a bounded structured overview (`summary()`).

…and the package is never mutated in the process.

## Supported API

All returned values are **structured data** (dataclasses / dicts /
lists), never CLI-formatted text.

### Opening

- `open_package(path, *, strict=True, limits=DEFAULT_LIMITS) -> PackageReader`
- `PackageReader.open(path, *, strict=True, limits=DEFAULT_LIMITS)` — same thing.

Opening loads and schema-validates `manifest.json` (bounded read). It
does **not** run the full validator, run ffprobe, or read every track —
opening stays cheap. A missing package, missing/oversized/invalid
`manifest.json` raises `PackageReaderError` (or `MalformedPackageError`).

### Manifest-level properties

- `reader.path` — resolved package `Path`.
- `reader.manifest` — the validated `Manifest` model.
- `reader.package_id` — `manifest.package_id`.
- `reader.duration_ms` — `manifest.source.duration_ms`.
- `reader.created_at`, `reader.status`, `reader.has_audio`.

### Tracks

- `reader.list_tracks() -> list[TrackHandle]` — one handle per
  manifest-declared track (`name`, `file`, `schema_id`,
  `schema_version`, `record_count`, `sorted_by`, `known`).
- `reader.has_track(name) -> bool`.
- `reader.load_track(name) -> list[EventRecord]` — resolved safely,
  read bounded, ordered by `t_start_ms` then `id`.
- `reader.iter_events(name) -> Iterator[EventRecord]`.

### Events

- `EventRecord` — frozen dataclass: `id`, `type`, `track_name`,
  `t_start_ms`, `t_end_ms`, `timestamp_ms` (== `t_start_ms`),
  `producer`, `confidence`, `payload`, `raw` (the full record dict).
- `reader.get_event(event_id, *, track_names=None) -> EventRecord | None`.
- `reader.query_time(start_ms, end_ms=None, *, track_names=None) -> list[EventRecord]`
  — every event whose `[t_start_ms, t_end_ms]` overlaps `[start_ms,
  end_ms]`, across all (or the named) tracks, ordered by time then
  track then id. `end_ms=None` means "to the end of the package".
- `reader.resolve_event_ref(ref_or_id) -> EventRecord | None` — accepts
  a bare event id, a `"track_name:event_id"` string, or a
  `{"track": ..., "id": ...}` / `{"id": ...}` dict.

### Receipts / lock / validation / summary

- `reader.receipts() -> list[ReceiptHandle]` — one handle per receipt
  JSONL file present under `receipts/` (`name`, `file`, `exists`); each
  handle can `.records()` (bounded read) on demand.
- `reader.lock_status() -> dict` — wraps `lock.lock_status`; reports
  `status` (`unlocked` / `locked` / `lock-invalid` / `lock-partial`)
  and, when present, hash validity — read-only, never mutates lock
  state.
- `reader.validate() -> ValidationReport` — delegates to
  `validate_package`.
- `reader.summary() -> dict` — a bounded structured overview: ids,
  duration, status, per-track counts + known/unknown, receipt files,
  lock status.

## Safety rules

Every video/package/path/JSONL/SQLite/subprocess/model output is
untrusted. The reader treats the package it opens as untrusted data:

- **Path safety.** Every manifest-declared track/receipt file is
  resolved through `security.paths.resolve_in_package` (containment- and
  symlink-checked). A traversal/symlink-escaping path raises, it is
  never read.
- **Bounded reads.** Manifest, tracks, and receipts are read through the
  existing bounded readers (`Manifest.from_json_file`,
  `tracks.read_track_file` / `iter_jsonl_bounded`) — an oversized file
  fails cleanly instead of exhausting memory.
- **No silent corruption.** In the default **strict** mode, a malformed
  JSONL line or a record that fails the shared event envelope raises
  `MalformedPackageError`. In non-strict mode, bad lines are skipped
  **but recorded** on `reader.read_warnings` — never silently swallowed.
- **Read-only.** The reader never writes, never creates receipts, never
  touches lock state, never rebuilds the index. It does not run FFmpeg,
  Pillow, ML, network, or model calls.
- **No interpretation.** The reader returns the records ingest/writers
  already stored. It never claims what a keyframe depicts, what audio
  means, or what any event signifies. Detected ≠ trusted; generated ≠
  canonical.
- **Known vs unknown tracks.** Tracks whose name is in the known
  CLULatent catalog are flagged `known=True`. A manifest may declare an
  unknown track; the reader will **list** it and can read it, but marks
  it `known=False` so a caller never mistakes an arbitrary declared
  track for a trusted canonical lane. Trust is the validator's call, not
  the reader's.

## Non-goals

- No new evidence lane, no new track type, no new writer.
- No semantic interpretation of visual/audio content; no object/person/
  action/intent/scene claims.
- No network, model, ML, FFmpeg, or Pillow calls.
- No package mutation, no receipt writing, no lock modification, no
  index rebuild.
- Not a general SDK, not CLUBIN, not a cross-package query engine.
- The CLI is **not** the center of this phase — a thin optional
  `clulatent package inspect` / `clulatent package tracks` may wrap the
  library, but the library API is the contract.

## Example Python usage

```python
from clu_latent.package_reader import open_package

reader = open_package("/path/to/movie.clulatent")

# Manifest-level facts, no folder knowledge needed.
print(reader.package_id, reader.duration_ms, reader.status)

# Tracks by name — caller never types a filename.
for track in reader.list_tracks():
    print(track.name, track.record_count, "known" if track.known else "unknown")

# Events by time, across all tracks.
for event in reader.query_time(0, 10_000):
    print(event.track_name, event.id, event.timestamp_ms)

# Resolve an evidence/event reference.
kf = reader.get_event("kf_000003")
bundle = reader.resolve_event_ref("evidence_bundles:eb_000000")

# Inspect receipts, lock, validity — all read-only.
print([r.name for r in reader.receipts()])
print(reader.lock_status())
print(reader.validate().valid)
print(reader.summary())
```

## How future lanes plug into the reader

The reader keys everything off `manifest.tracks`. When a future phase
adds a new lane, its writer already registers a `TrackDescriptor`
(name + file + schema + count) into the manifest — exactly like every
existing lane. That means:

1. **No reader change is required to *read* a new lane** — `load_track`,
   `iter_events`, `query_time`, `get_event`, and `resolve_event_ref`
   work over any manifest-declared track immediately.
2. To have the new lane counted as a **known** canonical track (rather
   than `known=False`), add its track name to the reader's known-track
   catalog (`KNOWN_TRACK_NAMES`) — the single line that keeps the
   known-track set in sync with the format.
3. Lane-specific convenience (e.g. "nearest keyframe") stays in the
   lane's own retrieval module; the reader intentionally stays generic.

This keeps the reader stable as the format grows: new lanes flow through
the manifest, and the reader exposes them without a schema change to its
own API.
