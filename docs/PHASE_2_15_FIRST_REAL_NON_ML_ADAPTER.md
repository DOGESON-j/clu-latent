# Phase 2.15 — First Real Non-ML Adapter

Base: Phase 2.14 Analysis Adapter Demo Workflow is frozen
(tag `phase-2.14-analysis-adapter-demo-workflow-freeze`).
New module: `src/clu_latent/analysis_ffmpeg_visual_change_adapter.py`.
New CLI command: `clulatent analysis generate-ffmpeg-visual-change-adapter-result`.

## Scope

This phase adds the first **real** analysis adapter in this codebase —
one whose output is derived from actually reading a real media file,
rather than fixed fixture data (Phase 2.13) or an unimplemented
protocol (Phase 2.10). It wraps FFmpeg's built-in `scdet`
(scene-change detection) video filter to produce candidate
`visual_change_events`.

It implements no ML model, no OCR runtime, no object detector, no
semantic scene understanding, no dynamic plugin loading, no adapter
discovery, no shell execution, no Studio UI, and no CLUBIN. FFmpeg is
invoked exactly as every other ffmpeg call in this codebase already is
— through `security.subprocess.run_tool` (no `shell=True`, a hard
timeout, bounded stdout/stderr capture) — and nothing else.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

A real ffmpeg scene-score signal is still not a semantic scene
detector, still not an ML model, and still not confirmed truth about
any real scene change. Every event this adapter produces uses
deliberately hedged language ("visual-change candidate", "ffmpeg
scene-score candidate", "frame difference candidate") and never claims
certainty ("scene definitely changed", "cut detected with certainty",
"semantic scene" are all forbidden phrasing, and none of this module's
strings use them).

## What the adapter does

`analysis_ffmpeg_visual_change_adapter.py` defines:

- `build_visual_change_adapter_result(media_path, *, threshold=10.0,
  max_events=200, limits=DEFAULT_LIMITS) -> AdapterResult` — runs
  ffmpeg's `scdet` filter over `media_path` and returns an
  `AdapterResult` with one lane, `visual_change_events`.
- `visual_change_adapter_result_to_dict(result) -> dict` — serializes
  an `AdapterResult` into the plain JSON dict shape Phase 2.11's loader
  and Phase 2.12's import command both expect.
- `visual_change_adapter_result_json(media_path, ...) -> str` — the
  same, as a JSON string.
- `parse_scdet_log(stderr_text) -> list[tuple[int, float]]` — a small,
  independently testable regex parser that turns ffmpeg's `scdet`
  stderr lines (`lavfi.scd.score: 15.625, lavfi.scd.time: 1`) into
  `(t_ms, score)` pairs.

`threshold` (ffmpeg's own `scdet` scale, `0`-`100`, default `10`,
matching ffmpeg's own default) and `max_events` (`1`-`2000`, default
`200`) are validated up front; out-of-range or wrong-typed values raise
`VisualChangeAdapterError` before any subprocess runs.

### How ffmpeg is invoked

```
ffmpeg -hide_banner -nostdin -protocol_whitelist file,pipe \
  -i <media_path> -vf scdet=threshold=<threshold> -f null -
```

via `security.subprocess.run_tool` — the same hardened helper (no
`shell=True`, `stdin=DEVNULL`, bounded stdout/stderr capture, a hard
timeout, process-group kill on timeout/overflow) every other ffmpeg
call in this codebase already uses. `resolve_tool("ffmpeg")` is used
to locate the binary; a missing ffmpeg raises `ToolNotFoundError`,
which this module catches and re-raises as a clean
`VisualChangeAdapterError` — never an unhandled traceback.

### Score-to-confidence mapping

`scdet` scores are on ffmpeg's own `0`-`100` scale, but Phase 2.6
requires an event's `confidence` field (when present) to be in
`[0.0, 1.0]`. This module derives (does not measure) a bounded
confidence via a simple linear clamp-and-scale:
`confidence = max(0.0, min(1.0, score / 100.0))`. This is a
convenience conversion for downstream tooling that expects a
`[0, 1]` confidence, not a separately measured probability — the raw
`score` is preserved unmodified in the event payload alongside it.

### Event shape

Each detected candidate becomes one `visual_change` event:

```json
{
  "id": "ffmpeg_visual_change_0000",
  "type": "visual_change",
  "t_start_ms": 1000,
  "t_end_ms": 1001,
  "producer": {"name": "adapter:clulatent-ffmpeg-visual-change-adapter", "version": "1.0.0"},
  "confidence": 0.15625,
  "payload": {
    "signal": "ffmpeg_scene_score",
    "score": 15.625,
    "threshold": 10.0,
    "evidence_label": "ffmpeg visual-change candidate",
    "description": "ffmpeg scene-score candidate"
  }
}
```

Timestamps are integer milliseconds derived from ffmpeg's own
`lavfi.scd.time` seconds value (`round(time_s * 1000)`). Event ids are
deterministic and stable for a given detection run
(`ffmpeg_visual_change_<index>`, zero-padded, in detection order — not
random, not clock-derived). Every event uses a tiny, fixed
`t_end_ms = t_start_ms + 1` (a 1ms point-like duration) rather than
relying on the edge case where `t_end_ms == t_start_ms` is technically
also valid, to make each event's shape unambiguous. No event payload
contains an absolute path, a parent-traversal segment, or any of the
codebase's `FORBIDDEN_IDENTITY_FIELDS`/`IDENTITY_FLAG_FIELDS`.

### Metadata and safe input-source handling

`AdapterMetadata.input_sources` is validated by
`security.paths.validate_relative_posix`, which rejects absolute
paths — and this adapter has no package root to make a source path
relative to, since it runs directly against a media file outside any
`.clulatent` package. Rather than leak the real (often absolute)
filesystem path into a field that is supposed to be a safe relative
label, this module uses a fixed constant, `"external-media/input"`, as
the sole `input_sources` entry. The real filename (not the full path)
is preserved separately in the non-path-validated
`metadata.parameters["media_filename"]` field, which is bounded,
free-form metadata rather than a path Phase 2.10 validates as such.

`metadata.parameters` also records `threshold`, `max_events`, and
`filter: "scdet"`. `metadata.tool_name` is always `"ffmpeg"`;
`metadata.tool_version` comes from the existing
`ffmpeg_tools.get_tool_version("ffmpeg")` helper (already used
elsewhere in this codebase), which returns `"unknown"` rather than
raising if version detection ever fails. `metadata.warnings` and the
top-level `AdapterResult.warnings` both explicitly flag the result as
ffmpeg-detected candidate evidence, not confirmed truth — and add an
extra note if zero candidates were found, or if detections were
truncated to `max_events`.

### No events, and truncation

A flat/unchanging video legitimately produces zero `scdet` candidates.
This is not an error: `build_visual_change_adapter_result` returns a
normal `AdapterResult` with an empty `visual_change_events` list (which
still validates cleanly against Phase 2.10's contract), plus a warning
noting that no candidates were detected at the given threshold. If
ffmpeg reports more candidates than `max_events`, the list is
truncated (kept in detection order) and a warning records how many
were kept vs. dropped — the adapter never silently returns partial
data without saying so.

## What the CLI command does

`clulatent analysis generate-ffmpeg-visual-change-adapter-result
<media-path> [--output/-o PATH] [--force] [--threshold N]
[--max-events N]`:

1. Builds the adapter result by running ffmpeg's `scdet` filter over
   `media-path`.
2. On any failure (missing ffmpeg, invalid/unreadable media, ffmpeg
   subprocess failure, out-of-range `--threshold`/`--max-events`), the
   command prints a clean, single-line error via the same `_fail(...)`
   helper every other CLI command in this codebase uses, and exits
   nonzero — never an unhandled traceback.
3. With no `--output`, prints the JSON to stdout via
   `console.print(text, markup=False, soft_wrap=True)` — the same
   established Phase 2.13 pattern, for the same reason: this output is
   fully self-generated JSON, so disabling markup/soft-wrap keeps
   stdout parseable and free of any raw control sequence.
4. With `--output PATH`, refuses to overwrite an existing file unless
   `--force` is given, refuses if the parent directory does not exist,
   and otherwise writes the JSON (plus a trailing newline) to `PATH`.

This command never touches a `.clulatent` package. It has no package
argument, acquires no lock, and writes no track, manifest, or receipt
— its only possible side effect is writing the one JSON file the user
explicitly named with `--output`. The generated result is a normal
file the user then feeds into `dry-run-adapter` / `import-adapter-result`
separately, exactly like the Phase 2.13 fixture.

## How the result can be dry-run with Phase 2.11

```sh
clulatent analysis generate-ffmpeg-visual-change-adapter-result clip.mp4 -o visual_change.json
clulatent analysis dry-run-adapter visual_change.json --package "$PKG"
```

using Phase 2.11's existing `dry_run_adapter_result`/
`load_adapter_result_json` verbatim. No new dry-run logic is added by
this phase.

## How the result can be imported with Phase 2.12

```sh
clulatent analysis import-adapter-result "$PKG" visual_change.json
```

which validates it (Phase 2.10's `validate_adapter_result`), then
writes the non-empty `visual_change_events` lane through the same
Phase 2.7 `append_analysis_events` writer every other canonical write
in this codebase uses. No new writer or import logic is added by this
phase.

## Why results are candidates/evidence only

Every event carries `payload.evidence_label: "ffmpeg visual-change
candidate"` and `payload.description: "ffmpeg scene-score candidate"`.
The result-level and metadata-level `warnings` both restate that this
is ffmpeg-detected candidate evidence, not confirmed truth about any
real scene change. `scdet` is a signal-processing heuristic over pixel
differences, not a semantic understanding of what changed or why —
this module never claims otherwise, in any string it ever emits.

## How locks still prevent import

Unchanged from Phase 2.10/2.12: if the target package carries a valid
integrity lock, `import-adapter-result` refuses before any lane is
written, via the Phase 2.7 writer's `lock_status()` check reached
through `write_adapter_result`. A dry-run against a locked package
still exits `0` (reporting `would write: False`). This phase adds no
new lock behavior — a real ffmpeg-derived result is refused by a
locked package exactly as a fixture result is.

## How review_events can later approve/reject/correct

Unchanged from Phase 2.13/2.14: once a `visual_change_events` record
from this adapter is committed to `tracks/visual_change_events.jsonl`,
it is ordinary, canonical evidence, indistinguishable to
`review_writer.py` from fixture-produced or hand-authored evidence. A
`review_approval`, `review_rejection`, or `review_correction` event can
target it by id the same way it targets anything else — an ffmpeg
scene-score candidate being *real* (derived from an actual video) does
not exempt it from review, or promote it to trusted truth on its own.

## Why this is not adapter runtime / plugin discovery

No adapter is executed, discovered, or loaded from an external
location. `build_visual_change_adapter_result` is a plain Python
function that shells out to one fixed, hardcoded external tool
(`ffmpeg`, resolved via the existing `resolve_tool`) — there is no
`AnalysisAdapter` implementation satisfying Phase 2.10's `Protocol`,
no registry, no plugin manifest, and no dynamic lookup of installed
adapters. This phase adds one more concrete adapter a human can invoke
directly by name, exactly like Phase 2.13's fixture adapter — not a
mechanism for finding or loading adapters in general.

## Why this is not Studio UI

This phase adds a Python module, one CLI subcommand, and tests — no
graphical interface, no persistent server, no interactive review
surface. `generate-ffmpeg-visual-change-adapter-result` is a single,
stateless CLI invocation (aside from the one ffmpeg subprocess it
runs) that prints or writes JSON and exits.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
This adapter's only output is a plain JSON document in the same shape
every other adapter result document in this codebase already uses —
not a new file format or a data model backing any external tool.

## Safety properties (enforced by tests)

- FFmpeg is invoked only through `security.subprocess.run_tool`: no
  `shell=True`, a hard timeout, bounded stdout/stderr capture.
- Missing ffmpeg is a clean `VisualChangeAdapterError` /
  nonzero-exit CLI error, never a traceback (verified with
  `reset_tool_cache()` + a `PATH` that cannot resolve `ffmpeg`).
- Invalid media (e.g. a `.mp4` containing garbage bytes) is a clean
  error, not a crash.
- No-event media (a flat, unchanging video) produces a valid, empty
  `visual_change_events` result, not an error.
- `threshold` and `max_events` are validated and bounded before any
  subprocess runs; out-of-range or wrong-typed values are rejected
  cleanly, from both the Python API and the CLI.
- Generated results use only the `visual_change_events` lane from the
  Phase 2.6 lane catalog.
- Generated events avoid every `FORBIDDEN_IDENTITY_FIELDS`/
  `IDENTITY_FLAG_FIELDS` name.
- Generated timestamps are always integers (milliseconds).
- `input_sources` never contains the real absolute media path.
- CLI stdout is always parseable JSON with no unsafe terminal control
  sequences.
- CLI `--output` mode refuses to silently overwrite an existing file
  without `--force`.

## Non-goals (explicit)

- No ML model, OCR runtime, or object detector of any kind.
- No semantic scene understanding — `scdet` is a pixel-difference
  heuristic, and this module never claims it is anything more.
- No adapter execution runtime, dynamic plugin loading, or
  installed-adapter discovery.
- No shell execution (`shell=True` is never used).
- No direct package writes — this command has no package argument and
  never touches a `.clulatent` package.
- No new validation, writer, dry-run, or import logic — every check
  this phase's tests exercise is a direct call into Phase 2.6, 2.7,
  2.10, 2.11, or 2.12 code, unmodified.
- No change to any frozen phase module or command.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.15 usage |
|---|---|---|
| 2.6 | `analysis_lanes.py` schema/shape checks | Reached indirectly via `validate_adapter_result`; `visual_change_events` is one of the frozen 14-lane catalog |
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | Reached indirectly via `write_adapter_result`; the sole writer of the imported track/manifest/receipt |
| 2.8 | `clulatent analysis ...` CLI | Unmodified; `generate-ffmpeg-visual-change-adapter-result` added as a new sibling command in the same `analysis_app` group |
| 2.10 | `analysis_adapters.py` contracts + `write_adapter_result` | `AdapterMetadata`/`AdapterResult`/`AdapterDeclaredOutputs`/`validate_adapter_result`/`write_adapter_result` reused directly, unmodified |
| 2.11 | `analysis_adapter_dry_run.py` | `dry_run_adapter_result`/`load_adapter_result_json` reused directly, unmodified |
| 2.12 | `import-adapter-result` CLI command | Reused directly, unmodified, to prove the real result imports cleanly |
| 2.13 | First non-ML adapter (fixture data) | Established the hedged-language, evidence-not-truth pattern this phase follows for real data |
| Security | `security/subprocess.py`, `security/paths.py` | `run_tool`/`resolve_tool` reused directly, unmodified; `validate_relative_posix`'s absolute-path rejection is why `input_sources` uses a fixed safe label instead of the real media path |

## Files changed

- `src/clu_latent/analysis_ffmpeg_visual_change_adapter.py` (new)
- `src/clu_latent/cli.py` (new `clulatent analysis
  generate-ffmpeg-visual-change-adapter-result` command)
- `tests/test_analysis_ffmpeg_visual_change_adapter.py` (new, 33 tests)
- `docs/PHASE_2_15_FIRST_REAL_NON_ML_ADAPTER.md` (new, this file)
- `README.md` (roadmap bullet added)

## Summary

Phase 2.15 gives this codebase its first adapter whose evidence comes
from actually reading a real media file — wrapping ffmpeg's built-in
`scdet` filter behind the same hardened subprocess pattern already
used everywhere else in this project, with the same bounded,
hedged-language event shape the Phase 2.13 fixture established. It
adds no ML dependency, no semantic claim, no direct package write, no
adapter runtime, and no new validation/writer/import logic — visual
change *candidates* flow through exactly the same generate → dry-run
→ import → validate pipeline every prior phase already built. It does
not make CLULatent claim truth about real media, and it does not write
package internals directly.
