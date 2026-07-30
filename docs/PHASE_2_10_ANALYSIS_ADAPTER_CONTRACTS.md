# Phase 2.10 — Analysis Adapter Contracts

Base: Phase 2.9 Analysis Lane Validation Integration is frozen
(tag `phase-2.9-analysis-lane-validation-integration-freeze`).
New module: `src/clu_latent/analysis_adapters.py`.

## Scope

This phase defines the safe internal contract a **future** analysis
adapter must satisfy before its output can become CLULatent
analysis-lane evidence. It implements no video/audio analysis, no
FFmpeg tracker, no OCR runtime, no object detector, no ML model
dependency, no semantic truth generation, no adapter execution
runtime, no Studio UI, and no CLUBIN. It defines no runtime registry,
no dynamic plugin loading, no external-tool import, no subprocess
execution, and no installed-adapter discovery.

## Core principle

Adapters produce **evidence**. Evidence is not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

Every phase since 2.5 has restated this because it is the load-bearing
assumption behind the whole analysis-lane design: an adapter (human,
script, or future ML pipeline) is never trusted just because it ran.
Its output only becomes canonical once it has passed Phase 2.6's
shape/bound checks, been written through Phase 2.7's atomic,
lock-aware writer, and (optionally, later) been reviewed via
`review_events`.

## What an adapter is

In this codebase's terms, "an adapter" is any process — human,
script, or future tool wrapper — that produces candidate
analysis-lane events and hands them, as plain JSON-shaped dicts, to
something that validates and writes them. Nothing more. An adapter
does not need to be a Python class, does not need to subclass
anything, and does not need to be discovered or registered anywhere.
The `AnalysisAdapter` `Protocol` defined in this phase exists purely
so a *future* concrete adapter implementation has an agreed structural
shape (a `run(...) -> AdapterResult` method) to target — nothing in
this codebase ever imports, instantiates, calls, or discovers a class
that implements it.

## What an adapter is not

- Not a truth oracle. An adapter's output describes what a detector
  *proposed*, never a confirmed fact about the world.
- Not privileged. An adapter has no special permission to bypass
  Phase 2.6 validation, Phase 2.7's atomicity/locking, or Phase 2.9's
  package-level validation. Every adapter-produced event goes through
  exactly the same checks a hand-typed JSON event would.
- Not a package-internals writer. An adapter never touches
  `manifest.json`, a `tracks/*.jsonl` file, or `receipts/*.jsonl`
  directly. It can only ever hand a candidate result to
  `write_adapter_result`, which itself only ever calls the existing
  Phase 2.7 `append_analysis_events` — the only code in this
  repository that is allowed to write those files.
- Not a runtime. There is no adapter registry, no plugin discovery, no
  dynamic loading, and no subprocess execution anywhere in this phase.

## Adapter output is evidence, not truth

`AdapterResult.events_by_lane` holds raw, untrusted candidate event
dicts — the same shape `analysis_lanes.validate_analysis_event`
already checks a hand-written record against. Nothing about being
produced by "an adapter" (rather than typed by a human via
`clulatent analysis append`) grants a shortcut past that validation.
`validate_adapter_result` calls the exact same
`analysis_lanes.validate_analysis_track` function Phase 2.9's package
validation and Phase 2.7's writer already call — there is only one
gate, reused by every caller.

## Adapters do not bypass validation

`validate_adapter_result` is layered, matching the "layered
independent checks" precedent established in Phase 2.7/2.9:

1. `AdapterMetadata` fields (`adapter_name`, `adapter_version`,
   `tool_name`, `tool_version`, optional `model_name`/`model_version`)
   are bounded strings, checked the same way `analysis_lanes.py`
   bounds `producer.name`/`producer.version`.
2. `metadata.parameters` and `AdapterResult.receipt_metadata` are
   bounded, JSON-serializable payloads (same byte bound as an
   analysis event's `payload`).
3. `metadata.input_sources` are path-safe: lexically checked with
   `security.paths.validate_relative_posix` always, and additionally
   resolved with `security.paths.resolve_in_package` (full filesystem
   containment + symlink-escape refusal) whenever a real
   `package_root` is supplied.
4. Every non-empty lane in `events_by_lane` is validated with
   `analysis_lanes.validate_analysis_track` — unsupported lane names,
   invalid event shapes, out-of-range confidence, unsafe payload
   paths, and identity claims in object/tracking-lane payloads are all
   caught there, not reimplemented here.
5. `AdapterDeclaredOutputs` (an optional self-declared summary — lanes,
   per-lane counts, output track references), if present, is checked
   for shape only: supported lane names, non-negative counts, and
   output track references drawn only from the fixed whitelist of bare
   lane names or their canonical `tracks/<lane>.jsonl` paths (mirrors
   Phase 2.9's `_check_output_track_reference`) — this alone rules out
   an absolute path, parent traversal, or a symlink-escape attempt,
   since none of those strings can equal a whitelisted form.

"No semantic truth claims" is not a separate mechanical check beyond
the above: the 14-lane catalog (Phase 2.5) is already scoped to
evidence-shaped lanes (scenes, motion, objects, OCR, audio, etc.),
none of which admit a "this is definitely X" field, and the
lane-agnostic identity-claim rule already refuses the one concrete
way a payload could assert a real-world fact about a person. There is
no `semantic_events` lane and this phase adds none.

## Adapters do not write directly to package internals

`AdapterResult` and `AdapterMetadata` are plain dataclasses with no
filesystem access of their own — constructing one touches no disk.
The only path from an `AdapterResult` to an actual package file is
`write_adapter_result`, and that function does nothing but:

1. Check the package exists.
2. Call `validate_adapter_result` (refusing, writing nothing, on any
   error).
3. Refuse if every lane is empty (nothing to write).
4. Loop over each non-empty lane and call
   `analysis_writer.append_analysis_events` — the same Phase 2.7
   function `clulatent analysis append`/`append-file` already call.

No new atomic-write, lock-acquisition, or manifest-update logic is
added. The writer remains the sole source of atomicity, manifest
updates, receipts, and lock enforcement — exactly the same file
touched by Phase 2.7 and Phase 2.8, touched the same way.

## How Phase 2.6 schemas validate outputs

Unchanged and reused verbatim: `analysis_lanes.validate_analysis_track`
is called once per non-empty lane in `AdapterResult.events_by_lane`,
with `package_root` forwarded through so path-like payload fields get
full filesystem-containment/symlink checking whenever a real package
is the validation target (matching how `validate_adapter_result` is
called from `write_adapter_result`).

## How Phase 2.7 writer commits outputs

`write_adapter_result` calls `analysis_writer.append_analysis_events`
once per lane with events. Each call is independently atomic
(temp file + fsync + `os.replace`, lane track then manifest then
optional receipt, with rollback of earlier files if a later write in
the same call fails) exactly as Phase 2.7 already guarantees. This
bridge adds **no cross-lane atomicity** beyond that: if an
`AdapterResult` has events in two lanes and the first lane's write
succeeds but the second's then fails (e.g. the package became locked
in between the two calls), the first lane's write is not rolled back.
This is intentional and matches calling `append_analysis_events` by
hand once per lane — the bridge is a convenience loop, not a new
transaction boundary.

`metadata.adapter_version` has no home in the frozen
`AnalysisAdapterReceipt` dataclass (Phase 2.6) or the frozen
`append_analysis_events` signature (Phase 2.7) — neither is modified
by this phase. Instead, `write_adapter_result` folds
`adapter_version` into the receipt's existing free-form `environment`
dict (already threaded through by Phase 2.7), merged with
`AdapterResult.receipt_metadata` if supplied, so both are preserved on
disk in `receipts/analyze.jsonl` without touching either frozen
module.

## How Phase 2.8 CLI can manually write adapter-produced JSON

Unchanged: `clulatent analysis append`/`append-file` remain the
CLI-level path for writing already-produced evidence — including
evidence a future adapter produced and serialized to JSON/JSONL by
hand or by an external process, then handed to the CLI. This phase
adds no new CLI surface; a future `clulatent analyze ...` command (if
one is ever added) would call `write_adapter_result` the same way
`analysis append` calls `append_analysis_events` today, with no
additional operation-lock wrapper needed (matching Phase 2.7/2.8's
established non-reentrant-lock reasoning).

## How Phase 2.9 package validation catches bad lane tracks

Unchanged and untouched by this phase: once an adapter's output has
been written (via `write_adapter_result` or the Phase 2.8 CLI), it
is just another `tracks/<lane>.jsonl` entry in `manifest.json`.
`clulatent validate`/`validate_package` already validates every
supported-lane track and `receipts/analyze.jsonl` the same way
regardless of whether a human or a future adapter produced it — there
is no adapter-specific validation bypass anywhere in `validate.py`.

## How review_events can later approve/reject/correct adapter outputs

Once an adapter-produced event exists as a canonical
`tracks/<lane>.jsonl` record, it is indistinguishable from any other
canonical event to `review_writer.py`: a `review_approval`,
`review_rejection`, or `review_correction` event (Phase 2.0-2.4) can
target it by id exactly the same way it targets a human-authored or
ingest-produced event. This phase adds nothing new here — it only
ensures adapter output reaches that state (canonical, in a lane
track) through the same validated, receipted path everything else
uses, so the existing review workflow already applies unmodified.

## Why this is not adapter runtime

No adapter implementation exists in this codebase, and this phase
adds none. `AnalysisAdapter` is a `typing.Protocol` — a structural
type description, checked (if at all) by a type checker or
`isinstance()` against `@runtime_checkable`, never invoked by any code
in this module. There is no way, using only what this phase adds, to
name an adapter by string, look one up, load one from a package, or
execute one. A future phase that adds a concrete adapter (e.g. a
PySceneDetect wrapper) would import this module's dataclasses/Protocol
and call `write_adapter_result` directly — this phase only makes that
future work smaller by having the contract ready.

## Why this is not semantic truth generation

Nothing in this phase (or anything it calls) makes a claim about what
is *true* in the source media. `validate_adapter_result` only checks
that a candidate result is well-formed, bounded, and safe — it has no
opinion on whether the events inside it are correct. The identity-
claim rule (inherited unmodified from Phase 2.6) is the one place this
schema actively refuses a category of claim (real-person identity)
rather than merely checking shape; everything else is left to human
review (`review_events`), exactly as designed since Phase 2.0.

## Why this is not CLUBIN or Studio UI

This phase adds a Python module and its tests. No binary, no UI, no
end-user-facing surface of any kind is introduced. `AdapterMetadata`,
`AdapterResult`, and `AdapterDeclaredOutputs` are contract objects for
future in-process Python callers (adapters, or a future CLI command
wrapping them) — they are not a file format for CLUBIN or a data model
backing a Studio UI screen.

## Non-goals (explicit)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime, subprocess execution, or
  external-tool import.
- No dynamic plugin loading or installed-adapter discovery.
- No new CLI surface.
- No change to the frozen Phase 2.6 (`analysis_lanes.py`) or Phase 2.7
  (`analysis_writer.py`) modules.
- No semantic truth generation.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.10 usage |
|---|---|---|
| 2.5 | Lane catalog design | `ANALYSIS_LANE_NAMES` reused unmodified |
| 2.6 | `analysis_lanes.py` schema/shape checks | `validate_analysis_track`, `is_supported_analysis_lane`, `SUPPORTED_ANALYSIS_LANES` called directly, not duplicated |
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | `append_analysis_events` called once per lane from `write_adapter_result` |
| 2.8 | `clulatent analysis ...` CLI | Unmodified; still the CLI-level path for manually-produced JSON |
| 2.9 | `validate_package` lane/receipt validation | Unmodified; applies to adapter-written tracks the same as any other |

## What later phases need

A future phase that adds a real adapter (e.g. a PySceneDetect bridge)
would: construct an `AdapterMetadata`/`AdapterResult` from that tool's
actual output, call `write_adapter_result`, and rely on this phase's
contract layer plus Phase 2.6-2.9 for everything else. No further
contract-layer work should be needed for a single-lane, synchronous
adapter; a future multi-adapter runtime/registry would still be free
to build on the `AnalysisAdapter` `Protocol` defined here without
needing to change it.

## Files changed

- `src/clu_latent/analysis_adapters.py` (new)
- `tests/test_analysis_adapters.py` (new, 25 tests)
- `docs/PHASE_2_10_ANALYSIS_ADAPTER_CONTRACTS.md` (new, this file)
- `README.md` (roadmap bullet added)

## Summary

Phase 2.10 gives a future analysis adapter one safe, small, fully
conservative contract to target: typed, bounded, path-safe metadata
and result objects, validated with the exact same Phase 2.6 checks
everything else uses, written through the exact same Phase 2.7 writer
everything else uses, and visible to the exact same Phase 2.9 package
validation and review workflow everything else uses. It makes no
media analysis happen. It only makes the next phase that does smaller.
