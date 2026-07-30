# Phase 2.12 — Analysis Adapter Result Import

Base: Phase 2.11 Analysis Adapter Dry-Run Harness is frozen
(tag `phase-2.11-analysis-adapter-dry-run-harness-freeze`).
New CLI command: `clulatent analysis import-adapter-result`.
No new module — this phase adds one CLI command to `cli.py`, built
entirely on Phase 2.10's `write_adapter_result` and Phase 2.11's
`dry_run_adapter_result`.

## Scope

Phase 2.11 proved a candidate adapter result *would* be valid, without
writing anything. Phase 2.12 lets a user intentionally commit that
already-produced adapter result into a package. It implements no video/
audio analysis, no FFmpeg tracker, no OCR runtime, no object detector,
no ML model dependency, no semantic truth generation, no dynamic plugin
loading, no adapter discovery, no subprocess execution, no Studio UI,
and no CLUBIN.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

Import is the first point in this lane where an adapter's evidence
*becomes* canonical — but only by reusing exactly the same validation
and writer path every other canonical write already uses. It adds no
new trust: the same `AdapterResult` that fails a Phase 2.11 dry run
fails an import the same way, for the same reason.

## What import does

1. Safely parses the candidate adapter-result JSON file, reusing Phase
   2.11's `load_adapter_result_json` (bounded read, clean
   `AdapterResultLoadError` on malformed/non-object JSON — never a raw
   traceback).
2. Validates the loaded result with Phase 2.11's `dry_run_adapter_result`
   (which itself calls Phase 2.10's `validate_adapter_result`, and
   transitively Phase 2.6's `validate_analysis_track` per lane) against
   the target package. If the result has any validation error, or the
   package is missing or not a directory, import refuses and writes
   nothing.
3. If validation passes, calls Phase 2.10's `write_adapter_result` —
   which **re-validates immediately before writing** (its own internal
   call to `validate_adapter_result`), so validation happens a second
   time, right at the write boundary, not just once earlier in the
   command. This is intentional, not wasted duplication: the two calls
   share one validation function, so there is no second, divergent copy
   of the validation *logic* — only two calls to the same trusted
   function, the second timed to immediately precede the write.
4. `write_adapter_result` writes every non-empty lane through Phase
   2.7's `append_analysis_events`, one independent call per lane —
   the only code in this repository allowed to write a
   `tracks/*.jsonl` file, update `manifest.json`, or append a
   `receipts/analyze.jsonl` entry.
5. Prints a human-readable success summary (adapter name, package path,
   lanes written, event counts per lane, total events written, receipt
   path or "skipped" status, warning count) — or a clean error and a
   nonzero exit code on any refusal.

## Import does not write package internals directly

Exactly like Phase 2.10's writer bridge, `import-adapter-result` never
touches `manifest.json`, a `tracks/*.jsonl` file, `receipts/*.jsonl`,
`lock/*`, or `index.sqlite` itself. It only ever calls
`analysis_adapters.write_adapter_result`, which only ever calls
`analysis_writer.append_analysis_events` — this command adds no new
write path, and no new atomicity, lock-acquisition, or manifest-update
logic of its own.

## Writer bridge: reused as-is

`write_adapter_result` (Phase 2.10) was already correct for this
purpose and is reused unmodified:

- Refuses (raises `AnalysisAdapterError`, writes nothing) if the
  package does not exist or is not a directory.
- Refuses if `validate_adapter_result` reports any error.
- Refuses if every lane is empty (nothing to write).
- Refuses if the underlying Phase 2.7 writer itself refuses — most
  importantly, a currently-locked package (`lock_status() ==
  "locked"`), which `append_analysis_events` checks before writing
  anything for that lane.

Import's own pre-write dry-run check (step 2 above) catches a missing/
non-directory package and a validation failure *before* even attempting
the writer bridge; a locked package is caught by the writer bridge
itself, since lock state is a live, real-time property of the package
that could change between a dry run and the write attempt — checking it
twice (informationally, then authoritatively at write time) is more
honest than trying to predict it once and trust that prediction.

## Multi-lane behavior: per-lane atomic, not whole-result atomic

If an adapter result contains events in more than one lane, import
validates the **entire** result before writing anything — a validation
error in *any* lane refuses the *whole* import, and nothing is written
for any lane. Once validation passes, each non-empty lane is written
with its own independent call to `append_analysis_events`.

**This is per-lane atomic, not whole-result atomic**, and this phase
does not claim otherwise. Concretely: if a result has events in `lane_A`
and `lane_B`, and `lane_A`'s write succeeds but `lane_B`'s write then
fails (for example, because the package became locked in the instant
between the two calls, or `lane_B` has an event id that collides with
one already on disk), `lane_A`'s write is **not** rolled back. This
mirrors calling `analysis append` by hand once per lane, and is exactly
how Phase 2.10's `write_adapter_result` has behaved since it was
written — this phase adds no new cross-lane transaction boundary, and
is tested to prove no cross-lane atomicity is silently assumed anywhere
in the CLI layer either.

In practice, the most common cause of a mid-import failure (a package
becoming locked) can only happen *between* two lane-write calls in the
same command invocation if something else concurrently locks the
package while `import-adapter-result` is running — the pre-write dry
run cannot detect that race, and this phase makes no claim that it can.

## Receipt behavior

Import forwards adapter metadata into the receipt exactly the way
`write_adapter_result` already does, with **no change to the frozen
Phase 2.6/2.7 receipt schema**:

- `adapter_name`, `tool_name`, `tool_version`, `model_name`,
  `model_version`, `parameters`, `input_sources` are passed straight
  from `AdapterResult.metadata` into `append_analysis_events`.
- `adapter_version` (a Phase 2.10-only concept with no field in the
  frozen receipt dataclass) is folded into the writer's existing
  free-form `environment` dict, merged with `result.receipt_metadata`
  if present — unchanged from Phase 2.10.
- Lane event counts are visible per-lane in `AnalysisWriteResult.
  events_written`, printed in the CLI summary; nothing new is added to
  the on-disk receipt shape to carry them, since `receipts/analyze.jsonl`
  already records one entry per lane write.
- `--no-receipt` skips the receipt exactly as `analysis append`/
  `append-file` already support, via the same `write_receipt` flag
  threaded through unchanged.

## How it differs from Phase 2.11 dry-run

| | `dry-run-adapter` | `import-adapter-result` |
|---|---|---|
| Writes anything | Never | Yes, on success |
| Package required | Optional (`--package`) | Required (positional) |
| Locked package | Reported as a warning; exits 0 | Refused; exits 1 |
| Purpose | "Would this be accepted?" | "Commit this now." |

`import-adapter-result --dry-run` exists as a convenience and delegates
directly to the same `dry_run_adapter_result`/report-printing code the
standalone `dry-run-adapter` command uses — it does not reimplement or
weaken Phase 2.11's dry-run behavior, and with `--dry-run` given, import
writes nothing, exactly like the standalone command.

## How it uses Phase 2.7 writer/receipts

Unchanged: `append_analysis_events` (Phase 2.7) remains the sole source
of atomic per-lane writes (temp file + fsync + `os.replace`, lane track
then manifest then optional receipt, with rollback of earlier files if
a later write *within the same call* fails), manifest updates, receipt
writing, operation-lock acquisition, and locked-package refusal. This
phase adds no new implementation of any of these — it only adds a CLI
entry point that ends up calling the exact same function Phase 2.7,
2.8, and 2.10 already call.

## How Phase 2.9 package validation catches committed bad lanes

Unchanged: once import has written a lane, it is just another
`tracks/<lane>.jsonl` entry in `manifest.json`, indistinguishable to
`validate_package` from a hand-written or Phase 2.8-appended event.
`clulatent validate` still independently re-checks every track and
receipt regardless of whether import, a human, or a future adapter
produced them.

## How locks prevent import

A currently-locked package (`lock_status() == "locked"`) causes the
Phase 2.7 writer to refuse the very first lane write, before any file
is touched; import surfaces that refusal as a clean error message
("... has a valid integrity lock ...") and a nonzero exit code, with no
partial write for any lane — this is the exact same refusal path
`clulatent analysis append` has always used, reached through the same
writer.

## How review_events can later approve/reject/correct imported evidence

Unchanged: once an imported event exists as a canonical
`tracks/<lane>.jsonl` record, it is indistinguishable from any other
canonical event to `review_writer.py`. A `review_approval`,
`review_rejection`, or `review_correction` event can target it by id
exactly the same way it targets a human-authored, ingest-produced, or
Phase 2.8-appended event.

## Why this is not adapter runtime

No adapter is executed, discovered, or loaded. The only input is a
plain JSON file already sitting on disk, produced by some other process
entirely outside this phase's control. Nothing in this command can name,
look up, or invoke an adapter.

## Why this is not Studio UI

This phase adds one CLI subcommand and tests — no graphical interface,
no persistent server, no interactive review surface. `import-adapter-result`
is a single, stateless CLI invocation that validates, writes (or
refuses), prints a report, and exits.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
This phase's only new code is a CLI command function calling existing
Phase 2.7/2.10/2.11 functions.

## Non-goals (explicit)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime, subprocess execution, or
  external-tool import.
- No dynamic plugin loading or installed-adapter discovery.
- No new writer, validation, or lock-checking logic — every check is a
  direct call into Phase 2.6, 2.7, 2.10, or 2.11 code.
- No change to the frozen Phase 2.6 (`analysis_lanes.py`), Phase 2.7
  (`analysis_writer.py`), or Phase 2.10 (`analysis_adapters.py`)
  modules.
- No whole-result atomicity claim — multi-lane writes remain per-lane
  atomic, exactly as Phase 2.10 already documented.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.12 usage |
|---|---|---|
| 2.6 | `analysis_lanes.py` schema/shape checks | Reached indirectly via `validate_adapter_result`; never called or duplicated directly |
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | Reached indirectly via `write_adapter_result`; the sole writer of tracks/manifest/receipts |
| 2.8 | `clulatent analysis ...` CLI | Unmodified; `import-adapter-result` added as a new sibling command in the same `analysis_app` group |
| 2.9 | `validate_package` lane/receipt validation | Unmodified; still the authority on already-committed tracks |
| 2.10 | `analysis_adapters.py` contracts + `write_adapter_result` | Called directly, unmodified, as the only write path |
| 2.11 | `analysis_adapter_dry_run.py` | `load_adapter_result_json`/`dry_run_adapter_result`/`dry_run_is_hard_package_error` reused directly for loading and pre-write validation, plus the `--dry-run` flag |

## Files changed

- `src/clu_latent/cli.py` (new `clulatent analysis import-adapter-result`
  command; dry-run report printing extracted into a shared
  `_print_dry_run_report` helper, reused by both `dry-run-adapter` and
  `import-adapter-result --dry-run`)
- `tests/test_analysis_adapter_import.py` (new, 24 tests)
- `docs/PHASE_2_12_ANALYSIS_ADAPTER_RESULT_IMPORT.md` (new, this file)
- `README.md` (roadmap bullet added)

## Summary

Phase 2.12 lets a user commit an already-produced, already-dry-run-able
adapter result into a real package, using nothing but existing,
unmodified Phase 2.6/2.7/2.10/2.11 machinery: one CLI command that
loads JSON safely, validates before writing, writes through the one
writer this codebase has ever had, and reports honestly — per-lane
atomic, not whole-result atomic; locked packages refused, not silently
skipped; no new validation rule, no new write path. It does not make
CLULatent analyze media.
