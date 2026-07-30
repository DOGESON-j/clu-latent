# Phase 2.3: Safe Reviewed-Package Workflow

Status: **design/workflow document only — no code changes**. Builds on
the frozen Phase 2.2 Review CLI Writer Commands
(`docs/PHASE_2_REVIEW_CLI_WRITER_COMMANDS.md`, tag
`phase-2.2-review-cli-writer-commands-freeze`), and on the locking
workflow already sketched in `docs/LOCKING.md`'s "How locking interacts
with human review" section (written when Phase 1.9 was still
design-only). This phase does not change that section's conclusion — it
formalizes and extends it now that `clulatent review ...` actually
writes review events, and answers the concrete operational question:

> If a `.clulatent` package is locked, and a human needs to add review
> corrections, what is the safe process?

## Scope of this phase

Implemented:

- This document (`docs/PHASE_2_3_REVIEWED_PACKAGE_WORKFLOW.md`),
  describing the end-to-end safe review lifecycle for both unlocked and
  locked packages, using only commands that exist today
  (`clulatent review ...`, `clulatent validate`, `clulatent lock`,
  `clulatent verify-lock`, `clulatent lock-status`) plus manual
  filesystem copy.
- A short Phase 2.3 roadmap note in `README.md`, cross-referencing this
  document.

Not implemented this phase (explicitly out of scope, per the Phase 2.3
implementation instruction):

- No new CLI command (no `copy-unlocked`, no `unlock`, no
  `relock`/`review-commit` convenience wrapper). Every workflow below
  is expressed in terms of commands that already exist today, plus
  ordinary filesystem copy (`cp -r` / `shutil.copytree`), exactly as
  `docs/LOCKING.md` already recommends for "editing a locked package."
- No change to `lock.py`, `review_writer.py`, `review_resolver.py`, or
  any other module. This phase adds zero lines of application code.
- No unsafe unlock behavior, no silent modification of locked packages,
  no silent replacement of an existing lock. `clulatent lock --force`
  remains the only way to replace a lock, and it is always an explicit,
  deliberate, separately-invoked step — never triggered automatically
  by a review write.
- No mutation of source tracks, no `reviewed_truth` (or any other new)
  track, no automatic merging of conflicting review history into a
  single "winning" truth — unchanged from Phase 2.0/2.1's core
  principle, restated below.
- No CLUBIN, no Studio UI, no export format, no cryptographic
  signatures, no cloud collaboration, no user accounts, no destructive
  editing.

## Core principle (unchanged, restated)

Review is additive evidence, never a destructive edit. This has held
since Phase 1.9's design and every implemented phase since (2.0, 2.1,
2.2), and this document does not relax it anywhere:

- A review correction never overwrites the source event it corrects —
  it is a new record in `tracks/review_events.jsonl` that *references*
  the source event's id and carries its own `original_payload`/
  `corrected_payload`. The source event's own record, in its own
  track, is byte-for-byte unchanged.
- "Reviewed truth" is never stored as its own track. It is computed at
  read time by `review_resolver.py` (Phase 2.1) by folding
  `review_events.jsonl` over the package's other tracks. This document
  does not introduce a `reviewed_truth` track or any other persisted
  resolution — the *lifecycle* around locking and re-locking described
  below is orthogonal to, and does not change, how resolution works.
- An integrity lock, once created, is never silently invalidated,
  replaced, or bypassed. Every workflow below either (a) refuses to
  write against a locked package (already Phase 2.2's behavior, see
  "Two independent write guards" in
  `docs/PHASE_2_REVIEW_CLI_WRITER_COMMANDS.md`), or (b) works on a
  fresh copy and re-locks that copy *explicitly*, never the original.

## Workflow 1: unlocked package review

The common case, and the one Phase 2.2 was built directly for. No
special handling is needed:

```bash
clulatent lock-status pkg.clulatent      # confirm: unlocked
clulatent review approve pkg.clulatent ts_000000 --reviewer-id alice
clulatent review correct pkg.clulatent ts_000001 \
  --original-payload-json '{"text": "helo"}' \
  --corrected-payload-json '{"text": "hello"}'
clulatent review-state pkg.clulatent     # confirm the write is reflected
```

`clulatent review ...` (Phase 2.2) checks `lock_status()` itself before
every write; on an `"unlocked"` package (or one whose lock is
`"lock-invalid"`/`"lock-partial"` — see "Two independent write guards"
in the Phase 2.2 doc for why those two are treated as *not* blocking)
the write proceeds directly, in place, on the package the human is
already looking at. There is no copy step in this workflow — copying
before review is only needed to protect a **currently-valid** lock
(Workflow 2/3), not as a general precaution.

## Workflow 2: locked package review (refuse-first, by design)

If `clulatent lock-status pkg.clulatent` reports `"locked"`, every
`clulatent review ...` subcommand refuses outright — this is existing,
frozen Phase 2.2 behavior, not new in this phase:

```bash
$ clulatent review approve pkg.clulatent ts_000000
Error: package is locked (lock/package.lock.json is present and
currently verifies clean); review writes are refused. ...
```

There is deliberately **no** `--force` escape hatch for this specific
check (see Phase 2.2 doc, "Two independent write guards", point 1) and
this phase does not add one. The safe process for a locked package is
always the same three steps, in this order:

1. **Copy** the locked package to a new working location (Workflow 3).
2. **Review** the copy using the exact same `clulatent review ...`
   commands as Workflow 1 — the copy is unlocked from the moment
   `lock/` is dropped, so nothing about the write path differs.
3. **Re-lock** the reviewed copy as a distinct, new lock (Workflow 5).

The original locked package is never touched by any of these three
steps. It remains exactly as it was, its lock still valid, still
verifiable with `clulatent verify-lock`, for as long as the operator
chooses to keep it around — which is also why this is the *auditable*
half of the workflow: the original lock is untouched evidence of "what
this package looked like before review," independent of whatever the
reviewed copy becomes.

## Workflow 3: copy-before-review

There is no `clulatent copy-unlocked` command today (see
`docs/LOCKING.md`, "Why there is no `unlock` command" — a dedicated
command is called out there as "a natural future addition" but still
not implemented). The manual equivalent, unchanged from what
`docs/LOCKING.md` already documents:

```bash
cp -r pkg.clulatent pkg.clulatent.review-copy
rm -rf pkg.clulatent.review-copy/lock
clulatent lock-status pkg.clulatent.review-copy   # confirm: unlocked
```

Why plain filesystem copy is safe here, and why this phase does not
try to replace it with a CLI command:

- Every canonical file in a `.clulatent` package (`manifest.json`,
  `sources/*`, `tracks/*.jsonl`, `receipts/*.jsonl`) is a plain file on
  disk — copying the directory tree copies exactly the package's full
  canonical state, with no hidden side channel a copy could miss.
  `index/search.sqlite` is derived and safe to copy or drop either way
  (`clulatent reindex` rebuilds it from canonical tracks if dropped or
  stale).
- Removing `lock/` from the copy is the *only* structural change this
  step makes, and it is exactly what `lock_status()` needs to see to
  report `"unlocked"` for the copy — `lock_status()` looks only at
  whether `lock/package.lock.json` and `lock/package.lock.sha256`
  exist next to the package being checked, nothing about the copy's
  provenance.
- The original is never opened for writing during this step. `cp -r`
  only reads the original and writes the new location.

A future `copy-unlocked` command remains a reasonable later addition
(see "What commands may be added later" below) — it would only ever be
a safer, more convenient wrapper around exactly this sequence (copy,
then drop `lock/`), not a new capability.

## Workflow 4: validate-after-review

Whether the reviewed package started out locked (via the copy) or
unlocked (in place), `clulatent validate` should be run again after
review, before re-locking:

```bash
clulatent validate pkg.clulatent.review-copy
```

This is not a new requirement invented by this phase — `validate`
already re-runs the full canonical-file check, including
`review.validate_review_track` (wired into `validate_package` since
Phase 2.0) whenever a `review_events` track is present. Two things this
step is specifically useful for catching before locking in a possibly
bad state:

- Any review-track-local issue `append_review_event`'s own
  validate-before-write step (Phase 2.2, "Validate-before-write") did
  *not* already reject at write time — none is currently known, since
  every `clulatent review ...` write already runs the identical
  `validate_review_track` check before touching disk, but re-running
  `validate` after a *sequence* of several review writes (some of which
  may have used `--force-stale-lock` to clear a crashed process's
  operation lock) is a cheap, independent confirmation that the whole
  track is still internally consistent, not just the most recent write.
- General package health (manifest/track-descriptor consistency,
  record-count agreement, etc.) unrelated to review specifically —
  `validate` was already the recommended step after any canonical-file
  change, per the existing `docs/LOCKING.md` "Recommended workflow".

`validate` never writes anything — this step is purely a read-only gate
before the next (destructive-to-the-lock-state, though not to any
canonical file) step.

## Workflow 5: re-lock

Once the reviewed copy validates cleanly, lock it as its own, new,
independent lock:

```bash
clulatent lock pkg.clulatent.review-copy
clulatent verify-lock pkg.clulatent.review-copy
```

No `--force` is needed here, since the copy has no `lock/` directory at
all (dropped in Workflow 3) — `clulatent lock` only requires `--force`
to *replace* an existing lock, and there isn't one on the copy. This
matches `docs/LOCKING.md`'s existing guidance verbatim (step 3 of "How
locking interacts with human review"): the new lock covers "the
original model output *and* the review layer together," as a single,
fresh `package.lock.json`/`package.lock.sha256` pair, with its own
independent timestamp and file list — it does not reference, chain
from, or invalidate the original package's lock in any way. The two
locks (original and reviewed-copy) are two separate, independently
auditable pieces of tamper-evidence, exactly matching two separate
directories on disk.

If the operator later revises review further (more corrections,
additional approvals) on the same reviewed copy, that copy is now
itself a locked package — Workflow 2 applies to it recursively: copy
again, review, validate, re-lock. Nothing in this document special-cases
"a copy of a copy"; the same five workflows apply uniformly at any
depth.

## How review-state fits in

`clulatent review-state <package>` (Phase 2.1) is read-only and
requires no special handling at any point in this lifecycle:

- It works identically on a locked or unlocked package — it never
  checks `lock_status()` at all (see Phase 2.1 doc, "Lock behavior":
  "unaffected by running the resolver ... at any point, any number of
  times").
- Run it on the *original* locked package at any time to see its
  current (possibly `unreviewed`-only, if review hasn't started)
  resolved state, with zero risk — it cannot write anything.
- Run it on the reviewed copy, before or after re-locking, to confirm
  the review writes accumulated in Workflow 2 resolve the way the
  operator expects, before committing to Workflow 5's re-lock. This is
  the recommended check between Workflow 2 and Workflow 4: cheaper than
  full `validate`, and answers a different question ("what does this
  resolve to?" vs. "is this internally consistent?").
- After re-locking, `review-state` continues to work exactly the same
  way — locking a package never changes what `review-state` reports,
  since both `review_resolver.py` and `lock.py` only ever *read*
  `review_events.jsonl`; neither writes to it.

## How receipts should record review operations

Today, `receipts/ingest.jsonl` (`receipts.py`) is the only receipts
file any command writes, and it is scoped to `ingest` specifically —
`ReceiptLog` is constructed and flushed once, inside `ingest.py`, and
no other command (`lock`, `reindex`, and, since Phase 2.2, `review ...`)
writes to it or to any receipts file of its own. This phase does not
change that. Two facts are worth recording precisely because a future
phase may want to close this gap, and this section defines the target
shape now so that future work has a specific, considered design to
implement rather than an open question:

- **What already makes review operations auditable without a receipt.**
  Every `clulatent review ...` write already appends a fully-attributed
  `EventEnvelope` to `tracks/review_events.jsonl` itself —
  `reviewer_id`, `reviewed_at`, `producer.name` (`human:<reviewer_id>`),
  and (for `review_correction`/`review_override`) the exact
  `original_payload`/`corrected_payload` diff are all already permanent,
  on-disk, lockable record fields. In this sense `review_events.jsonl`
  *is already* the audit log for review operations — a separate
  receipts entry would be redundant with data the track already carries
  for every successful write.
- **What a receipt would add that the track alone doesn't.** The one
  gap: `review_events.jsonl` has no records at all for *failed* or
  *refused* review-write attempts (locked-package refusal, operation-
  lock contention, invalid-payload rejection) — by construction,
  nothing is appended to the track when `append_review_event` raises.
  A future `receipts/review.jsonl` (mirroring `receipts/ingest.jsonl`'s
  existing shape: `operation`, `status`, `errors`, `warnings`,
  `timestamp`, tool version) is the natural place to record *attempts*,
  successful or not — giving an operator a way to see "someone tried to
  review this locked package and was refused at 14:32" even though
  nothing was written to any track. This is explicitly deferred, not
  implemented here (see "What commands may be added later").
- **Locking already covers `receipts/*.jsonl` generically.** `lock.py`
  globs every file under `receipts/` (see `docs/LOCKING.md`, "Files
  covered"), so a future `receipts/review.jsonl` would automatically be
  covered by Workflow 5's re-lock step with no change to `lock.py`
  needed — worth noting now so a future implementer doesn't need to
  re-derive it.

## What the CLI currently supports

Summary of the exact command surface every workflow above is built
from — nothing here is new in this phase:

| Command | Reads/writes lock? | Notes |
|---|---|---|
| `clulatent lock-status <pkg>` | read-only | unlocked / locked / lock-invalid / lock-partial |
| `clulatent review approve\|reject\|correct\|override\|status\|note <pkg> ...` | refuses if locked; else appends | Phase 2.2, always append-only to `review_events.jsonl` |
| `clulatent review-state <pkg>` | read-only | Phase 2.1, works regardless of lock state |
| `clulatent validate <pkg>` | read-only | includes `validate_review_track` whenever the track exists |
| `clulatent lock <pkg> [--force]` | writes lock | `--force` only needed to *replace* an existing lock |
| `clulatent verify-lock <pkg> [--strict]` | read-only | confirms a lock still matches current bytes |

Copy-before-review (Workflow 3) is manual (`cp -r` + `rm -rf lock/`) —
not a CLI command today.

## What commands may be added later

None of the following are implemented by this phase; each is called
out here only because this document's workflows motivate it as a
plausible, bounded future addition — not a commitment:

- **`clulatent copy-unlocked <pkg> <dest>`**: a safer, atomic wrapper
  around Workflow 3 (copy + drop `lock/`) — mirroring `docs/LOCKING.md`'s
  own "natural future addition" note. Would need the same atomic-
  staging-then-rename discipline `ingest` already uses, so a crash
  mid-copy can't leave a half-copied `dest` that looks superficially
  valid.
- **A `clulatent review commit` (or similarly named) convenience
  command** that chains validate-then-lock (Workflows 4+5) in one
  invocation, purely as a convenience over running both commands
  separately — it would not skip either check, just sequence them.
- **`receipts/review.jsonl`**, as described above, recording every
  review-write *attempt* (including refusals), independent of what
  ends up in `review_events.jsonl` itself.
- **A `clulatent diff-lock <original> <reviewed-copy>` (or similar)**
  command that explicitly reports "these two packages share the same
  pre-review canonical files except for `tracks/review_events.jsonl`
  and `manifest.json`'s `review_events` descriptor" — today an operator
  can reconstruct this manually (`verify-lock` the copy against the
  original's `package.lock.json` in `--strict` mode already reports
  `review_events.jsonl` as an "extra" file — see `docs/LOCKING.md`,
  "How locking interacts with human review" — but there is no single
  command that packages this comparison up more legibly).

None of these are required for the workflows in this document to be
safe today — they are documented as *possible* future conveniences, not
as gaps that make the current manual process unsafe.

## Non-goals

Restated explicitly, matching the Phase 2.3 implementation instruction:

- No CLUBIN.
- No Studio UI.
- No export format.
- No cryptographic signatures.
- No cloud collaboration.
- No user accounts.
- No destructive editing — every workflow above is either purely
  read-only or purely additive (a new review event, a new lock on a
  new copy).
- No `reviewed_truth` track — resolution remains Phase 2.1's read-time
  job, unaffected by anything in this document.
- No automatic truth merging — conflicting review history is still
  surfaced via `has_conflict` (Phase 2.1), never auto-resolved.

## Files changed

- `docs/PHASE_2_3_REVIEWED_PACKAGE_WORKFLOW.md` (new, this document).
- `README.md`: short Phase 2.3 roadmap note.

## What Phase 2.4+ still need to build

- Any of the "commands that may be added later" above, if a future
  phase decides the manual steps in this document are worth automating.
- Any consumer (inspector/viewer, Studio UI) that surfaces this
  workflow to a human as guided steps rather than documented CLI
  invocations.
