# Phase 2.16 — Real Adapter Demo Package

Base: Phase 2.15 First Real Non-ML Adapter is frozen
(tag `phase-2.15-first-real-non-ml-adapter-freeze`).
No new module. No new CLI command. No new adapter architecture. This
phase adds documentation and tests that walk the existing Phase
2.10–2.15 commands end to end, using the Phase 2.15 *real* ffmpeg
`scdet` visual-change adapter instead of the Phase 2.13/2.14 static
fixture.

## Scope

This phase turns the real adapter built in Phase 2.15 into a clean,
copy-pasteable, user-facing demo: point the adapter at a real (tiny,
deterministic, test-generated) media file, generate candidate evidence,
dry-run it, import it, validate the package, and inspect the committed
track, manifest entry, and receipt — proving the first real adapter's
output flows through the same pipeline every fixture and hand-authored
event already flows through.

It adds no video/audio analysis logic of its own, no FFmpeg tracker
beyond the one Phase 2.15 already wraps, no OCR runtime, no object
detector, no ML model dependency, no semantic truth generation, no
dynamic plugin loading, no adapter discovery, no subprocess execution
(beyond invoking the existing CLULatent CLI and ffmpeg-generated test
media, both already this project's test style), no Studio UI, and no
CLUBIN. It adds **no new CLI command** — every step below uses a
command that already existed before this phase (`clulatent analysis
generate-ffmpeg-visual-change-adapter-result` from Phase 2.15,
`dry-run-adapter` from Phase 2.11, `import-adapter-result` from Phase
2.12, `validate`/`inspect` from Phase 1/2.8).

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

The Phase 2.15 adapter's events use deliberately hedged evidence
language ("ffmpeg visual-change candidate", "ffmpeg scene-score
candidate") and assert nothing about any real cut, shot, or semantic
scene boundary — only that ffmpeg's `scdet` filter's numeric frame-
difference score crossed the given threshold at a given timestamp.
This demo does not change that; it only proves the existing pipeline
carries that evidence end to end without weakening or reinterpreting
it. Importing it makes it *canonical* (validated, bounded, receipted,
reviewable, lockable), which is a statement about process, not about
reality.

## What this demo proves

- A real media file, read only by ffmpeg's `scdet` filter, can produce
  a Phase 2.10-valid `AdapterResult` (Phase 2.15).
- That result dry-runs cleanly against a real package without writing
  anything (Phase 2.11).
- That result imports cleanly into a real package through the one
  Phase 2.7 writer, producing a real `tracks/visual_change_events.jsonl`
  file, a real `manifest.json` `TrackDescriptor` entry, and a real
  `receipts/analyze.jsonl` line (Phase 2.12).
- The package independently re-validates afterward, from scratch,
  regardless of the fact that one of its tracks came from a real
  adapter run rather than ingest or a fixture (Phase 2.9).
- Every other file the ingest step produced — `sources/source.mp4`,
  `sources/source.sha256`, `tracks/keyframes.jsonl`,
  `tracks/audio_events.jsonl`, `tracks/speech_events.jsonl`,
  `tracks/semantic_events.jsonl`, `media/keyframes/*`,
  `receipts/ingest.jsonl` — is byte-for-byte unchanged by the import,
  and `index/search.sqlite` (derived, non-canonical) is also
  byte-for-byte unchanged, because nothing in this pipeline reindexes
  automatically.
- A locked package still refuses import before any lane is written,
  exactly as it does for the Phase 2.13/2.14 fixture.

## What this demo does not prove

- It does not prove ffmpeg's `scdet` score correlates with any
  semantically meaningful scene, shot, or cut — `scdet` is a numeric
  frame-difference heuristic, not a shot classifier, and this phase
  adds no evaluation of detection quality or accuracy.
- It does not prove the adapter works against arbitrary real-world
  video — the demo's test media is a tiny, synthetic, deterministic
  ffmpeg-generated clip (`lavfi` color-block sources), not a sample of
  real-world footage.
- It does not make the visual-change events "trusted" or "canonical
  truth" — imported evidence remains reviewable/rejectable/correctable
  exactly like every other canonical event (see below).
- It does not add any new adapter, lane, writer, validation rule, or
  CLI command — every command below already existed before this phase.

## The workflow

Each command below is copy-pasteable. `MEDIA` is a placeholder for a
real (or test-generated) media file; `PKG` is a placeholder for the
path to a real `.clulatent` package folder.

### 1. Create (or use) a package

A package is produced by ingesting any video (Phase 1):

```sh
clulatent ingest my_clip.mp4 -o demo.clulatent
PKG=demo.clulatent
```

### 2. Generate the real visual-change adapter result

```sh
clulatent analysis generate-ffmpeg-visual-change-adapter-result my_clip.mp4 -o visual_change_adapter_result.json
```

This reads `my_clip.mp4` through ffmpeg's `scdet` filter (Phase 2.15)
and writes a self-contained `AdapterResult` JSON document containing
zero or more `visual_change_events` candidates, each with a `[0,1]`
confidence derived from `scdet`'s 0-100 score. It touches no package.
Omit `-o` to print the JSON to stdout instead.

### 3. Dry-run the result against the package

```sh
clulatent analysis dry-run-adapter visual_change_adapter_result.json --package "$PKG"
```

Expected tail of output (exact candidate count depends on the source
media and `--threshold`):

```
  package status:  unlocked — write would be eligible
  would write:     True
PASS — adapter result is structurally valid (nothing written)
```

The dry-run writes nothing — `"$PKG/tracks"` is unchanged, no
`receipts/analyze.jsonl` entry is added, and no lock file is created.

### 4. Import the result into the package

```sh
clulatent analysis import-adapter-result "$PKG" visual_change_adapter_result.json
```

Expected tail of output:

```
  total events written: N
  receipt: .../demo.clulatent/receipts/analyze.jsonl
```

Import validates the result again (Phase 2.10's
`validate_adapter_result`), then writes the single non-empty
`visual_change_events` lane through the Phase 2.7 writer. Exit code
`0` on success — including when `N` is `0` (a flat/static clip
legitimately produces zero candidates, which is still a valid,
importable result with an empty lane simply skipped by the writer).

### 5. Validate the package

```sh
clulatent validate "$PKG"
```

Expected tail:

```
PASS — package is valid
```

### 6. Confirm the track, manifest entry, and receipt

```sh
clulatent inspect "$PKG"
cat "$PKG/tracks/visual_change_events.jsonl"
cat "$PKG/receipts/analyze.jsonl"
```

The `Tracks` table now includes `visual_change_events` alongside the
ingest tracks; `manifest.json`'s `tracks` list gained one
`TrackDescriptor` entry (`name: "visual_change_events"`, `file:
"tracks/visual_change_events.jsonl"`, `record_count` matching the
number of candidates imported); `receipts/analyze.jsonl` gained one
line with `"adapter_name": "clulatent-ffmpeg-visual-change-adapter"`.

## Why nothing else in the package changes

Only the Phase 2.7 writer, reached through Phase 2.12 import, ever
writes during this workflow, and it only ever touches
`tracks/visual_change_events.jsonl`, `manifest.json` (appending one
`TrackDescriptor`), and `receipts/analyze.jsonl` (appending one line).
Every ingest-produced file — the source media copy, its checksum, the
ingest-produced tracks, the keyframe images, the ingest receipt — is
untouched, and so is `index/search.sqlite`, which is derived and
non-canonical and is never rewritten by an adapter import. This is
verified byte-for-byte by `test_no_source_track_mutation` and
`test_no_index_mutation`.

## How locks still prevent import

Exactly as for the Phase 2.13/2.14 fixture: if the package carries a
valid integrity lock, import is refused before any lane is written —

```sh
clulatent analysis import-adapter-result "$PKG" visual_change_adapter_result.json
# -> Error: ... has a valid integrity lock ...  (exit 1, nothing written)
```

reached through the same unchanged Phase 2.7 `lock_status()` check
Phase 2.12 import already goes through. A dry-run against a locked
package still succeeds (it reports `would write: False`).

## How review_events fit afterward

Once the imported `visual_change_events` are canonical
`tracks/visual_change_events.jsonl` records, they are ordinary
evidence. A Phase 2.1 `review_approval`, `review_rejection`, or
`review_correction` event can target any of them by id, exactly as it
can target ingest-produced, fixture-produced, or hand-authored events.
`clulatent review-state "$PKG"` then reports each as reviewed/
approved/rejected/corrected. This is unchanged by Phase 2.16 — it is
the same demonstration Phase 2.14 already made for the fixture
adapter, now shown to hold for the first real adapter's output too.

## Why this is still not real intelligence

`scdet` is a low-level, purely numeric frame-difference filter with no
concept of "scene" in any semantic sense — no shot classification, no
object/person awareness, no cut-type judgment. Every event this demo
produces and imports is labeled a "visual-change candidate" or
"ffmpeg scene-score candidate," never a confirmed cut or semantic
scene boundary. This phase adds no model, no classifier, no learned
weights, and no semantic interpretation of any kind — it only proves
that whatever hedged, bounded evidence the Phase 2.15 adapter emits
reaches a real package safely, through the pipeline that was already
there.

## Why this is not ML

No model, weights, embedding, or learned inference of any kind is
introduced by this phase or by Phase 2.15's adapter. `scdet` is a
deterministic signal-processing filter shipped inside ffmpeg itself —
the same binary this codebase already depends on for every other
media operation.

## Why this is not semantic understanding

Nothing in this workflow classifies, labels, or interprets *what*
changed in the frame — only *that* a numeric frame-difference score
crossed a threshold at a given timestamp. No object, person, text, or
scene-type claim is made anywhere in the demo, the adapter, or the
imported events.

## Why this is not Studio UI

This phase adds documentation and tests — no graphical interface, no
persistent server, no interactive review surface. Every step is a
single stateless CLI invocation.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
The generated adapter-result JSON is a plain document in the same
shape every other adapter result document in this codebase already
uses.

## Safety properties (enforced by tests)

- Generation and dry-run mutate nothing in the package (no track, no
  receipt, no lock).
- Import touches only `tracks/visual_change_events.jsonl`,
  `manifest.json`, and `receipts/analyze.jsonl` — every other
  ingest-produced file, and the derived `index/search.sqlite`, is
  byte-for-byte unchanged.
- The workflow writes only inside explicit package/temp paths given on
  the command line.
- CLI output carries no unsafe terminal control sequences.
- No optional ML dependency (torch/whisper/silero) is required by any
  demo step or demo test.
- No event payload claims semantic truth or identity; every event's
  `evidence_label` contains the word "candidate."
- A locked package blocks import before any lane is written.

## Non-goals (explicit)

- No new CLI command, module, validation rule, writer, lock logic, or
  adapter — every step is an existing Phase 2.7–2.15 command or code
  path.
- No new media analysis logic — the demo uses the Phase 2.15 adapter
  exactly as implemented, unmodified.
- No claim about detection quality, accuracy, or real-world semantic
  meaning.
- No change to any frozen phase module or command.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.16 usage |
|---|---|---|
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | Reached indirectly via `write_adapter_result`; the sole writer of the track/manifest/receipt this demo creates |
| 2.9 | `validate_package` lane/receipt validation | Unmodified; still the authority on the committed visual-change track |
| 2.10 | `analysis_adapters.py` contracts + `write_adapter_result` | `validate_adapter_result`/`write_adapter_result` reused directly, unmodified |
| 2.11 | `analysis_adapter_dry_run.py` | `dry_run_adapter_result` reused directly to prove the real result dry-runs cleanly |
| 2.12 | `import-adapter-result` CLI command | Reused directly, unmodified, to prove the real result imports cleanly |
| 2.13/2.14 | Fixture adapter + demo workflow | Structural precedent this doc and test file mirror, applied to real (not fixture) evidence |
| 2.15 | `analysis_ffmpeg_visual_change_adapter.py` + CLI command | The adapter under demonstration, used exactly as implemented, unmodified |

## Files changed

- `tests/test_real_adapter_demo_package.py` (new)
- `docs/PHASE_2_16_REAL_ADAPTER_DEMO_PACKAGE.md` (new, this file)
- `README.md` (roadmap bullet)

## Summary

Phase 2.16 proves the Phase 2.15 real, non-ML ffmpeg `scdet`
visual-change adapter is usable end-to-end exactly like the Phase
2.13/2.14 fixture adapter was already shown to be: generate candidate
evidence from a real media file, prove it would be accepted (dry-run),
commit it (import), independently re-check the whole package
(validate), and confirm precisely what landed — one track, one
manifest entry, one receipt line — and precisely what did not change —
every other file in the package, byte-for-byte. It adds no new
command, no new adapter, and no new capability — only a clear, tested
path proving the first real adapter's evidence flows safely through
the pipeline that was already there. It still does not make CLULatent
understand real media.
