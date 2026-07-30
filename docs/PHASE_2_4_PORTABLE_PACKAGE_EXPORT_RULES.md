# Phase 2.4: Portable Package Export Rules

Status: **design/spec document only — no code changes**. Builds on the
frozen Phase 2.3 Safe Reviewed-Package Workflow
(`docs/PHASE_2_3_REVIEWED_PACKAGE_WORKFLOW.md`, tag
`phase-2.3-reviewed-package-workflow-freeze`), and on the path-security
and locking rules already implemented in `security/paths.py` (Phase
1.x) and `lock.py` (Phase 1.7.5). This phase does not change any of
that code — it defines, in one place, the rules a `.clulatent` package
must already satisfy (or must come to satisfy, for two named future
profiles) to be safely moved, copied, archived, shared, and reopened
by a different tool invocation, on a different machine, at a different
time, without losing trust or breaking.

> What makes a `.clulatent` package portable without losing trust,
> breaking paths, or accidentally including unsafe/generated junk?

## Scope of this phase

Implemented:

- This document, defining portability rules against the *existing*
  package format (nothing here requires a schema change to be true
  today) and specifying two **future** export profiles — reference and
  embedded — as named, not-yet-implemented targets for later phases.
- A short Phase 2.4 roadmap note in `README.md`, cross-referencing this
  document.

Not implemented this phase (explicitly out of scope, per the Phase 2.4
implementation instruction):

- No `clulatent export` (or similarly named) CLI command. Every rule
  below is either already true of the current single-source, embedded-
  only package format, or is scoped as future work for whichever phase
  actually implements reference-mode source media.
- No new manifest fields, no `storage_mode` value other than the
  existing `"embedded"` (see `SourceInfo.storage_mode` in
  `manifest.py`, currently a `Literal["embedded"]`) — reference mode is
  specified here as a *target shape*, not implemented.
- No binary/compressed container format, no CLUBIN, no cloud upload, no
  encryption, no signatures beyond the existing hash-based lock.
- No change to `lock.py`, `validate.py`, `security/paths.py`, or any
  other module.

## Core principle

A `.clulatent` package is already, by construction, a plain directory
of plain files with no absolute paths, no external references, and a
self-describing manifest — this was a Phase 1 design goal (see
`docs/spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md` §5, "Package-relative
POSIX paths"), not something this phase introduces. Portability, as
defined here, is mostly a matter of **naming which of those existing
properties an operator can already rely on**, plus flagging the small
number of files (the operation lock, the derived index) that must be
handled deliberately during a move. This phase does not weaken or
relax any existing security check to make packages "more portable" —
every rule below is compatible with, and in most cases already
enforced by, `security/paths.py` and `validate.py` as they exist today.

## Required files vs optional files

A package's manifest already declares everything canonical
(`manifest.py`). Restating what "required" means precisely, for
portability purposes:

**Always required** (a package missing any of these fails
`clulatent validate` today, and should be treated as not-yet-portable):

- `manifest.json` — the root of trust for every other path.
- The embedded source media file at `manifest.source.stored_path`
  (currently always `sources/<filename>`, since `storage_mode` is
  presently always `"embedded"` — see "Embedded/copy-contained source
  media mode" below).
- `sources/source.sha256` — the sidecar hash `validate_package` checks
  the source media against independently of `manifest.source.sha256`
  (`validate.py`, checks 6–8).
- Every `tracks/*.jsonl` file a `TrackDescriptor` in `manifest.tracks`
  names, even if it is empty (zero records is valid; a *missing* file
  is a hard validation error).
- `receipts/ingest.jsonl` (or whatever `manifest.receipts.file`
  names) — required to exist, though it may be empty.

**Optional / regenerable** (safe to be absent from a portable copy,
and — if absent — must be rebuilt or re-derived by the receiving side,
never assumed):

- `index/search.sqlite` — see "Derived index behavior" below; a
  portable export may omit it entirely, and `clulatent reindex`
  rebuilds it from canonical tracks alone.
- `media/keyframes/*.jpg` — not part of the Phase 1.7.5 lock's hash set
  at all (see `lock.py` module docstring: "media/keyframes/*.jpg is
  never hashed by this phase"), and `validate_package` never
  dereferences `media.keyframes.dir` beyond a lexical + containment
  check (see `validate.py`, "Not currently dereferenced by any
  reader"). Keyframes are a nice-to-have viewer convenience today, not
  a canonical artifact this phase's rules treat as required.
- `lock/` (both the integrity lock and the operation lock) — see "How
  hashes and locks survive moving/copying" and "Why
  `package.operation.lock.json` must not be exported" below. A package
  with no `lock/` at all is a fully valid, fully portable, simply
  *unlocked* package — locking is optional metadata about a package's
  history, not a requirement for validity.

## Embedded (copy-contained) source media mode

This is the **only** mode the current implementation supports —
`SourceInfo.storage_mode` in `manifest.py` is a
`Literal["embedded"] = "embedded"`, and `phase_1_single_source` is a
`Literal[True] = True`. Concretely, this means every `.clulatent`
package produced by `clulatent ingest` today is already
self-contained: the full source media byte stream lives at
`sources/<filename>` inside the package directory, verified by two
independent hashes (`manifest.source.sha256` and the
`sources/source.sha256` sidecar — both checked by `validate_package`).

For portability, embedded mode's implication is simple and already
true: **copying the package directory copies everything needed to
reopen it, with nothing left behind.** No network fetch, no external
file lookup, no environment-specific path is ever required to read a
`storage_mode: "embedded"` package. This is why "Embedded export" is
named as a future *profile* below rather than a new capability — it
would formalize (and let an operator explicitly request) what already
happens by default today.

## Reference-only source media mode (not yet implemented)

Not implemented in any phase to date. Named here because "Reference
export" (below) depends on it existing eventually, and because this
document is the natural place to record what such a mode would need to
guarantee before it could be considered safe to add:

- A `storage_mode: "reference"` package would **not** contain the
  source media bytes under `sources/`; instead `manifest.source` would
  need to carry a **content identifier** the source media can be
  verified against once relocated — the existing `sha256` field is
  already exactly that, and a reference-mode manifest should keep using
  it as the authority, not introduce a second, weaker identity concept.
- A reference-mode manifest would still need `sources/source.sha256`,
  or an equivalent sidecar, describing the *expected* hash — the only
  change from embedded mode is that the bytes it describes live
  somewhere else, resolved at open-time rather than baked into the
  package.
- **Where** that "somewhere else" is (a local filesystem path, a
  content-addressed cache, a user-supplied search path) is explicitly
  undecided by this document — every option raises the same "missing-
  source behavior" question (below) and none is chosen here. This is
  deliberately left as a decision for whichever future phase actually
  implements reference mode, not resolved speculatively now.
- Whatever shape it takes, a reference-mode package must still satisfy
  every path rule in this document (relative POSIX, no parent
  traversal, no absolute paths, no symlink escapes) for any path it
  *does* store — a reference is still untrusted input the moment it is
  read back, exactly like every other manifest-declared path today
  (see `security/paths.py`'s docstring: "Every path stored inside a
  `.clulatent` package ... is untrusted").

## Missing-source behavior

Already exercised today by `validate_package` (`validate.py`, checks 5
& 7): if `manifest.source.stored_path` does not resolve to an existing
file, validation reports a hard error
(`"source file not found: ..."`) and the package is `invalid` — it is
never silently treated as "valid but sourceless." This is the
governing precedent for reference mode too, once it exists: a
reference-mode package whose referenced source cannot be located or
whose hash does not match `manifest.source.sha256` must fail
validation exactly the same way an embedded package with a missing or
corrupted `sources/` file does today — "missing source" is always a
hard validation error, in either mode, never a warning and never a
silently-degraded read. Whether a future reference-mode reader offers
an explicit `--allow-missing-source` (or similar, read-only,
opt-in-only) escape hatch for inspection-without-media use cases is
left to whichever phase implements reference mode; this document does
not pre-approve or design one.

## Relative POSIX path rules / no absolute paths / no parent traversal / no symlink escapes

Already fully implemented and enforced, not new to this phase.
`security/paths.py`'s `validate_relative_posix` (lexical stage) and
`resolve_in_package` (filesystem stage) are the single choke point
every manifest-declared path and every per-record `payload.path` value
passes through before being opened, anywhere in the codebase. For
portability specifically, this is exactly what makes "move the
directory anywhere, on any machine" safe by construction:

- **Relative POSIX only.** Every path stored in `manifest.json` or a
  track record is relative to the package root and forward-slash
  (`/`)-separated — `validate_relative_posix` rejects backslashes and
  Windows drive-letter prefixes outright, so a package's on-disk path
  strings are portable across POSIX and Windows filesystems without
  rewriting.
- **No absolute paths.** A leading `/` is rejected lexically, before
  any filesystem call — a package can never declare a path that
  depends on where *some other* package or install happens to live on
  a given machine.
- **No parent traversal.** Bare `.` and `..` path segments are rejected
  lexically (`validate_relative_posix`) — a package can never declare a
  path that climbs out of its own directory tree via segment syntax
  alone.
- **No symlink escapes.** `resolve_in_package`'s filesystem stage
  additionally resolves real paths and verifies containment under the
  package root, and separately refuses to read or write through a
  symlinked final path component, regardless of what the lexical string
  said — this catches the case a purely lexical check cannot: a
  legitimately relative-looking path whose target directory (or the
  path itself) has been replaced with a symlink pointing outside the
  package.

Because these three checks (lexical relative-POSIX, filesystem
containment, symlink refusal) already run on every read in this
codebase (`validate_package`, `lock.py`, `review_writer.py`, etc.),
**a portable package's paths need no special "export-time" rewriting
at all** — the same checks that make a path safe to open on the
machine it was created on already make it safe to open after being
copied anywhere else. Portability does not require normalizing or
translating paths on export; it only requires that a package continue
to satisfy the rules it was already required to satisfy at ingest.

## How hashes and locks survive moving/copying

By design, unaffected by a move or copy, for two independent reasons:

1. **Content hashes are path- and metadata-independent.** Every hash
   this codebase computes (`manifest.source.sha256`,
   `sources/source.sha256`, every `LockFileEntry.sha256` in
   `package.lock.json`) is a SHA-256 of file *bytes*
   (`hash.sha256_file`), never of a path string, inode number, or
   timestamp. Copying a file with any standard tool (`cp`, `rsync`,
   `tar`, a cloud-storage sync client) preserves bytes exactly, so
   every stored hash remains valid against the copy without
   recomputation.
2. **Lock verification never checks mtime.** `PackageLock.files`
   records `modified_time_ns` per file (informational — see
   `LockFileEntry` in `lock.py`), but `verify_lock` never compares it;
   it only re-hashes each locked file's current bytes and compares
   against the recorded `sha256`. This matters specifically for
   portability because copying, archiving, and re-extracting a
   directory tree routinely changes every file's modification time —
   if `verify_lock` depended on `modified_time_ns` matching, an
   untouched-but-copied package would incorrectly report as changed. It
   does not, so `clulatent verify-lock` on a freshly-copied package
   reports exactly the same result as it would have on the original.

Net effect: `clulatent lock` a package once, copy or move it anywhere
(a new directory, a new machine, an archive and re-extract), and
`clulatent verify-lock` against the copy still passes — with one
caveat, covered next.

## How lock verification should behave after transfer

Two things that *do* need attention after a transfer, both consequence
of the previous section, not contradictions of it:

- **Permission bits are not covered by the lock.** `--chmod-readonly`
  (an opt-in `clulatent lock` flag) strips write bits as a
  best-effort, non-security nudge (see `docs/LOCKING.md`, "What locking
  proves — and what it doesn't"). Some transfer mechanisms (zip
  archives on certain platforms, some cloud-sync clients) do not
  preserve Unix permission bits faithfully. This has no effect on
  `verify-lock`'s pass/fail result (it never checks mode bits either),
  but an operator who relied on `--chmod-readonly` as an accidental-
  edit nudge before transfer should be aware a receiving environment
  may not have preserved it, and re-apply it if desired
  (`clulatent lock --force --chmod-readonly` after arrival, accepting
  that this replaces the lock with an identical one recording the same
  hashes).
- **`--strict` verification depends on what else came along for the
  ride.** `verify_lock(strict=True)` additionally scans for untracked
  "extra" files under the directories it covers (`_scan_candidate_files`
  in `lock.py`) and fails if any exist. A transfer mechanism that
  leaves behind its own artifacts inside the package directory (e.g. a
  `.DS_Store`, a sync client's conflict-copy file, an
  incompletely-cleaned temp file from an interrupted archive extraction)
  would surface as a `--strict` "extra files" failure — correctly, since
  those files genuinely were not part of what was locked. This is not a
  false positive to work around; it is `--strict` doing exactly its
  job. The practical guidance is to run `clulatent verify-lock --strict`
  once immediately after any transfer, before trusting the copy, so any
  transfer-mechanism artifact is caught immediately rather than later.
- Plain `clulatent verify-lock` (non-strict) is unaffected either way —
  it only checks the files the lock actually names, so an extra file
  left behind by a transfer mechanism has no effect on it at all.

## Why `package.operation.lock.json` must not be exported

`lock/package.operation.lock.json` (`security/operation_lock.py`) is
explicitly transient, process-scoped state — it records a live process
id, hostname, and a wall-clock creation time, and exists only for the
duration of a single write operation on the machine that started it
(see `operation_lock.py`'s module docstring: "cooperative guard...
Not a security boundary"). Exporting it would be actively harmful, not
merely useless, for two concrete reasons:

- **A pid/hostname pair means nothing on a different machine.**
  `_check_stale_at`'s liveness check (`_pid_running`, POSIX
  `os.kill(pid, 0)`) only makes sense against the process table of the
  machine that created the lock. On a receiving machine, the same pid
  number is almost certainly a *different, unrelated* process — an
  operation-lock file that survived a transfer could report as "held by
  a live process" against a pid that happens to be running something
  else entirely, or as unconditionally stale/confusing depending on
  what that pid resolves to. Either way it is meaningless data that can
  only confuse the receiving side's own locking logic.
- **It was never meant to persist past a single run.** `_guarded_lock`
  (the shared acquire/release core both `operation_lock` and
  `ingest_target_lock` use) always removes the lock file on the way out
  of its `with` block, success or failure (`finally: ... lock_path.unlink()`).
  Its only legitimate on-disk lifetime is "while a `clulatent` write
  command is actively running." A copy of a package made *while* a
  write was in progress is already an edge case (see "Relationship to
  Phase 2.3" below — Phase 2.3's workflows never copy a package that is
  mid-write), but if it happened, the stray operation-lock file would
  falsely claim the copy is "locked by another operation" the moment
  someone tries to write to it, requiring an unnecessary
  `--force-stale-lock` to clear — friction with zero corresponding
  benefit.

Rule: a portable export should always **exclude**
`lock/package.operation.lock.json` (and, for `ingest`'s sibling variant,
any stray `<output>.oplock.json` next to a package directory, though
that file lives *outside* the package root and is not part of the
package tree to begin with). This is a strict subset of "exclude
transient/generated junk" (below) — it is the one specific file this
document calls out by name because leaving it in is not just wasted
space but actively misleading on the receiving end. The integrity lock
(`package.lock.json` / `package.lock.sha256`) is the opposite case —
it is exactly the kind of durable, meaningful state a portable export
should normally *keep*, per "How hashes and locks survive
moving/copying" above.

## Derived index behavior: `index/` is rebuildable, not canonical

Unchanged from Phase 1 and restated here because it directly answers
"what's safe to leave out of a portable export": `manifest.index` is a
fixed `IndexInfo` shape with `status: Literal["derived"] = "derived"`
and `canonical: Literal[False] = False` (`manifest.py`) — a package
whose manifest claims otherwise already fails Pydantic schema
validation before `validate_package` can even reach its other checks.
`index/search.sqlite` is:

- Excluded from the integrity lock by default (`lock.py`'s
  `create_lock` only hashes it if the caller passes
  `include_index=True` — see `docs/LOCKING.md`, "Why `index/search.sqlite`
  is excluded by default").
- Fully reconstructable from canonical tracks alone via
  `clulatent reindex`, which is explicitly documented as read-only with
  respect to every canonical file and only ever deletes/recreates the
  sqlite file itself (`reindex.py` module docstring).

For portability: a portable export may include or omit
`index/search.sqlite` freely without affecting correctness — the
receiving side can always run `clulatent reindex` to produce (or
refresh) it locally. Omitting it makes a "reference export" profile
(below) meaningfully smaller for no loss of canonical information; a
package transferred *with* a stale or platform-specific sqlite file is
still fully valid (`validate_package` only checks that the file, if
present, opens as valid SQLite — it never compares its *contents*
against the canonical tracks), since nothing treats it as a source of
truth.

## What should be excluded from a portable export

Summarizing the rules above into one exclude-list, for whichever phase
implements an actual `clulatent export` command:

| Path | Exclude? | Why |
|---|---|---|
| `lock/package.operation.lock.json` | **Always exclude** | Transient, process/host-scoped; meaningless (or actively misleading) on another machine — see above. |
| `<output>.oplock.json` (ingest sibling file, outside the package root) | **Always exclude** (it is not inside the package tree in the first place) | Same reasoning; only ever exists briefly next to an in-progress `ingest` target. |
| `index/search.sqlite` | **Safe to exclude** (profile-dependent) | Derived, rebuildable via `clulatent reindex`; not canonical, not required for validity. |
| `lock/package.lock.json` + `lock/package.lock.sha256` | **Keep, if present** | Durable, meaningful evidence — see "How hashes and locks survive moving/copying." Excluding it does not make a package *invalid*, just unlocked; there is no reason to strip it deliberately. |
| Any stray non-canonical file an editor/OS/sync-client left inside the package directory (`.DS_Store`, editor swap files, sync-conflict copies, etc.) | **Exclude** | Not part of the manifest-declared file set at all; `--strict` `verify-lock` already flags these as "extra" — see above. |
| Everything manifest-declared as canonical (`manifest.json`, `sources/*`, every `tracks/*.jsonl`, `receipts/*.jsonl`) | **Never exclude** | Required for validity; see "Required files vs optional files." |

## Future export receipts

Not implemented this phase, named here for the same reason Phase 2.3
named a future `receipts/review.jsonl`: so a future implementer has a
specific, already-considered shape to build against rather than an
open question. A future `clulatent export` command, whenever it
exists, should record what it did as a receipt — mirroring
`receipts/ingest.jsonl`'s existing shape (`operation`, `status`,
`errors`, `warnings`, `timestamp`, tool version) — capturing at least:
which profile was used (reference vs. embedded), which files were
included vs. excluded, and the resulting export's own identity (see
next section). Because `lock.py` already globs `receipts/*.jsonl`
generically, any future `receipts/export.jsonl` would automatically be
covered by a subsequent `clulatent lock` with no change to `lock.py`
needed — the same observation Phase 2.3 made about a hypothetical
`receipts/review.jsonl`.

## Package identity vs. exported-copy identity

`manifest.package_id` (`Manifest.package_id`, set once at ingest — see
`ingest.py`) identifies *what this package's content is*, not *which
file on disk it currently is*. This distinction already matters today,
before any export command exists, and this phase's rules do not change
it:

- Copying a package — for review (Phase 2.3's Workflow 3) or for any
  future export — does **not** change `package_id`. Two directories on
  two different machines, both containing byte-identical
  `manifest.json`/`tracks/*`/`sources/*`, are, and should remain, "the
  same package" by `package_id`, exactly as they would be if copied
  with `cp -r` on a single machine.
- A package whose *content* has changed (new review events written, a
  re-lock, or a future export that drops `index/` or changes
  `storage_mode`) is a **derived** package — a distinct entity for
  locking purposes (it gets its own, independent
  `package.lock.json`/`.sha256` pair, per Phase 2.3's Workflow 5) — but
  this document does not propose minting a new `package_id` for it
  either. `package_id` identity and lock identity are deliberately
  different axes: `package_id` answers "what content lineage is this,"
  while a lock answers "what did this specific directory's bytes look
  like at time T." Two locks can legitimately describe two different
  states of the same `package_id` (e.g. a pre-review and a post-review
  copy), exactly as Phase 2.3 already describes.
- This document does not define a new "export identity" concept
  distinct from the existing `package_id` + lock pair. A reference
  export and an embedded export of the same underlying content should
  share the same `package_id` — they are two representations of the
  same package, not two different packages — while each still gets its
  own independent lock if locked, since their file sets differ (a
  reference export's `sources/` is absent or reduced to a pointer, so
  its lock's file list necessarily differs from an embedded export's).

## Validation expectations after export/import

No new validation rule is introduced by this phase — the existing
`clulatent validate` (`validate.py`) is already the correct, sufficient
gate both before export and after import, for the same reason
Phase 2.3's Workflow 4 already established for reviewed copies: it
re-checks every canonical file's presence, hash, shape, and internal
consistency from scratch, with no assumption that anything about the
package's history (including "was this copied from somewhere else") is
trustworthy on its own. Concretely:

- **Before export**, run `clulatent validate` on the source package —
  exporting an already-invalid package (missing source, corrupt track,
  mismatched hash) would only propagate the problem, never fix it; this
  document does not add an export-time repair step, matching
  `validate.py`'s own read-only, "never fixes anything" design.
- **After import** (i.e. after receiving a transferred/exported
  package on a new machine), run `clulatent validate` again before
  trusting or acting on it — even though every hash-based check
  described in "How hashes and locks survive moving/copying" is
  transfer-safe by construction, `validate` is the one command that
  confirms the transfer *actually* completed correctly (no truncated
  file, no corrupted byte, no accidentally-dropped track) rather than
  merely asserting it should have.
- **`clulatent verify-lock`** (if the package is locked) is a
  complementary, narrower check specifically for "do the locked files'
  bytes still match what was locked" — `validate` is the broader,
  format-correctness check. Running both after a transfer, in either
  order, is the recommended combination; neither subsumes the other
  (`validate` does not re-hash against a `package.lock.json`, and
  `verify-lock` does not check schema/shape of non-lock-covered content
  like `manifest.json`'s field types).
- A future reference-mode package (see above) would add exactly one new
  failure mode to check for at import time — the referenced source
  media not being reachable/resolvable in the new environment — which
  is already covered in principle by "Missing-source behavior" above;
  this document does not add a second, export-specific validation path
  for it.

## Relationship to Phase 2.3 reviewed-copy workflow

Directly compatible, no conflict, no new interaction to design: Phase
2.3's "copy-before-review" (Workflow 3) is already a portability
operation in miniature — copy the package directory, drop `lock/`
(specifically, both `package.lock.json`/`.sha256` *and* any stray
`package.operation.lock.json`, per this phase's rule above, though
Phase 2.3 did not need to distinguish the two since it always dropped
the whole `lock/` directory), review, validate, re-lock. Every rule
this document states about hashes/locks surviving a copy applies
identically whether the copy's purpose is "make an editable reviewed
copy" (Phase 2.3) or "make a package shareable with someone else"
(this phase) — they are the same underlying filesystem operation with
different downstream intent. A reviewed, re-locked copy (the end state
of Phase 2.3's workflow) is already a portable package by every rule in
this document, with no additional step required to make it so.

## Relationship to future CLUBIN

Not designed, not started, explicitly out of scope for this phase
(and, per the Phase 2.4 instruction, no binary or compression format is
introduced here either). The one forward-looking constraint this
document places on a hypothetical future CLUBIN (a single-file,
possibly compressed/binary container for a `.clulatent` package) is
that it should be **round-trippable against everything in this
document without loss**: unpacking a CLUBIN back into a plain directory
should be able to reproduce a package that satisfies every rule above
(required files present, relative POSIX paths intact, hashes/locks
still verifiable) exactly as if it had been `cp -r`'d instead. This
document does not specify *how* CLUBIN would achieve that — only that
"a directory of plain files with relative POSIX paths and content
hashes" (this phase's subject) is the invariant any future container
format must preserve, not replace.

## Relationship to future analysis lanes

Not designed, not started, explicitly out of scope for this phase.
Nothing in this document assumes, precludes, or special-cases any
future track type ("analysis lane") beyond the existing canonical
tracks — every rule here (required-files, path safety, hash/lock
survival, derived-index handling) is expressed in terms of the generic
`manifest.tracks` list and the generic `TrackDescriptor`/`EventEnvelope`
shapes, not any specific track name. A future analysis-lane track
becomes portable automatically, under the same rules already stated
here, the moment it is declared in `manifest.tracks` and written as a
`tracks/*.jsonl` file — exactly as `review_events.jsonl` did in Phase
2.0 with zero changes needed to `lock.py`'s generic `.jsonl` glob (see
Phase 2.0's own "Locking" section). This document does not need, and
does not add, any lane-specific portability rule.

## Two future export profiles

Named targets for a future implementation phase, not implemented here.
Both profiles produce a directory (or, later, a CLUBIN container built
from that directory) that already satisfies every rule above; they
differ only in whether source media bytes travel with the package.

### 1. Reference export

Smaller package; external media is referenced, not copied. Depends on
reference-mode source media (see above, not yet implemented) existing
first. Would carry:

- `manifest.source.sha256` (already exists) as the authoritative
  content identity the referenced media must still match at read time.
- Some form of path/content-id pointer to the external media —
  deliberately unspecified here (see "Reference-only source media
  mode" above for why).
- Every other canonical file (`tracks/*.jsonl`, `receipts/*.jsonl`)
  copied in full, exactly as in embedded export — only the source media
  itself is external in this profile.
- A validation/open path that treats an unreachable or hash-mismatched
  referenced source as a hard error, per "Missing-source behavior"
  above — never a silent degraded read.

### 2. Embedded export

Self-contained package with source media included under `sources/` —
this is simply the current, only-implemented package shape
(`storage_mode: "embedded"`), named here as an explicit "export
profile" so a future `clulatent export --profile embedded` (or
default, no-flag behavior) has a documented target to implement against
rather than needing to reinvent what "the current format" already
means. No new capability is required to produce an embedded export
today — `cp -r` of a validated, optionally-locked package already *is*
one, modulo excluding the operation lock and any stray junk per "What
should be excluded" above.

Neither profile is implemented by this phase. Choosing between them
(and building the CLI surface to select one) is explicitly deferred to
a future phase.

## Non-goals

Restated explicitly, matching the Phase 2.4 implementation instruction:

- No CLUBIN implementation.
- No binary format.
- No compression format.
- No cloud upload.
- No encryption.
- No signatures beyond the current hashes/locks.
- No Studio UI.
- No analysis lanes yet.
- No object tracking yet.
- No destructive editing.
- No unlock/relock workflow (Phase 2.3's existing copy-based workflow
  remains the answer; this phase adds no alternative to it).

## Files changed

- `docs/PHASE_2_4_PORTABLE_PACKAGE_EXPORT_RULES.md` (new, this
  document).
- `README.md`: short Phase 2.4 roadmap note.

## What Phase 2.5+ still need to build

- Reference-mode source media (`storage_mode: "reference"` or
  equivalent), including the still-undecided "where does the
  referenced media actually live" question raised above.
- An actual `clulatent export` (and corresponding import/open path)
  CLI command implementing the reference and embedded profiles named
  here, plus the exclude-list ("What should be excluded from a
  portable export") as concrete behavior rather than documented intent.
- `receipts/export.jsonl`, as sketched above, once an export command
  exists to write it.
- Any CLUBIN container format, built to round-trip against the rules in
  this document, per "Relationship to future CLUBIN."
