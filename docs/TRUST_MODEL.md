# CLULatent Trust Model — Evidence, Not Truth

This is the single most important thing to understand about CLULatent, so
it lives in its own short document.

## The core principle

**Adapters produce evidence, not truth.**

- **Detected does not mean trusted.** A detector firing is a signal, not a
  fact.
- **Generated does not mean canonical.** Producing an adapter result, or
  placing a file on disk, does not make it part of a package.
- **Canonical means validated, bounded, receipted, reviewable, and
  lockable** — a statement about *process*, not about *reality*.

Nothing in CLULatent ever claims that a piece of evidence is *true*.
"Canonical" is the strongest status a piece of evidence can reach, and it
only means the four concrete, checkable properties above were satisfied.

## What "canonical" actually guarantees

When evidence is imported into a package, it becomes canonical only after
all of the following succeed:

1. **Validated** — its shape, bounds, path-safety, and non-identity-claim
   rules pass (`analysis_adapters.validate_adapter_result`, reusing the
   Phase 2.6 lane validators).
2. **Bounded** — per-event and per-lane size/count limits are enforced.
3. **Receipted** — an entry is appended to `receipts/analyze.jsonl`
   recording that the write happened.
4. **Reviewable** — the events are ordinary source events that a human can
   later approve, reject, correct, or override via
   `tracks/review_events.jsonl`.
5. **Lockable** — the package can be locked (`clulatent lock`) so any later
   drift is detectable.

None of these five properties is a claim that the evidence is *correct*.

## The human is still required

CLULatent deliberately keeps a human in the loop:

- Adapters generate candidate evidence.
- Import makes eligible evidence canonical (process, not truth).
- **A human reviewer decides what the evidence means** —
  `clulatent review approve|reject|correct|override|note` writes additive
  review events; nothing an adapter produces is ever auto-approved.

CLULatent does not, and is not intended to, replace human judgment about
what is actually in a piece of media.

## What CLULatent does *not* claim

- It does **not** understand video.
- It does **not** perform object recognition, OCR, captioning, semantic
  scene interpretation, or identity recognition.
- Its one real adapter reports a low-level numeric scene-change score from
  ffmpeg's `scdet` filter — a mechanical signal, not an interpretation.

## Why this matters

If you build on CLULatent, treat every imported event as a *reviewable
claim by a machine producer*, not as ground truth. The format is designed
so that trust is earned through validation, receipts, review, and locking —
never assumed because "the tool said so."

See also:

- [`docs/PHASE_2_19_ADAPTER_PIPELINE_RELEASE_REVIEW.md`](PHASE_2_19_ADAPTER_PIPELINE_RELEASE_REVIEW.md)
  — full adapter-pipeline trust/safety review.
- [`docs/SECURITY.md`](SECURITY.md) — how untrusted packages are treated as
  attacker-controlled input.
