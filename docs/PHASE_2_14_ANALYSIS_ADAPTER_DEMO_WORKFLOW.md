# Phase 2.14 — Analysis Adapter Demo Workflow

Base: Phase 2.13 First Non-ML Adapter Fixture is frozen
(tag `phase-2.13-first-non-ml-adapter-fixture-freeze`).
No new module. No new CLI command. This phase adds documentation, a
tracked deterministic example JSON artifact, and tests that walk the
existing Phase 2.6–2.13 commands end to end.

## Scope

This phase turns the adapter runway built across Phase 2.6–2.13 into a
single, copy-pasteable demo workflow: generate a fixture adapter
result, dry-run it, import it, validate the package, and inspect the
committed tracks and receipt — all without analyzing any real media.

It implements no video/audio analysis, no FFmpeg tracker, no OCR
runtime, no object detector, no ML model dependency, no semantic truth
generation, no dynamic plugin loading, no adapter discovery, no
subprocess execution (beyond invoking the existing CLULatent CLI in
tests, which is already this project's test style), no Studio UI, and
no CLUBIN. It adds **no new CLI command** — every step below uses a
command that already existed before this phase.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

The demo below deliberately uses the Phase 2.13 *fixture* adapter,
whose events use hedged evidence language ("synthetic scene boundary",
"fixture visual change", "impact-like transient", "cross-lane fixture
link") and assert nothing about any real video or audio. Everything
this workflow commits is example evidence, not truth — importing it
makes it *canonical* (validated, bounded, receipted, reviewable,
lockable), which is a statement about process, not about reality.

## The tracked example

`examples/fixture_adapter_result.json` is a small, deterministic,
committed copy of exactly what
`clulatent analysis generate-fixture-adapter-result` produces. It is
byte-for-byte equal to `fixture_adapter_result_json() + "\n"` and a
test (`test_committed_example_matches_generator`) fails if the two
ever drift, so the file can never silently go stale. You can use it
directly in any of the steps below in place of regenerating it.

## The workflow

Each command below is copy-pasteable. `PKG` is a placeholder for the
path to a real `.clulatent` package folder.

### 1. Create (or use) a minimal valid package

A package is produced by ingesting any video (Phase 1). For the demo
any tiny clip works:

```sh
clulatent ingest my_clip.mp4 -o demo.clulatent
PKG=demo.clulatent
```

`clulatent ingest` is the only step here that reads real media, and it
is pre-existing Phase 1 behaviour — the adapter workflow itself
(steps 2–7) never touches the source video again.

### 2. Generate the fixture adapter result JSON

```sh
clulatent analysis generate-fixture-adapter-result -o fixture_adapter_result.json
```

This writes a self-contained JSON document (identical to the tracked
`examples/fixture_adapter_result.json`). It reads no media, runs no
subprocess, and touches no package. Omit `-o` to print the JSON to
stdout instead (safe to pipe — it is plain JSON with no terminal
control sequences).

### 3. Dry-run the result against the package

```sh
clulatent analysis dry-run-adapter fixture_adapter_result.json --package "$PKG"
```

Expected tail of output:

```
  package status:  unlocked — write would be eligible
  would write:     True
PASS — adapter result is structurally valid (nothing written)
```

**The dry-run writes nothing.** After it runs, `"$PKG/tracks"` still
contains only the tracks ingest created (e.g. `keyframes.jsonl`,
`audio_events.jsonl`, `speech_events.jsonl`, `semantic_events.jsonl`)
— no `scene_events.jsonl`, no `receipts/analyze.jsonl` entry, no lock
file. This is verified by `test_dry_run_does_not_mutate_package`.

### 4. Import the result into the package

```sh
clulatent analysis import-adapter-result "$PKG" fixture_adapter_result.json
```

Expected tail of output:

```
  total events written: 4
  receipt: .../demo.clulatent/receipts/analyze.jsonl
```

Import validates the whole result again (the same Phase 2.10
`validate_adapter_result`), then writes each of the four non-empty
lanes through the one Phase 2.7 writer. Exit code `0` on success.

### 5. Validate the package

```sh
clulatent validate "$PKG"
```

Expected tail:

```
PASS — package is valid
```

`clulatent validate` re-checks every track and receipt from scratch,
regardless of who wrote them — the freshly imported fixture tracks are
indistinguishable to it from ingest-produced or hand-authored ones.

### 6. Confirm the analysis tracks exist

```sh
clulatent inspect "$PKG"
```

The `Tracks` table now includes the four fixture lanes alongside the
ingest tracks:

```
│ audio_transient_events │       1 │ tracks/audio_transient_events.jsonl │
│ cross_lane_link_events │       1 │ tracks/cross_lane_link_events.jsonl │
│ scene_events           │       1 │ tracks/scene_events.jsonl           │
│ visual_change_events   │       1 │ tracks/visual_change_events.jsonl   │
```

The raw files are readable directly, one JSON event per line:

```sh
cat "$PKG/tracks/scene_events.jsonl"
```

### 7. Confirm the analyze receipt exists

```sh
cat "$PKG/receipts/analyze.jsonl"
```

Each line is one lane write's receipt; every fixture line carries
`"adapter_name": "clulatent-fixture-adapter"`.

## What is created, and what is not

| After step | tracks/`<fixture lane>`.jsonl | receipts/analyze.jsonl | lock file |
|---|---|---|---|
| 2 (generate) | — | — | — |
| 3 (dry-run) | — | — | — |
| 4 (import) | created (4 lanes) | created/appended | — |

Generation and dry-run create **nothing** inside the package — no
track, no receipt, no lock. Only import (step 4) changes the package,
and only through the Phase 2.7 writer.

## Repeated import (idempotency is *not* claimed)

Running step 4 a second time on the same package is **refused**, with
a clean error and a nonzero exit code, because every fixture event id
already exists on disk:

```
Error: refusing to write: candidate audio_transient_events events failed
validation: audio_transient_events[0]: event id 'fixture_audio_000'
already exists in the audio_transient_events track
```

The writer refuses at the first lane it tries (lanes are processed in
the loaded JSON's key order), so on a fully-duplicate re-import nothing
new is written. This is the same duplicate-id protection every Phase
2.7 write already has; the demo does not add or weaken it. If you want
to import a *second, distinct* batch of evidence, produce a result
whose event ids do not collide with ids already committed. This
behaviour is tested by `test_repeated_import_is_refused_on_duplicate_ids`.

## How locks prevent import

If the package carries a valid integrity lock, import is refused before
any lane is written:

```sh
# (a lock is normally created by `clulatent` lock tooling / verify-lock)
clulatent analysis import-adapter-result "$PKG" fixture_adapter_result.json
# -> Error: ... has a valid integrity lock ...  (exit 1, nothing written)
```

The refusal comes from the Phase 2.7 writer's `lock_status()` check,
reached unchanged through `write_adapter_result`. A dry-run against a
locked package still exits `0` (it reports `would write: False` and a
`locked` package status) — "is this result valid" is orthogonal to "is
this package writable right now." This is tested by
`test_locked_package_blocks_import`.

## How review_events fit afterward

Once the fixture events are committed as canonical
`tracks/<lane>.jsonl` records, they are ordinary evidence. A Phase 2.1
`review_approval`, `review_rejection`, or `review_correction` event
(written additively to `tracks/review_events.jsonl`) can target any of
them by id, exactly as it can target ingest-produced or hand-authored
events. `clulatent review-state "$PKG"` then reports each as
reviewed/approved/rejected/corrected. Nothing about a fixture event
being fixture data changes how review works — reviewing evidence is
the same operation regardless of the evidence's origin, which is part
of what this demo shows: the pipeline treats all canonical evidence
uniformly.

## Why this is still not real media analysis

The only command that reads the source video is `clulatent ingest`
(step 1), which is pre-existing Phase 1 behaviour and produces no
adapter evidence. Steps 2–7 never open the video or audio again — the
four committed events are fixed constants from the Phase 2.13 fixture,
not measurements of anything. No FFmpeg tracker, OCR runtime, object
detector, or ML model runs at any point in this workflow.

## Why fixture evidence is not truth

Every committed event is hedged example data ("synthetic", "fixture",
"impact-like", "cross-lane fixture link") and its
`metadata.warnings`/`warnings` explicitly flag it as
fixture/example evidence, not a real analysis result. Importing it
makes it *canonical* — validated, bounded, receipted, reviewable,
lockable — which is a claim about how the data was handled, never a
claim that the data is true about any real scene, image, or sound.

## How this proves the adapter pipeline

The workflow exercises every seam between the phases with real
commands and a real package: Phase 2.13 generates → Phase 2.11 dry-run
validates → Phase 2.12 import commits through the Phase 2.10 writer
bridge and the Phase 2.7 writer → Phase 2.9 `validate` independently
re-checks → Phase 2.8 `inspect`/`timeline` and the raw JSONL files
expose the result → Phase 2.1 review can act on it. If any seam
regressed, a demo test would fail.

## Why this is not adapter runtime

No adapter is executed, discovered, or loaded. The fixture result is
static JSON; the CLI commands read and write files but never invoke an
`AnalysisAdapter.run(...)`, load a plugin, or discover an installed
adapter. This phase adds a *walkthrough* of the existing static
pipeline, not any runtime that produces evidence from media.

## Why this is not Studio UI

This phase adds documentation, one tracked JSON example, and tests —
no graphical interface, no persistent server, no interactive review
surface. Every step is a single stateless CLI invocation.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
`examples/fixture_adapter_result.json` is a plain JSON document in the
same adapter-result shape the codebase already uses — not a new file
format or a data model backing any external tool.

## Safety properties (enforced by tests)

- Generation and dry-run mutate nothing in the package (no track, no
  receipt, no lock).
- The workflow writes only inside explicit package/temp paths given on
  the command line.
- CLI output carries no unsafe terminal control sequences (the
  generated JSON is emitted with markup disabled).
- No optional ML dependency (torch/whisper/silero) is required by any
  demo step or demo test.
- No event payload claims semantic truth; identity-claim fields are
  absent (inherited from the Phase 2.13 fixture, re-asserted here).

## Non-goals (explicit)

- No new CLI command, module, validation rule, writer, or lock logic —
  every step is an existing Phase 2.6–2.13 command.
- No real media analysis, external-tool call, or subprocess beyond
  invoking the existing CLI in tests.
- No idempotency claim for repeated import — a duplicate re-import is
  refused, and that refusal is documented and tested.
- No change to any frozen phase module or command.
- No Studio UI or CLUBIN.

## Files changed

- `examples/fixture_adapter_result.json` (new, tracked, deterministic)
- `tests/test_analysis_adapter_demo_workflow.py` (new)
- `docs/PHASE_2_14_ANALYSIS_ADAPTER_DEMO_WORKFLOW.md` (new, this file)
- `README.md` (roadmap bullet + demo-workflow quickstart pointer)

## Summary

Phase 2.14 packages the Phase 2.6–2.13 adapter runway into one honest,
copy-pasteable demo: generate fixture evidence, prove it would be
accepted (dry-run), commit it (import), independently re-check it
(validate), and look at what landed (inspect + raw JSONL + receipt),
with locked-package refusal and duplicate-re-import refusal both
documented and tested. It adds no new command and no new capability —
only a clear, tested path through what was already there. It still
does not make CLULatent analyze real media.
