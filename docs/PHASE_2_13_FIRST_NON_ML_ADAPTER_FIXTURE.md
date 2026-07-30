# Phase 2.13 — First Non-ML Adapter Fixture

Base: Phase 2.12 Analysis Adapter Result Import is frozen
(tag `phase-2.12-analysis-adapter-result-import-freeze`).
New module: `src/clu_latent/analysis_fixture_adapter.py`.
New CLI command: `clulatent analysis generate-fixture-adapter-result`.

## Scope

This phase adds the first conservative, deterministic, non-ML adapter
*fixture* — a tiny, hand-written generator that produces a valid
Phase 2.10 `AdapterResult`, purely to prove the adapter pipeline built
across Phase 2.6–2.12 actually works end to end, without ever
analyzing real media.

It implements no video/audio analysis, no FFmpeg tracker, no OCR
runtime, no object detector, no ML model dependency, no semantic truth
generation, no dynamic plugin loading, no adapter discovery, no
subprocess execution, no Studio UI, and no CLUBIN.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

The fixture in this phase is not exempt from this principle — it is
the principle's simplest possible demonstration. Its events use
deliberately hedged, safe evidence language ("synthetic scene
boundary", "fixture visual change", "impact-like transient",
"cross-lane fixture link") and make no real-world truth claim about
any video or audio. It is example/test data from the first line to
the last.

## What the fixture is

`analysis_fixture_adapter.py` defines:

- `build_fixture_adapter_result() -> AdapterResult` — returns a fresh,
  deterministic `AdapterResult` with one event in each of four lanes:
  `scene_events`, `visual_change_events`, `audio_transient_events`,
  and `cross_lane_link_events`. Every id, timestamp, and payload field
  is a fixed constant; nothing reads the clock, the filesystem, or any
  source of randomness.
- `fixture_adapter_result_to_dict(result=None) -> dict` — serializes an
  `AdapterResult` (defaulting to a fresh fixture one) into the plain
  JSON dict shape Phase 2.11's loader and Phase 2.12's import command
  both expect.
- `fixture_adapter_result_json(*, indent=2) -> str` — the same, as a
  JSON string.

`AdapterMetadata` is filled in with a fixed `adapter_name`
(`clulatent-fixture-adapter`), `adapter_version`, `tool_name`,
`tool_version`, bounded `parameters` (`{"fixture": True,
"deterministic": True}`), one safe `input_sources` entry
(`sources/fixture-input.txt` — a relative-POSIX path the fixture
*claims* to have read, per Phase 2.10's contract; it does not need to
exist on disk, and this module never reads, writes, or otherwise
touches it), and a `warnings` entry flagging the result as
fixture/example evidence. `AdapterDeclaredOutputs` is filled in
consistently with the actual `events_by_lane` (same lanes, same
per-lane counts).

## The cross-lane link event, and why it is safe without a real package

The fixture's `cross_lane_link_events` event references the fixture's
own `scene_events` and `audio_transient_events` event ids in its
`source_event_ids`/`target_event_ids` payload fields, using
`relation_type: "co_occurs"` (not a forbidden causal term like
`"causes"` or `"proves"`). This is safe even though nothing has been
written to any package yet: Phase 2.6's `validate_analysis_track`
checks a cross-lane-link payload's *shape* (bounded string lists, no
self-reference, no forbidden causal relation type) but never requires
the referenced ids to already exist as written track records anywhere
— there is no cross-track referential-integrity check in this
codebase, by design, since a lane's events and a link event can be
proposed together in the same adapter result before either is
written.

## What the CLI command does

`clulatent analysis generate-fixture-adapter-result [--output/-o PATH]
[--force]`:

1. Builds the fixture result and serializes it to a JSON string.
2. With no `--output`, prints the JSON to stdout via
   `console.print(text, markup=False, soft_wrap=True)` — deliberately
   bypassing this codebase's `safe_console_text` markup-escaping helper
   (which exists for *untrusted*, package-derived strings) because this
   output is fully self-generated, deterministic content: escaping it
   would corrupt valid JSON by inserting backslashes in front of `[`
   and `]` characters, and Rich's default soft-wrapping would break
   long lines mid-token. `markup=False, soft_wrap=True` avoids both,
   so stdout is always parseable JSON, and never contains any raw ANSI
   control sequence (none is ever emitted in the first place).
3. With `--output PATH`, refuses to overwrite an existing file unless
   `--force` is given, refuses if the parent directory does not exist,
   and otherwise writes the JSON (plus a trailing newline) to `PATH` —
   mirroring the existing `ingest -o`/`--force` CLI precedent, since
   this is a plain user-supplied output path, not one derived from
   untrusted or package-relative data.

This command never touches a `.clulatent` package. It has no package
argument, acquires no lock, and writes no track, manifest, or receipt
— its only possible side effect is writing the one JSON file the user
explicitly named with `--output`.

## How the result can be dry-run with Phase 2.11

The generated JSON is a valid Phase 2.10 `AdapterResult` document, so
it can be fed directly into `clulatent analysis dry-run-adapter`:

```sh
clulatent analysis generate-fixture-adapter-result -o fixture.json
clulatent analysis dry-run-adapter fixture.json
```

reporting all four lanes, four total events, and (with `--package`) a
package-writability check — using Phase 2.11's existing
`dry_run_adapter_result`/`load_adapter_result_json` verbatim. No new
dry-run logic is added by this phase.

## How the result can be imported with Phase 2.12

The same JSON can be committed into a real package with
`clulatent analysis import-adapter-result`:

```sh
clulatent analysis import-adapter-result my.clulatent fixture.json
```

which validates it (Phase 2.10's `validate_adapter_result`), then
writes each non-empty lane through the same Phase 2.7
`append_analysis_events` writer every other canonical write in this
codebase uses — producing four real `tracks/<lane>.jsonl` files and
one `receipts/analyze.jsonl` entry with `adapter_name:
"clulatent-fixture-adapter"`. No new writer or import logic is added
by this phase; the fixture is simply the first concrete input this
pipeline has ever been exercised against end to end in this
repository's tests.

## How Phase 2.9 validation checks committed tracks

Unchanged: once the fixture's events are written, they are just
another set of `tracks/<lane>.jsonl` entries in `manifest.json`,
indistinguishable to `validate_package` from a hand-written, real
adapter-produced, or Phase 2.8-appended event. `clulatent validate`
still independently re-checks every track and receipt regardless of
origin — this is tested directly
(`test_package_validates_after_fixture_import`).

## How review_events can later approve/reject/correct imported evidence

Unchanged: once a fixture event exists as a canonical
`tracks/<lane>.jsonl` record, it is indistinguishable from any other
canonical event to `review_writer.py`. A `review_approval`,
`review_rejection`, or `review_correction` event can target a fixture
event by id exactly the same way it targets human-authored,
ingest-produced, or real-adapter-produced evidence — reviewing a
fixture event has exactly the same meaning as reviewing anything else,
which is itself part of the demonstration: the pipeline treats
evidence uniformly regardless of where it came from.

## Why this is not semantic truth generation

The fixture events assert nothing about any real scene, image, or
sound. Every payload field, and every event's `type`, is deliberately
hedged ("synthetic", "fixture", "impact-like", "cross-lane fixture
link") to make clear this is manufactured example data, not a claim
about reality. No confidence score implying certainty about real-world
content is attached. Nothing in this module infers, classifies, or
labels anything about an actual video or audio file — there is no
video or audio file involved at all.

## Why this is not adapter runtime

No adapter is executed, discovered, or loaded. `build_fixture_adapter_result`
is a plain Python function returning fixed data — there is no
`AnalysisAdapter` implementation, no `run()` call satisfying that
Phase 2.10 `Protocol`, no registry, and no dynamic lookup of any kind.
This phase adds one more static data source a human can point Phase
2.11/2.12 at, nothing more.

## Why this is not Studio UI

This phase adds a Python module, one CLI subcommand, and tests — no
graphical interface, no persistent server, no interactive review
surface. `generate-fixture-adapter-result` is a single, stateless,
side-effect-free (other than an explicitly requested `--output` file)
CLI invocation that prints or writes JSON and exits.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
The fixture module's only output is a plain JSON document in the same
shape every other adapter result document in this codebase already
uses — not a new file format or a data model backing any external
tool.

## Non-goals (explicit)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime, subprocess execution, or
  external-tool import.
- No dynamic plugin loading or installed-adapter discovery.
- No new validation, writer, dry-run, or import logic — every check
  this phase's tests exercise is a direct call into Phase 2.6, 2.7,
  2.10, 2.11, or 2.12 code, unmodified.
- No change to the frozen Phase 2.6 (`analysis_lanes.py`), Phase 2.7
  (`analysis_writer.py`), Phase 2.10 (`analysis_adapters.py`), Phase
  2.11 (`analysis_adapter_dry_run.py`), or Phase 2.12
  (`import-adapter-result`) modules/commands.
- No claim of real-world truth, in any event payload, at any point.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.13 usage |
|---|---|---|
| 2.6 | `analysis_lanes.py` schema/shape checks | Reached indirectly via `validate_adapter_result`; never called or duplicated directly |
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | Reached indirectly via `write_adapter_result`; the sole writer of tracks/manifest/receipts for a fixture import |
| 2.8 | `clulatent analysis ...` CLI | Unmodified; `generate-fixture-adapter-result` added as a new sibling command in the same `analysis_app` group |
| 2.9 | `validate_package` lane/receipt validation | Unmodified; still the authority on committed fixture tracks |
| 2.10 | `analysis_adapters.py` contracts + `write_adapter_result` | `AdapterMetadata`/`AdapterResult`/`AdapterDeclaredOutputs`/`validate_adapter_result`/`write_adapter_result` reused directly, unmodified |
| 2.11 | `analysis_adapter_dry_run.py` | `dry_run_adapter_result`/`load_adapter_result_json` reused directly to prove the fixture result dry-runs cleanly |
| 2.12 | `import-adapter-result` CLI command | Reused directly, unmodified, to prove the fixture result imports cleanly |

## Files changed

- `src/clu_latent/analysis_fixture_adapter.py` (new)
- `src/clu_latent/cli.py` (new `clulatent analysis
  generate-fixture-adapter-result` command)
- `tests/test_analysis_fixture_adapter.py` (new, 27 tests)
- `docs/PHASE_2_13_FIRST_NON_ML_ADAPTER_FIXTURE.md` (new, this file)
- `README.md` (roadmap bullet added)

## Summary

Phase 2.13 gives this codebase its first concrete, deterministic,
non-ML adapter result — proving, with a real test suite exercising a
real `.clulatent` package, that the adapter pipeline assembled across
Phase 2.6 through Phase 2.12 works from end to end: generate JSON,
validate it, dry-run it, import it, see it land as real tracks and a
real receipt, and confirm the package still validates afterward. It
adds no new validation rule, no new writer, and no new truth claim —
only a small, safe, honest example of evidence flowing through a
pipeline that was already there. It does not make CLULatent analyze
real media.
