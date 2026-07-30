# Phase 2.19 — Adapter Pipeline Release Review

Base: Phase 2.18 Real Adapter Demo Script is frozen
(tag `phase-2.18-real-adapter-demo-script-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase is a conservative release/readiness review of
the adapter pipeline built across Phases 2.10-2.18 — documentation and
tests only, plus one small fix if a clear doc/test/safety bug is found
along the way.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

## 1. Pipeline capability — what now works end-to-end

Every step below is a real, tested, currently-working command or
function call. None of it is aspirational.

| Step | Command / function | Phase |
|---|---|---|
| Generate adapter-result JSON (fixture) | `clulatent analysis generate-fixture-adapter-result` | 2.13 |
| Generate adapter-result JSON (real, non-ML) | `clulatent analysis generate-ffmpeg-visual-change-adapter-result` | 2.15 / 2.17 |
| Validate adapter-result JSON (shape/bounds/path-safety/identity) | `analysis_adapters.validate_adapter_result` (library); `clulatent analysis validate-file` | 2.10 |
| Dry-run adapter-result JSON against a package (writes nothing) | `clulatent analysis dry-run-adapter` | 2.11 |
| Import adapter-result JSON into a package | `clulatent analysis import-adapter-result` | 2.12 |
| Write validated analysis lanes | `analysis_writer.append_analysis_events` (reached through the Phase 2.10 writer bridge, `write_adapter_result`) | 2.7 / 2.10 |
| Write receipts | `receipts/analyze.jsonl`, appended by the same Phase 2.7 writer | 2.7 |
| Update manifest | `manifest.json` `TrackDescriptor` entries, updated by the same Phase 2.7 writer | 2.7 |
| Validate package after import | `clulatent validate` | 1 / 2.9 |
| Run a reproducible end-to-end demo | `python scripts/demo_real_adapter_workflow.py` | 2.18 |

Two adapters exist today, and both are intentionally non-ML:

- **Fixture adapter** (`analysis_adapter_fixture.py`, Phase 2.13) —
  fixed, hedged, deterministic example events (`synthetic scene
  boundary`, `fixture visual change`, `impact-like transient`,
  `cross-lane fixture link`). Proves the pipeline shape without
  claiming anything about real media.
- **Real ffmpeg visual-change adapter**
  (`analysis_ffmpeg_visual_change_adapter.py`, Phase 2.15) — wraps
  ffmpeg's `scdet` scene-change filter against a real media file to
  produce candidate `visual_change_events`. This is the first (and
  still only) adapter whose evidence is derived from actually reading
  media, not fixed fixture data. It is still non-ML: `scdet` is a
  classical signal-processing filter shipped inside ffmpeg itself, not
  a model.

Both adapters, and any future one, are reached the same way: build an
`AdapterResult` (Phase 2.10 dataclasses), validate it, optionally
dry-run it, then import it through the one Phase 2.7 writer. No
adapter ever writes a track, receipt, or manifest entry directly.

The full path is demonstrated, non-interactively and reproducibly, by
`scripts/demo_real_adapter_workflow.py` (Phase 2.18), which chains
ingest -> generate -> dry-run -> import -> validate against a tiny
synthetic local video and prints a safe summary.

## 2. Trust model

- **Adapter output is evidence, not truth.** Every event an adapter
  produces is a candidate — a "visual-change candidate", a "fixture
  scene boundary" — never asserted as a confirmed fact about the media.
  This wording is enforced by the adapters themselves
  (`payload.evidence_label`, hedged `payload.description`,
  `metadata.warnings`) and checked by tests, not just documented.
- **Adapter output is not semantic understanding.** No adapter in this
  codebase interprets *what* is in a frame or audio segment — only
  low-level, mechanical signal properties (a scene-change score
  threshold, a fixed fixture value). There is no object recognition, no
  OCR, no captioning, no embeddings.
- **Adapter output does not bypass validation.** Every path into a
  package — CLI or library — routes through
  `analysis_adapters.validate_adapter_result`, which itself reuses
  `analysis_lanes.validate_analysis_track` (Phase 2.6) for every lane.
  There is no adapter-specific fast path that skips this.
  `write_adapter_result` (the only writer bridge) validates before it
  ever calls the Phase 2.7 writer, and refuses (raises
  `AnalysisAdapterError`) rather than partially write on any error.
- **Imported lanes are canonical only after validation, writing, a
  receipt, and package validation all succeed.** "Canonical" in this
  project never means "true" — it means the four concrete properties
  in the core principle above: validated (schema/bounds/path-safety
  checked), bounded (size/count limits enforced), receipted (an
  `analyze.jsonl` entry exists), and reviewable (see below). Nothing
  is canonical just because an adapter produced it; nothing is
  canonical just because a file with the right shape was placed on
  disk by hand either — it must go through the writer.
- **`review_events` can later approve, reject, correct, or override
  evidence** (Phase 2.0-2.1, unchanged by this pipeline). Imported
  adapter events are ordinary source events like any other track's
  events; `clulatent review approve|reject|correct|override|note`
  writes additive `tracks/review_events.jsonl` entries against them,
  and `clulatent review-state` / `review-status` summarize the
  resulting reviewed state. No adapter or import step marks anything
  as reviewed — review is always a separate, explicit, human-driven
  step.
- **Locks prevent mutation of locked packages.** `clulatent lock`
  records SHA-256 digests of every canonical file; a package with a
  lock present is checked before any write. Import/append operations
  use `operation_lock` for the duration of the write, and existing
  package-level lock state is respected the same way it is for every
  other write command in this project — the adapter pipeline adds no
  exception here.

## 3. Safety boundaries

Reviewed against the actual code, not just intent:

- **No plugin discovery.** `analysis_adapters.py`'s `AnalysisAdapter`
  `Protocol` is a typing-only structural contract. Nothing in this
  codebase imports, calls, discovers, or registers a concrete adapter
  against it. Every adapter used today (`analysis_adapter_fixture.py`,
  `analysis_ffmpeg_visual_change_adapter.py`) is imported by name,
  statically, wherever it is used (`cli.py`,
  `scripts/demo_real_adapter_workflow.py`) — there is no registry, no
  dynamic `importlib` adapter lookup, no adapter directory scan.
- **No dynamic code execution.** No `eval`, `exec`, dynamic import of
  user-supplied code, or deserialization of executable objects exists
  anywhere in the adapter pipeline. Adapter-result JSON is parsed with
  `json.loads` through `security.jsonl`'s bounded readers, never
  `pickle` or similar.
- **No subprocess execution except the existing, tightly scoped
  ffmpeg use the real adapter already requires.** The only real-content
  generator (`generate-ffmpeg-visual-change-adapter-result` / the demo
  script's synthetic-media step) resolves the `ffmpeg` binary through
  `security.subprocess.resolve_tool` and runs it through `run_tool` —
  never `shell=True`, always argument-list `exec`, bounded
  stdout/stderr capture, and a hard timeout
  (`DEFAULT_LIMITS.ffmpeg_timeout_s`, 900s) with process-group kill on
  timeout. No other adapter or pipeline step spawns a subprocess.
- **No ML dependency.** Neither adapter, nor the dry-run harness, nor
  the import/writer bridge, nor the demo script imports torch,
  transformers, or any model-serving library. (The project's separate
  Whisper/VAD speech pipeline is unrelated code, not part of this
  adapter pipeline, and is not touched by this review.)
- **No identity claims.** `validate_adapter_result` rejects any
  `producer.name` that claims to be `human:*` from an adapter result —
  adapters can only ever identify themselves as machine producers, and
  review events (the only path that can attribute something to a
  human) are a separate, explicit write path (Phase 2.0-2.1).
- **No source-track mutation.** The Phase 2.7 writer is append-only
  per lane (`tracks/*.jsonl`); import never rewrites, reorders, or
  deletes an existing line in any track file, and never modifies the
  embedded source media under `sources/`.
- **No index mutation outside expected package-local behavior.**
  `index/search.sqlite` is documented and treated everywhere as a
  disposable, rebuildable cache (`clulatent reindex`); the adapter
  import path does not touch it directly, and no test asserts the
  import path is required to update it.
- **No writes outside explicit output/package paths.** Every generator
  writes only to the `-o`/`--output` path (or stdout) it is given;
  every import/dry-run only touches the one `--package` path given;
  `scripts/demo_real_adapter_workflow.py` confines every file it
  creates to its `--output-dir` argument (verified by dedicated Phase
  2.18 tests: `test_script_does_not_write_outside_output_dir`,
  `test_script_does_not_mutate_repo_files`).
- **No unsafe console echoing.** Every dynamic or path-derived value
  printed by the CLI or the demo script is passed through
  `security.console.safe_console_text`, which strips raw ANSI/control
  characters and escapes Rich markup before printing — checked by
  `test_..._has_no_control_sequences` tests across Phase 2.14, 2.16,
  2.17, and 2.18.
- **No raw tracebacks for normal user errors.** Every CLI command in
  this pipeline (`dry-run-adapter`, `import-adapter-result`,
  `generate-fixture-adapter-result`,
  `generate-ffmpeg-visual-change-adapter-result`) and the demo script
  wrap expected failure modes (missing file, invalid JSON, locked
  package, missing ffmpeg, existing output path, unwritable output
  directory) in a clean `Error: ...` message on stderr with a nonzero
  exit code — verified by `"Traceback" not in output` assertions
  present throughout `tests/test_analysis_adapter_dry_run.py`,
  `tests/test_analysis_adapter_result_import.py`,
  `tests/test_real_adapter_cli_polish.py`, and
  `tests/test_real_adapter_demo_script.py`.

## 4. User-facing workflow

Every command below is real, copy-pasteable, and already covered by an
existing test. No command is invented for this document.

### Fastest path: the reproducible demo script (Phase 2.18)

```sh
python scripts/demo_real_adapter_workflow.py
```

Runs the entire pipeline below in one step, inside a temp directory,
against a tiny synthetic local video, and prints a safe summary.
Optional flags: `--output-dir DIR`, `--media PATH`, `--threshold N`,
`--print-json` (see `docs/PHASE_2_18_REAL_ADAPTER_DEMO_SCRIPT.md`).

### Manual path, step by step

```sh
# 1. Create a package (Phase 1)
clulatent ingest sample.mp4 -o sample.clulatent

# 2. Generate real adapter-result JSON (Phase 2.15 / 2.17)
clulatent analysis generate-ffmpeg-visual-change-adapter-result sample.mp4 -o visual_change.json

#    (or, for a media-free fixture walkthrough, Phase 2.13:)
clulatent analysis generate-fixture-adapter-result -o fixture_adapter_result.json

# 3. Dry-run it against the package (Phase 2.11) — writes nothing
clulatent analysis dry-run-adapter visual_change.json --package sample.clulatent

# 4. Import it into the package (Phase 2.12)
clulatent analysis import-adapter-result sample.clulatent visual_change.json

# 5. Validate the package (Phase 1 / 2.9)
clulatent validate sample.clulatent

# 6. Inspect resulting tracks/receipts
clulatent inspect sample.clulatent      # Tracks table now includes the new lane
clulatent timeline sample.clulatent     # chronological view including the new events
clulatent review-state sample.clulatent # reviewed/unreviewed summary (Phase 2.1)
```

`receipts/analyze.jsonl` and `tracks/visual_change_events.jsonl` (or
`tracks/*` for the fixture lanes) can be read directly — they are
plain JSONL, the source of truth, not a derived cache.

## 5. Limitations (intentionally not implemented)

- **Not a general plugin system.** There is no adapter registry, no
  install-time adapter discovery, no config file naming a third-party
  adapter to load.
- **Not a scheduler/runtime.** There is no daemon, queue, or
    long-running process that runs adapters automatically; every
    invocation is a single, explicit, one-shot command.
- **Not multi-adapter orchestration.** Nothing in this pipeline chains
  multiple adapters together, resolves conflicts between adapters, or
  merges/deduplicates evidence across adapters. Each adapter result is
  generated, validated, and imported independently.
- **Not visual understanding.** `scdet` measures a numeric
  scene-change score; it does not know what changed or why.
- **Not object recognition.** No detector, classifier, or bounding-box
  model exists anywhere in this pipeline.
- **Not OCR.** No text-in-image extraction exists anywhere in this
  pipeline.
- **Not ML.** Confirmed in Section 3 — no model weights, no inference
  runtime, anywhere in the adapter pipeline.
- **Not Studio UI.** Every interaction is a terminal command
  (`clulatent ...` or `python scripts/...`); there is no graphical
  interface, no persistent server, no interactive review surface.
- **Not CLUBIN.** No binary, packaging, or distribution format is
  introduced; every artifact is a plain `.clulatent` directory or a
  plain JSON file.
- **Not a public release yet, unless the project owner decides
  otherwise.** This review documents current, tested capability — it
  is not itself an announcement or a change in distribution status.

## 6. What can be demonstrated today

- Generating real, ffmpeg-derived candidate evidence from an actual
  video file (not a fixed fixture) and importing it end-to-end into a
  validated, receipted package, entirely offline.
- The complete evidence lifecycle for that evidence: generate -> (self)
  validate -> dry-run -> import -> package-validate -> inspect ->
  (optionally) review approve/reject/correct.
- The same path with zero real media, using the Phase 2.13 fixture
  adapter, for environments without ffmpeg or for pure-pipeline
  demonstration.
- A single, reproducible, non-destructive script
  (`scripts/demo_real_adapter_workflow.py`) that runs the real-media
  path unattended and prints a clear, honest, evidence-not-truth
  summary — suitable for a first-look demo to a new user or reviewer.
- Locking a package after import and verifying no drift
  (`clulatent lock` / `verify-lock` / `lock-status`), proving the
  imported evidence is durable and tamper-evident going forward.

## 7. What should be considered pre-alpha / unstable

- **Adapter count and coverage.** Only one real (non-ML) adapter
  exists. The pipeline shape is proven; broader adapter coverage
  (audio, more visual signal types) is unproven at scale.
- **No adapter versioning/compatibility policy yet.** `adapter_version`
  is recorded (folded into the writer's `environment` dict) but
  nothing yet enforces or checks compatibility between adapter
  versions and package/schema versions over time.
- **No performance/scale testing.** Every test and demo in this
  pipeline runs against tiny (one-to-two-second) synthetic clips.
  Behavior against long-form or high-resolution real media is
  untested by this codebase's test suite.
- **No concurrent-adapter-import stress testing.** Operation locks are
  reused unchanged from Phase 1.7.5, but no test in this pipeline
  specifically exercises many adapter imports racing against the same
  package.
- **Speech/VAD pipeline instability is unrelated but adjacent.** The
  8 pre-existing, unrelated test failures present in this environment
  (`torchaudio`/`torchcodec` version mismatch, in `test_vad.py` /
  `test_transcribe.py` / `test_ingest_phase17.py`) are outside this
  review's scope (they are not part of the adapter pipeline) but are
  worth the project owner's attention before any release, since they
  indicate an unpinned/fragile optional dependency elsewhere in the
  same package.

## 8. Release recommendation: GO WITH CAVEATS

The adapter pipeline (Phase 2.10-2.18) is internally consistent,
conservatively scoped, and does what its own documentation claims: it
generates, validates, dry-runs, imports, receipts, and package-
validates real (non-ML) and fixture adapter evidence, with no adapter
ever bypassing the single Phase 2.7 writer, no unsafe subprocess or
console behavior found, and a working, tested, reproducible one-command
demo. No blocking safety or correctness issue was found during this
review.

Caveats:

1. **Adapter coverage is minimal (one real adapter).** Treat this
   pipeline as validated *infrastructure*, not a broad perception
   capability — do not market or demo it as "CLULatent understands
   video."
2. **No adapter-version compatibility policy yet.** Fine for current
   single-adapter use; should be addressed before multiple adapter
   versions are expected to coexist against the same package format
   version.
3. **No large/long-form media testing.** All current tests and demos
   use tiny synthetic clips. Validate real-world file sizes/durations
   before any performance claim.
4. **Unrelated `torchaudio`/`torchcodec` failures exist in this
   environment.** Not part of the adapter pipeline, but should be
   resolved (pin or fix the dependency) before a broader release, since
   they currently fail `pytest` for unrelated features in the same
   package.
5. **This pipeline has not been used against real-world untrusted
   third-party adapter output** — only against this project's own two
   adapters. The validation path is designed for untrusted input
   (bounded parsing, path containment, no identity claims), but has
   only been tested against this codebase's own generators.

None of these caveats are safety regressions or correctness bugs; they
are scope and maturity notes appropriate to a pre-alpha pipeline.

## 9. Recommended next phase

Given the caveats above, the most valuable next phase is **not** a new
adapter or new intelligence. In priority order:

1. A **second real adapter** in a different signal family (e.g. an
   audio-domain, still-non-ML adapter) to prove the pipeline
   generalizes beyond one visual-change detector, before any broader
   claim about "the adapter pipeline" is made.
2. **Adapter-version / compatibility documentation** (not new code
   necessarily) — a short policy for what happens when an adapter's
   `adapter_version` changes against an already-imported package.
3. Separately (outside this pipeline's scope): resolve the
   `torchaudio`/`torchcodec` dependency mismatch so the full test
   suite is green in a stock environment.

## Phases reviewed

| Phase | Title | What it contributed |
|---|---|---|
| Phase 2.10 | Analysis Adapter Contracts | `AdapterResult`/`AdapterMetadata` dataclasses, `validate_adapter_result`, `write_adapter_result` (the writer bridge) |
| Phase 2.11 | Analysis Adapter Dry-Run Harness | `dry_run_adapter_result` — checks eligibility to import without writing anything |
| Phase 2.12 | Analysis Adapter Result Import | `clulatent analysis import-adapter-result` CLI command |
| Phase 2.13 | First Non-ML Adapter Fixture | The fixture adapter, `clulatent analysis generate-fixture-adapter-result` |
| Phase 2.15 | First Real Non-ML Adapter | The real ffmpeg `scdet` visual-change adapter |
| Phase 2.16 | Real Adapter Demo Package | Doc + tests proving the real adapter's generate -> dry-run -> import -> validate path via the CLI |
| Phase 2.17 | Real Adapter CLI Polish | Richer `--output`-mode summary, one raw-traceback fix, for the Phase 2.15 CLI command |
| Phase 2.18 | Real Adapter Demo Script | `scripts/demo_real_adapter_workflow.py`, a single reproducible one-command demo |

(Phase 2.14, Analysis Adapter Demo Workflow, is earlier pipeline
history — the fixture-based demo doc/example predating Phase 2.16's
real-adapter version — and is referenced above in Section 3's test
list, but was not in this phase's required review scope.)

## Non-goals (explicit, unchanged from every phase reviewed)

- No new adapter architecture, no new real adapters.
- No ML model dependency.
- No semantic truth generation.
- No dynamic plugin loading, no adapter discovery.
- No Studio UI, no CLUBIN.
- No broad refactors.
- No behavior changes beyond what Section 10 below documents (none
  were needed).

## 10. Changes made during this review

None. This review found no safety, correctness, or documentation bug
in the reviewed phases (2.10-2.18) that warranted a code change. Every
finding above is a scope/maturity caveat, not a defect.

## Files changed

- `docs/PHASE_2_19_ADAPTER_PIPELINE_RELEASE_REVIEW.md` (new, this file)
- `tests/test_phase_2_19_release_review.py` (new)
- `README.md` (roadmap bullet)

## Summary

Phase 2.19 reviews, rather than extends, the adapter pipeline built
across Phases 2.10-2.18. It finds the pipeline internally consistent
with its own stated principle — adapters produce evidence, not truth —
at every layer: generation, validation, dry-run, import, writing,
receipting, and package validation. It recommends **GO WITH CAVEATS**:
safe to demonstrate and build on today, with explicit, documented scope
limits (one real adapter, no version-compatibility policy yet, no
large-media testing, one unrelated dependency issue in this
environment) rather than any found defect. It does not add adapters,
intelligence, or new capability — it only documents and lightly tests
what already exists.
