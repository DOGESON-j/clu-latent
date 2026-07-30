# CLULatent package locking (Phase 1.7.5)

This document explains the two kinds of "lock" `clulatent` uses, what
each does and does not prove, and the recommended workflow around them.

## Two independent lock kinds

### 1. Integrity lock (`lock/package.lock.json` + `lock/package.lock.sha256`)

A durable record you create deliberately with `clulatent lock`. It
lists every canonical file in the package (see "Files covered" below)
along with its SHA-256 digest, size, and modification time at the
moment of locking. `clulatent verify-lock` recomputes those digests
and reports whether any tracked file has changed, is missing, or (in
`--strict` mode) whether untracked extra files have appeared alongside
the locked ones.

`package.lock.sha256` is itself a SHA-256 digest of `package.lock.json`'s
exact bytes, so tampering with the lock file to hide a change is also
detectable — `verify-lock` treats a lock-hash mismatch as an error in
its own right, distinct from a changed/missing tracked file.

### 2. Operation lock (`lock/package.operation.lock.json`, or `<output>.oplock.json` during ingest)

A short-lived, transient file that exists only while a write operation
(`clulatent ingest`, `clulatent lock`, `clulatent reindex`) is actually
running. It records the pid, hostname, operation name, creation time,
package path, and tool version of the process holding it, and is
removed automatically when that operation finishes — successfully or
not. Its only purpose is to stop two `clulatent` invocations from
writing to the same package at the same time; it is not created or
checked by any read-only command (`inspect`, `timeline`, `query`,
`validate`, `verify-lock`, `lock-status`).

`clulatent lock` and `clulatent reindex` operate on a package that
already exists, so their lock file lives inside that package's own
`lock/` directory. `clulatent ingest` is different: for most of its run
the target package doesn't exist yet (everything is built in a
temporary staging directory next to it and only moved into place on
success — see `docs/SECURITY.md`'s "Atomic ingest" section), so there
is no `lock/` directory to put a lock file in yet. Its operation lock
is instead a sibling file next to the requested output path —
`pkg.clulatent.oplock.json` — using the exact same acquire/stale/
release logic as the in-package lock, just at a different path. This
means two concurrent `clulatent ingest ... -o pkg.clulatent` runs
targeting the same output path fail cleanly (the second one refuses)
instead of racing at the final atomic rename, where the loser's
completed work could otherwise be silently discarded.

## What locking proves — and what it doesn't

Locking proves: "the files listed in `package.lock.json` have (or have
not) changed, byte-for-byte, since the moment `clulatent lock` ran, as
observed by whoever ran `verify-lock`."

Locking does **not** prove:

- **Who created the package**, or that it came from a trustworthy
  source. There is no cryptographic signature tied to an identity —
  see "Why not a cloud/cryptographic signature" below.
- **That the package hasn't been read or copied.** Locking is a
  tamper-evidence mechanism, not confidentiality. It is not encryption.
- **That the package can't be edited.** Anyone with filesystem access
  can edit any file, including deleting or rewriting `lock/*` itself.
  `--chmod-readonly` is a best-effort convenience nudge against
  *accidental* edits (it strips write permission bits), explicitly not
  a security boundary — an owner can always `chmod` the file back.
- **Anything about the semantic correctness of the content.** Locking
  only checks bytes match; it says nothing about whether the perception
  data itself is accurate.

## Why not DRM, encryption, or cloud signing

- **Not DRM.** CLULatent packages are plain, human-inspectable
  directories by design (see the top-level README). Locking adds
  tamper-evidence on top of that, not access control.
- **Not encryption.** Every file remains in plain, readable form after
  locking. Encrypting package contents is out of scope for this phase
  and would conflict with the "human-inspectable" design goal.
- **Not a cloud/cryptographic identity signature.** A real signature
  (e.g. a detached GPG/minisign signature, or a signing-authority-backed
  scheme) proves *who* produced a file and requires key management,
  a trust model, and (usually) a network-reachable verifier or
  certificate chain. This phase deliberately ships only a **local hash
  manifest**: it detects change, not authorship. A future phase could
  layer a real signature *on top of* `package.lock.json` (sign the lock
  file's hash) without changing anything described here.

## Why `index/search.sqlite` is excluded by default

`index/search.sqlite` is derived, disposable, and can always be
rebuilt from the canonical tracks via `clulatent reindex` — see
`docs/SECURITY.md`. Locking it by default would mean routine, harmless
reindexing (e.g. after upgrading the search index format) always
"breaks" the lock. Pass `--include-index` if you specifically want the
search index covered too.

## Why there is no `unlock` command

Deliberately: a lock is meant to be evidence of "this is what the
package looked like at time T," and letting a tool silently rewrite
that evidence in place undermines the point. If you need to make a
legitimate edit to a locked package, the recommended flow is to work
on a fresh copy and re-lock it — not to unlock in place. A dedicated
`copy-unlocked` command (make an editable copy, dropping `lock/`) is a
natural future addition but is not implemented in this phase; today,
the same effect can be had by copying the package directory yourself
and removing `lock/` from the copy.

## How locking interacts with human review (Phase 1.9, design-only)

`clulatent lock` freezes exactly the canonical files it hashes at the
moment it runs — model-generated output included. Phase 1.9's
(design-only, not yet implemented) `review_events.jsonl` track is
additive-only by design: a human review layer is meant to sit *on top
of* frozen model output, never mutate it. That maps onto locking
cleanly, with no special-casing needed in this phase:

1. Lock the package right after ingest to freeze the model-generated
   state: `clulatent lock pkg.clulatent`.
2. Layer human review on top later — approvals, rejections,
   corrections, overrides — by appending to `tracks/review_events.jsonl`
   in a separate, unlocked working copy (or a copy with `lock/` removed;
   see "Why there is no `unlock` command" above).
3. Once review is complete, re-lock the reviewed state as a new lock:
   `clulatent lock` again over the copy that now includes
   `tracks/review_events.jsonl`, producing a fresh
   `package.lock.json`/`package.lock.sha256` pair that covers the
   original model output *and* the review layer together.

`verify-lock` against the original lock would (correctly) report
`tracks/review_events.jsonl` as an unrecorded/"extra" file in `--strict`
mode, since it didn't exist at lock time — this is expected, not a
bug: it's exactly the signal that reviewed content was added after the
original freeze, and is why step 3 re-locks rather than trying to
verify review output against the pre-review lock.

## Recommended workflow

```bash
clulatent ingest video.mp4 -o pkg.clulatent
clulatent validate pkg.clulatent          # confirm it's internally consistent
clulatent reindex pkg.clulatent           # only if you need to rebuild the search index
clulatent lock pkg.clulatent              # record the integrity lock
clulatent verify-lock pkg.clulatent       # before sharing/copying, confirm it still matches
```

If you need to edit a locked package, copy it first (dropping `lock/`
in the copy), make your edits there, and lock the copy separately —
rather than unlocking the original in place.
