# Phase 2.8: Analysis Lane CLI Commands

Status: **safe manual CLI door — no analysis runtime**. Builds on the
frozen Phase 2.7 Analysis Lane Writer Receipts
(`docs/PHASE_2_7_ANALYSIS_LANE_WRITER_RECEIPTS.md`, tag
`phase-2.7-analysis-lane-writer-receipts-freeze`), and transitively on
the frozen Phase 2.6 schema primitives, Phase 2.5 design, and Phase 2.4
portability rules. This phase adds a `clulatent analysis ...` command
group: the smallest CLI surface for writing **already-produced**
analysis-lane events into a real `.clulatent` package. It implements no
video/audio analysis, no FFmpeg-based tracker, no OCR runtime, no
object detector, no ML model dependency, no semantic truth generation,
and no adapter execution runtime.

> Given a batch of raw candidate lane-event JSON a future adapter, a
> script, or a human has already produced, how does it get from a
> terminal into `tracks/<lane>.jsonl` — validated, receipted, and
> refusing to touch a locked package — without writing a single line
> of new analysis intelligence?

## Scope of this phase

Implemented, in `src/clu_latent/cli.py`, under a new `analysis`
sub-Typer group (`app.add_typer(analysis_app, name="analysis")`,
mirroring the existing `review` group):

- `clulatent analysis lanes` — prints the fourteen supported lane
  names from Phase 2.5/2.6's catalog (`analysis_lanes.ANALYSIS_LANE_NAMES`),
  one per line.
- `clulatent analysis append <package> <lane> --event-json '<json>'` —
  parses one event object, validates it, and appends it via Phase 2.7's
  `append_analysis_events`.
- `clulatent analysis append-file <package> <lane> <events.jsonl|events.json>` —
  same, but for a whole batch loaded from a file. Format
  (JSONL vs. a JSON array) is auto-detected from the file's first
  non-whitespace character; either way, every item is validated
  together before anything is written, so a single bad event refuses
  the whole batch.
- `clulatent analysis validate-file <lane> <events.jsonl|events.json>` —
  runs Phase 2.6's `validate_analysis_track` and prints
  errors/warnings, without ever writing a track, a manifest, or a
  receipt. Takes an optional `--package` purely to extend path-safety
  checks on payload path fields to a real filesystem root; no package
  is required, and none is ever mutated by this command.
- Minimal receipt metadata flags on `append`/`append-file`:
  `--adapter-name` (default `manual-cli`), `--tool-name`/`--tool-version`
  (default `clulatent`/the package's own version), `--model-name`,
  `--model-version`, `--parameter-json`, `--input-source` (repeatable),
  and `--receipt/--no-receipt` (default: write one). All are passed
  straight through to Phase 2.7's `append_analysis_events`.

Not implemented (explicitly out of scope, matching Phase 2.5/2.6/2.7):

- No adapter runtime, no FFmpeg tracker, no OCR engine, no object
  detector, no ML model dependency, no external tool invocation. This
  CLI never shells out to anything; it only parses JSON already on
  disk or on the command line.
- No cross-lane referential-integrity checking — still deferred
  exactly as Phase 2.5 left it.
- No mutation of `sources/`, any other `tracks/*.jsonl`, or
  `index/search.sqlite`.

## Core principle (restated)

Adapters — and now, a human at a terminal — produce **evidence**, not
truth. This CLI adds no new trust: every event it writes still passes
through the same Phase 2.6 shape/bounds validation and Phase 2.7
atomic-write/rollback/lock-refusal path a future real adapter would
use. *Detected does not mean trusted. Generated does not mean
canonical. Canonical means validated, bounded, receipted, reviewable,
and lockable.* Nothing in this phase changes what "canonical" means —
it only gives a safe, conservative way to reach it by hand today,
before any real adapter exists.

## Why this is not an adapter runtime

There is still no code anywhere in this project that runs FFmpeg's
scene-cut filters, an OCR engine, an object detector, or a speech/audio
model to *produce* analysis-lane events. `clulatent analysis append...`
takes events as input — via `--event-json` or a file — exactly the
same shape Phase 2.6 already validates and Phase 2.7 already writes.
Nothing about parsing that JSON constitutes analysis; the CLI is
indifferent to whether the JSON came from a real detector, a hand-typed
test fixture, or a script that fabricated it entirely. That is by
design: this phase is a *door*, not an intelligence.

## Why raw event JSON in, the same safe write path out

`append`/`append-file` do not introduce any new parsing or validation
rules of their own. `--event-json` and `--parameter-json` reuse the
existing `_parse_payload_json` CLI helper (already used by
`review correct`/`review override` for JSON payload options) — parse,
reject malformed JSON cleanly, reject non-object JSON cleanly — and
`append-file`'s batch loader (`_load_candidate_events_file`) is new
only in that it accepts either JSONL or a JSON array; both branches
still hand plain dicts to Phase 2.7's `append_analysis_events`
unchanged. Every actual schema/bounds/identity/path-safety check still
happens exactly where Phase 2.6 and Phase 2.7 already put it — this
phase adds zero new validation logic, only a place to type JSON into.

## Why `append`/`append-file` don't wrap their own operation lock

Phase 2.7's docstring is explicit about this: `review_writer.append_review_event`
does **not** acquire the operation lock itself (the `review` CLI
commands wrap it), but `analysis_writer.append_analysis_events`/
`write_analysis_receipt` **do** acquire it internally, precisely
because Phase 2.7 shipped with no CLI surface yet to wrap it. Now that
Phase 2.8 adds one, the CLI commands call `append_analysis_events`
directly and let its own internal lock acquisition and
`AnalysisWriteError` (which already covers "Package not found" and
lock contention with clean messages) handle everything — wrapping it in
a second `operation_lock(...)` here would double-acquire against a
non-reentrant lock and simply break. This is the one deliberate
divergence from the `review` group's CLI wiring, and it was decided in
Phase 2.7, not invented in this phase.

## `validate-file`: read-only, package-optional by design

`validate-file` never calls the writer, never checks `lock_status`,
and never acquires the operation lock — it only calls Phase 2.6's
non-raising `validate_analysis_track` and prints the resulting errors/
warnings, exiting 1 if there are any errors. `--package` is optional:
without it, only the lexical relative-POSIX path-safety check applies
to any path-like payload fields (Phase 2.6's own semantics for a `None`
`package_root`); with it, the same real filesystem containment/
symlink-escape check every other canonical path check in this project
uses is layered on top. Either way, nothing is written, so this command
never needs an unlocked package, and can validate candidate events for
a lane before a package even exists.

## Interaction with existing systems

### Review (Phases 2.0–2.2)

No change needed, same as Phase 2.7. `review_writer.py` finds a review
target by searching every non-review track in `manifest.tracks` for a
matching event id — an event written by `clulatent analysis append...`
is just another entry in that list the moment it lands on disk. A
human reviewer can immediately run `clulatent review approve/reject/
correct ...` against any event id this CLI wrote, exactly as if a real
adapter had produced it.

### Locking (Phase 1.7.5)

No change needed. `lock.py` already globs `tracks/*.jsonl` and
`receipts/*.jsonl` generically, so the next `clulatent lock` after an
`analysis append`/`append-file` run picks up `tracks/<lane>.jsonl` and
`receipts/analyze.jsonl` automatically — the lock hash changes exactly
because the lane track changed, which is how a lock is supposed to
detect any lane write, manual or adapter-driven, with no special-casing
for this CLI. `append`/`append-file` independently refuse to write
against a package with a currently-valid integrity lock (surfaced as a
clean CLI error, not a traceback), matching the `review` group exactly.

### Portability (Phase 2.4)

No change needed. Every event this CLI writes goes through the same
`append_analysis_events` path Phase 2.7 already made portable: ordinary
canonical JSONL using the shared envelope, every payload path field
checked through `security.paths.resolve_in_package` against the real
package root, no absolute paths, no parent traversal, no symlink
escapes. The CLI adds no new file format and no new path-handling
logic of its own.

### Receipts

`--receipt/--no-receipt` (default: write) controls whether an
`AnalysisAdapterReceipt` entry is appended to `receipts/analyze.jsonl`
via Phase 2.7's `write_analysis_receipt`, unchanged. The receipt
metadata flags (`--adapter-name`, `--tool-name`, `--tool-version`,
`--model-name`, `--model-version`, `--parameter-json`,
`--input-source`) let a human record *what actually produced this
data* — even if that's just a person typing JSON — rather than
silently attributing it to nothing. Defaults are deliberately boring
and local: `--adapter-name manual-cli`, `--tool-name`/`--tool-version`
default to this project's own name/version (`clulatent`/its package
version), so a bare `clulatent analysis append ...` with no flags still
produces an honest, locally-meaningful receipt.

## Why this is not CLUBIN and not Studio UI

CLUBIN is a planned future compiled binary format a Rust
compiler/runtime would produce from a `.clulatent` package — nothing
in this phase compiles, packages, or transforms anything into a binary
form; every file this CLI writes is the same human-readable canonical
JSON/JSONL every other part of this project already produces. A
Studio UI, if one is ever built, would be a graphical, likely
adapter-integrated tool for reviewing and producing analysis data
interactively; `clulatent analysis ...` is a plain terminal command
that takes JSON as literal input and writes it through an already-safe
path — no UI, no visualization, no interactive review flow, and no
adapter orchestration of any kind.

## Non-goals (restated)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime — this CLI never runs an external tool.
- No cross-lane referential-integrity enforcement.
- No mutation of source tracks, unrelated tracks, or the derived
  search index.
- No unlock/relock workflow, no bypass of a valid integrity lock
  (`--force-stale-lock` only affects a *stale* operation lock, exactly
  as it already does for the `review` group — never the integrity
  lock).
- No claim that a written event is "trusted" or "canonical" beyond
  what Phase 2.6's validation and Phase 2.7's writer already
  established.

## Relationship to prior phases

| Phase | Relationship |
|---|---|
| 2.7 writer receipts | `append`/`append-file` call `append_analysis_events` unchanged; `--receipt` metadata flags map directly onto its keyword arguments; the CLI adds no new write logic. |
| 2.6 schema primitives | `validate-file` calls `validate_analysis_track` unchanged; `append`/`append-file` rely on the same validation running inside the Phase 2.7 writer they call. |
| 2.5 design | Gives the "adapter contract" a first concrete, manual entry point; cross-lane referential-integrity policy remains deferred exactly as that doc left it. |
| 2.4 portability | Written tracks/receipts are ordinary portable canonical files; no export-rule change needed. |
| 2.2 review writer | `analysis append`'s CLI wiring mirrors the `review` group's `_fail`/`_parse_payload_json` conventions; the one deliberate divergence (no CLI-level operation lock wrapper) was already decided in Phase 2.7. |
| 1.7.5 locking | Existing generic `tracks/*.jsonl`/`receipts/*.jsonl` globs cover every file this phase writes with no `lock.py` change. |

## What later phases still need to build

1. A real adapter that calls `append_analysis_events` (or shells out to
   `clulatent analysis append-file`) with genuinely detected events —
   still requires an actual detector/tool, not part of this phase.
2. Cross-lane referential-integrity validation (strict vs. warn),
   deferred since Phase 2.5.
3. Wiring lane-track validation into `validate.py`'s package-level
   checks (unchanged from Phase 2.7's own "still needs" list).
4. A Studio UI or any interactive review surface — this phase is a
   plain terminal door only.

## Files changed

- `src/clu_latent/cli.py`: new `analysis` sub-Typer group
  (`lanes`/`append`/`append-file`/`validate-file`) and supporting
  helpers (`_load_candidate_events_file`, `_run_analysis_append`).
- `tests/test_cli_analysis.py` (new): 21 tests.
- `docs/PHASE_2_8_ANALYSIS_LANE_CLI_COMMANDS.md` (new, this document).
- `README.md`: short Phase 2.8 roadmap note.

## Summary

Phase 2.8 gives a human — or a script standing in for a future adapter
— the smallest safe way to turn already-produced analysis-lane event
JSON into a real, validated, receipted, lock-respecting write, entirely
through existing Phase 2.6 validation and Phase 2.7 writer machinery.
No analysis intelligence is added — only the terminal door for
already-evidenced data to walk through, and a read-only way
(`validate-file`) to check that data before it ever touches a package
at all.
