# Phase 2.11 — Analysis Adapter Dry-Run Harness

Base: Phase 2.10 Analysis Adapter Contracts is frozen
(tag `phase-2.10-analysis-adapter-contracts-freeze`).
New module: `src/clu_latent/analysis_adapter_dry_run.py`.
New CLI command: `clulatent analysis dry-run-adapter`.

## Scope

This phase adds a conservative dry-run harness for **already-produced**
adapter results. It lets a developer or future adapter author check
whether a candidate `analysis_adapters.AdapterResult` (loaded from a
plain JSON file) is structurally valid, which lanes it would write, how
many events per lane, what warnings/errors exist, and whether a given
package would currently accept the write — **without writing anything**.

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

A *dry run* checks whether a candidate result is eligible to become
canonical — it never makes it canonical itself. Only
`analysis_writer.append_analysis_events` (Phase 2.7), reached either
directly, through `analysis_adapters.write_adapter_result` (Phase 2.10),
or through `clulatent analysis append`/`append-file` (Phase 2.8), ever
writes a track, a manifest, or a receipt.

## What a dry run does

1. Safely parses a candidate adapter-result JSON file
   (`load_adapter_result_json`): bounded read (reuses
   `security.jsonl.read_bytes_bounded`), UTF-8 decode, `json.loads`, and
   a top-level-must-be-an-object check. Any of these failing raises a
   single, clean `AdapterResultLoadError` — never a raw
   `json.JSONDecodeError`, `OSError`, or `UnicodeDecodeError`.
2. Builds a candidate `AdapterResult`/`AdapterMetadata`/
   `AdapterDeclaredOutputs` from the parsed dict, passing every field
   through as close to verbatim as possible (a missing key gets a
   default; a present-but-wrong-typed value is preserved unchanged) so
   the *validator* — not the loader — reports the specific problem.
3. Validates the candidate with `analysis_adapters.validate_adapter_result`
   (Phase 2.10) — the exact same function `write_adapter_result` calls
   before writing anything. No validation rule is reimplemented here.
4. If a `--package` was given, checks (read-only) whether it exists, is
   a directory, and its lock status (`lock.lock_status`, Phase 1.7.5+),
   and reports one of: skipped, not found, not a directory, locked,
   lock-invalid, lock-partial, or writable.
5. Reports lanes present, per-lane and total event counts, warnings,
   errors, package status, and whether the result would currently be
   eligible for writing (`would_write`) — then stops. Nothing is ever
   written.

## What a dry run does not do

- Does not run an adapter. There is no adapter execution, subprocess
  invocation, or media analysis anywhere in this module — it only reads
  a JSON file that some other process (human or future adapter) already
  produced.
- Does not analyze media. No video/audio file is ever opened by this
  phase.
- Does not write package files. `dry_run_adapter_result` never calls
  `analysis_writer.append_analysis_events` or
  `analysis_adapters.write_adapter_result`; package writability is
  determined purely by read-only filesystem/lock inspection
  (`Path.exists()`, `Path.is_dir()`, `lock.lock_status()`).
- Does not create canonical truth. A `valid`/`would_write` dry-run
  report is an eligibility check, not a commit — the candidate result
  remains just as untrusted after a passing dry run as before it. Only
  the Phase 2.7 writer, actually invoked, produces a canonical track.

## How it uses Phase 2.10 contracts

`dry_run_adapter_result` calls `analysis_adapters.validate_adapter_result`
directly — the same function `write_adapter_result` calls immediately
before writing. Every shape, bound, path-safety, and identity-claim
check this phase reports is Phase 2.10's, not a reimplementation.

## How it relies on Phase 2.6 lane schemas

`validate_adapter_result` in turn calls
`analysis_lanes.validate_analysis_track` once per non-empty lane —
unsupported lane names, invalid event shapes, out-of-range confidence,
unsafe payload paths, and identity claims in object/tracking-lane
payloads are all caught there, exactly as they are for a hand-typed
event or a real adapter write. This phase adds no lane-schema logic of
its own.

## How a passing dry run differs from a Phase 2.7 writer commit

A passing dry run (`report.valid is True`, `report.would_write is True`)
means the candidate result satisfies every check
`analysis_writer.append_analysis_events` would apply *at the moment the
dry run ran*. It does not mean:

- the package is still unlocked or unchanged by the time a real write is
  attempted afterward (a dry run takes no lock and holds no reservation);
- a track, manifest, or receipt now exists — none of the three has been
  touched;
- the result has become canonical. Only an actual writer call
  (`write_adapter_result` or `clulatent analysis append`/`append-file`)
  produces a canonical, receipted, on-disk record.

## How Phase 2.8 CLI can manually write events

Unchanged: `clulatent analysis append`/`append-file` remain the CLI-level
path for actually writing already-produced evidence, including evidence
that has just been dry-run-checked. A passing `dry-run-adapter` result
is a strong signal that a subsequent `append`/`append-file` (or a future
adapter's own `write_adapter_result` call) would succeed, but it does
not perform that write itself, and nothing links the two calls together
— the package can change state in between.

## How Phase 2.9 package validation catches committed bad lanes

Unchanged: if a result is ever written despite a bad dry run being
ignored, or the package changes between a dry run and a real write,
`clulatent validate`/`validate_package` (Phase 2.9) still independently
re-checks every track and receipt on disk. This phase is a pre-write
convenience, not a replacement for that check.

## How locks would prevent writes

A locked, lock-invalid, or lock-partial package is reported by the dry
run (`package_status`) using the exact same `lock.lock_status` function
`analysis_writer.append_analysis_events` checks before writing — the dry
run predicts the same refusal the writer would give, without attempting
the write.

**Exit-code design decision (explicit, as required):** a locked/
lock-invalid/lock-partial package status does **not** make the
`dry-run-adapter` CLI command exit nonzero. Only two things do:
structural validation errors in the adapter result itself
(`report.errors` non-empty), or a hard package-path problem (package
not found, or not a directory). This is a deliberate choice: a dry run
answers "is this candidate result valid," which is orthogonal to "is
this particular package writable right now." A locked package is
reported clearly in the console output (`package status: locked —
write would be refused`) and reflected in `would_write: False`, but the
adapter result itself can still be perfectly valid — so the command
still exits `0`. This is tested explicitly
(`test_cli_dry_run_adapter_locked_package_exit_code_is_explicit`,
`test_dry_run_with_locked_package_reports_would_refuse`).

## Why this is not adapter runtime

No adapter is executed, discovered, or loaded by this phase. The only
input is a plain JSON file already sitting on disk; the only outputs are
a `DryRunReport` (in-process) and a printed console report — no code
path here can name, look up, or invoke an adapter.

## Why this is not Studio UI

This phase adds a Python module, one CLI subcommand, and tests. There is
no graphical interface, no persistent server, no interactive review
surface — `dry-run-adapter` is a single, stateless, read-only CLI
invocation that prints a report and exits.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
`DryRunReport` is a plain in-process dataclass for this module's own
callers (the CLI command, or a future test harness) — not a file format
or a data model backing any external tool.

## Package-root handling (implementation note)

`security.paths.resolve_in_package` (used transitively by
`validate_adapter_result` for path-safety checks) calls
`Path(package_root).resolve(strict=True)`, which raises a raw
`FileNotFoundError` for a package that does not exist. To keep this
module's own errors clean (never a raw traceback), a `package_path` is
only ever passed through to `validate_adapter_result` as `package_root`
once it has already been confirmed to exist and be a directory; a
missing or non-directory package is instead reported as its own
`package_status`, and validation still runs without a `package_root`
(lexical-only path safety) — exactly like `clulatent analysis
validate-file` already does.

## Non-goals (explicit)

- No FFmpeg tracker, OCR runtime, object detector, or ML model
  dependency.
- No adapter execution runtime, subprocess execution, or
  external-tool import.
- No dynamic plugin loading or installed-adapter discovery.
- No writing of any kind: no track, manifest, receipt, lock file, or
  index is ever touched.
- No change to the frozen Phase 2.6 (`analysis_lanes.py`), Phase 2.7
  (`analysis_writer.py`), or Phase 2.10 (`analysis_adapters.py`)
  modules.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.11 usage |
|---|---|---|
| 2.6 | `analysis_lanes.py` schema/shape checks | Reached indirectly via `validate_adapter_result`; never called or duplicated directly |
| 2.7 | `analysis_writer.py` atomic, lock-aware writer | Never called — a passing dry run predicts what it *would* do, not what it did |
| 2.8 | `clulatent analysis ...` CLI | Unmodified; `dry-run-adapter` added as a new sibling command in the same `analysis_app` group |
| 2.9 | `validate_package` lane/receipt validation | Unmodified; still the authority on already-committed tracks |
| 2.10 | `analysis_adapters.py` contracts | `validate_adapter_result`, `AdapterMetadata`, `AdapterResult`, `AdapterDeclaredOutputs` reused directly, not duplicated |
| 1.7.5+ | `lock.py` lock detection | `lock_status` reused directly for read-only package-writability reporting |

## Files changed

- `src/clu_latent/analysis_adapter_dry_run.py` (new)
- `src/clu_latent/cli.py` (new `clulatent analysis dry-run-adapter` command)
- `tests/test_analysis_adapter_dry_run.py` (new, 33 tests)
- `docs/PHASE_2_11_ANALYSIS_ADAPTER_DRY_RUN_HARNESS.md` (new, this file)
- `README.md` (roadmap bullet added)

## Summary

Phase 2.11 gives a future adapter author a safe test bench: a way to
check, before ever touching a real package, whether a candidate result
is structurally valid, what it would write, and whether a given package
would currently accept it — reusing Phase 2.10's validation and Phase
1.7.5's lock detection verbatim, adding no new validation rule beyond
"is this package a safe target right now." It does not make CLULatent
analyze media. It does not commit adapter output.
