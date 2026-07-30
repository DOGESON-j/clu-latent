# Phase 2.2: Review CLI Writer Commands

Status: **implemented**. Builds on the frozen Phase 2.1 Reviewed State
Resolver (`docs/PHASE_2_REVIEWED_STATE_RESOLVER.md`, tag
`phase-2.1-reviewed-state-resolver-freeze`).

## Scope of this phase

Implemented:

- `src/clu_latent/review_writer.py`: `append_review_event(package_path,
  event_type, ...)` — the first thing in CLULatent that actually writes
  a review event to `tracks/review_events.jsonl`. Builds one
  `EventEnvelope` via the matching Phase 2.0 factory in `review.py`,
  appends it to the existing (possibly empty/absent) review track, and
  atomically rewrites both the track file and `manifest.json`'s
  `review_events` `TrackDescriptor`.
- `clulatent review <subcommand> <package> [event-id] [options]`: six
  CLI commands — `approve`, `reject`, `correct`, `override`, `status`,
  `note` — each a thin wrapper around `append_review_event`, guarded by
  the operation lock exactly like `reindex`/`lock` already are.
- Tests: `tests/test_review_writer.py` (28 tests: factory-backed happy
  paths for every subcommand, track-creation vs. append bookkeeping,
  source-track immutability, post-write `validate`/`review-state`
  round-trips, locked-package refusal, operation-lock contention,
  invalid-event-id/reviewer-id/payload rejection, nonexistent-package and
  out-of-range-confidence error paths (both API- and CLI-level), rollback
  behavior under an injected manifest-write failure, and the equivalent
  CLI-level checks via `CliRunner`).

Not implemented this phase (explicitly out of scope):

- No Studio UI, no caption/diarization/identity runtime, no
  `semantic_events` generation.
- No destructive edits to any existing track record — every write here
  is a pure append to `review_events.jsonl`; every other track is only
  ever read.
- No `reviewed_truth` (or any other new) track, no auto-merging of
  conflicting review history — resolving *effective* state is still
  entirely `review_resolver.py`'s (Phase 2.1) job, unaffected by this
  phase.
- No cryptographic signatures, no cloud/collaboration/accounts, no
  CLUBIN.
- No `unlock`/silent-relock workflow: a package with a currently-valid
  integrity lock (`lock_status() == "locked"`) is refused outright, with
  no `--force` escape hatch. The only supported way to add review
  events to a locked package is to lock/verify/write in the order the
  operator chooses outside this tool — this phase never bypasses or
  silently replaces `lock/package.lock.json`.

## Two independent write guards

`append_review_event` and every `clulatent review ...` command are
guarded by two separate, independently-motivated mechanisms — neither
is a substitute for the other:

1. **Integrity lock** (`lock/package.lock.json`, see `lock.py`,
   Phase 1.7.5). Checked via `lock_status()`. If it reports `"locked"`
   (files present *and* currently verifying clean), the write is
   refused before touching anything on disk. `"unlocked"`,
   `"lock-invalid"`, and `"lock-partial"` are all allowed to proceed —
   only a currently-valid lock blocks a write, matching the instruction
   ("if package has a valid package.lock.json, review write commands
   must refuse by default"). There is no `--force` flag for this
   check and no unlock command anywhere in CLULatent; re-establishing a
   lock after a review write is exactly the same `clulatent lock`
   workflow used after any other canonical-file change.
2. **Operation lock** (`lock/package.operation.lock.json`, see
   `security/operation_lock.py`, Phase 1.7.5). `review_writer.py` does
   not acquire this itself — the `clulatent review ...` CLI commands
   wrap their call to `append_review_event` in `operation_lock(...)`,
   the identical pattern `reindex`/`lock` already use, complete with
   `--force-stale-lock` for clearing a lock left behind by a crashed
   process.

## Targeting a source event

Every subcommand except a source-less `note` takes a positional
`event-id` argument. `append_review_event` resolves it by scanning
every canonical track in the manifest *except* `review_events` (in
manifest order) for a record whose `id` matches; the first match wins.
If no track contains it, the write is refused
(`ReviewWriteError: ... was not found in any canonical (non-review)
track of this package`) before anything else happens — no partial
state, no guessing. The matched event's own `t_start_ms`/`t_end_ms`
become the new review event's envelope span (a review event's time
range is defined as "the span of the thing it is reviewing"); a
source-less `note` (`--event-id` omitted) gets `t_start_ms=t_end_ms=0`
and an empty `source_event_ids`, exactly like `human_note`'s existing
"whole-package note" support in `review.py`.

## Building the new event

Reuses the Phase 2.0 factories in `review.py` unchanged — this phase
adds no new event shape, no new validation rule beyond what
`validate_review_track` already enforces. `reviewer_id` (CLI default
`local`, i.e. `producer.name` becomes `human:local`) is passed straight
through the factory, which is exactly where every bound/shape check
(length, control characters, NUL bytes) already lives; a rejected
`reviewer_id` surfaces as a plain `ReviewWriteError`/CLI error with no
partial write.

`index` (the numeric suffix in `rv_<type>_{index:06d}`) is computed by
scanning the existing review track for the highest existing index
*already used by that exact type* and adding one — not a single global
counter — so ids stay type-namespaced exactly as `review.py`'s module
docstring describes, and re-running the same command twice never
collides even if earlier writes were interleaved with hand-edits.

## Validate-before-write

Before either file on disk is touched, the full candidate list
(existing review events + the new one) is run through
`validate_review_track` (frozen from Phase 2.0, unchanged). Any error —
a bad `review_state`, a `review_correction` whose payload key sets
don't match, a `review_override` missing `override_kind`, a would-be
supersedes cycle, anything — aborts the write with a
`ReviewWriteError` and leaves the package byte-for-byte unchanged. This
is the "fail safely before final write" requirement: validation and
writing are two distinct steps, in that order, never interleaved.

## Atomic, ordered writes

Two files change on a successful write: `tracks/review_events.jsonl`
(rewritten in full — sorted by `t_start_ms`, same as `tracks.py`'s
`write_track_file` — since the track format has no true streaming
append) and `manifest.json` (its `review_events` `TrackDescriptor`
replaced with an updated `record_count`, or added for the first time).
Both are written via a private `_atomic_write` helper — temp file next
to the target + `fsync` + `os.replace` — mirroring `lock.py`'s existing
private `_atomic_write`, so neither file is ever observed half-written.

Write order is fixed: **track file first, manifest second**. If the
manifest write fails after the track file succeeded, the track file is
rolled back (restored to its pre-write bytes, or deleted if it did not
exist before) on a best-effort basis before the original exception is
re-raised — so a crash between the two writes is biased towards
leaving the package exactly as it was, rather than leaving
`review_events.jsonl` silently ahead of what `manifest.json` declares.
This does not need process-level atomicity guarantees stronger than
what a single `.jsonl`/`.json` write already gets elsewhere in this
codebase (see `lock.py`'s own two-file lock.json/lock.sha256 rollback
for the same pattern).

No other canonical file (source media, any other `tracks/*.jsonl`,
`receipts/*.jsonl`) is ever opened for writing by this module.

## CLI commands

```
clulatent review approve   <package> <event-id> [--reviewer-id ID] [--reviewer-label L] [--reason R] [--certainty C] [--reviewed-at TS]
clulatent review reject    <package> <event-id> [--reason R] [...]
clulatent review correct   <package> <event-id> --original-payload-json JSON --corrected-payload-json JSON [...]
clulatent review override  <package> <event-id> --override-kind KIND --state corrected|rejected --original-payload-json JSON --corrected-payload-json JSON [...]
clulatent review status    <package> <event-id> --state STATE [...]
clulatent review note      <package> --note TEXT [--event-id ID] [...]
```

Every subcommand also accepts `--force-stale-lock`, forwarded to the
operation lock exactly like `reindex`/`lock`. `--original-payload-json`
/ `--corrected-payload-json` are parsed with `json.loads`; malformed
JSON or a non-object value is rejected by the CLI before
`append_review_event` is ever called (exit code 1, nothing written).
`clulatent review-state <package>` (Phase 2.1, read-only) is untouched
and immediately reflects any review written by these commands, since it
recomputes from the same on-disk track on every invocation.

## Lock behavior

Unchanged for every *other* command. This phase does not touch
`reindex.py`, `index.py`, or `lock.py`'s own create/verify logic — it
only *reads* `lock_status()` as a precondition before writing. A
package's integrity lock, once created, is never silently invalidated,
replaced, or bypassed by any command in this phase.

## Files changed

- `src/clu_latent/review_writer.py` (new).
- `src/clu_latent/cli.py`: added the `clulatent review` subcommand
  group (`approve`, `reject`, `correct`, `override`, `status`, `note`).
- `tests/test_review_writer.py` (new).

## What Phase 2.3+ still need to build

- Any consumer (inspector/viewer, Studio UI) that lets a human choose
  what to review and calls these commands (or their underlying Python
  API) on their behalf — this phase is CLI/API only.
- Batch/session-level writing (`review_session_summary` has no writer
  command yet — only the five judgment types plus `human_note` do).
- Any policy for *automatically* re-running `clulatent lock` after a
  review write; today that remains a manual, deliberate step.
