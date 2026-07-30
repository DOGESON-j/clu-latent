# CLULatent Phase 1.13: Phase 2 Readiness Review

**Status**: Review / design only. No implementation. No runtime code. No new
dependencies. No UI. No schema change. No validation-behavior change. No
locking-behavior change. No CLUBIN. This document does not modify any frozen
tag; it reads the frozen foundation and renders a judgment.

**Core question**: Is CLULatent ready to begin **Phase 2 — Reviewed / Trusted
Packages**, where a package can carry human review, corrections, approvals,
rejections, and a derived reviewed-trust state on top of untrusted model
evidence?

**One-line answer**: **GO WITH CAVEATS.** The foundation is coherent,
consolidated, and stable; the review layer is fully designed and additive by
construction. The caveats are not blockers — they are the specific ordering
and guard-rail conditions in [§8](#8-recommended-phase-2-implementation-order)
and [§9](#9-final-recommendation) that Phase 2 must honor so that "reviewed
truth" never overclaims and canonical model evidence is never mutated.

---

## Table of Contents

1. [What Phase 2 Is](#1-what-phase-2-is)
2. [Foundation Review & Readiness Classification](#2-foundation-review--readiness-classification)
3. [What Phase 2 Is Allowed to Implement](#3-what-phase-2-is-allowed-to-implement)
4. [What Phase 2 Must NOT Implement Yet](#4-what-phase-2-must-not-implement-yet)
5. [Phase 2 Entry Criteria](#5-phase-2-entry-criteria)
6. [Phase 2 Exit Criteria](#6-phase-2-exit-criteria)
7. [Major Risks & Mitigations](#7-major-risks--mitigations)
8. [Recommended Phase 2 Implementation Order](#8-recommended-phase-2-implementation-order)
9. [Final Recommendation](#9-final-recommendation)

---

## 1. What Phase 2 Is

Phase 1 (through 1.7.5, plus the design-only 1.8–1.12) produced a package
format whose every perception record is, by design, **untrusted producer
output** — a model's best guess carrying a confidence score but no authority.
Locking proves bytes are unchanged; receipts record provenance; neither
asserts semantic correctness.

Phase 2 adds the missing layer: a way for a **human to assert correctness**
and for downstream consumers to resolve a **reviewed trust state** — all
without mutating the model evidence it judges. Concretely, Phase 2 turns the
design-only Phase 1.9 review layer into running code: a real
`tracks/review_events.jsonl` writer, review validation, review-aware read
commands, and a deterministic read-time resolver. It is the first phase in
which a `.clulatent` package can mean "a human looked at this and vouched for
(or rejected) it," and in which that judgment is traceable, challengeable, and
locked.

Phase 2 is explicitly **not** a semantics phase, an identity phase, a runtime
(CLUBIN) phase, or a UI phase. It is the trust/review phase and nothing more.

---

## 2. Foundation Review & Readiness Classification

Each frozen foundation area is classified **ready** / **ready with caveats** /
**blocked** / **deferred**, with the caveat stated where it exists. "Ready"
means Phase 2 can build directly on it with no change to the area itself.
"Ready with caveats" means it is stable and usable but Phase 2 must respect a
named constraint or extend it in a named, additive-only way. Nothing here is
**blocked**; the two **deferred** items are deliberately out of Phase 2 scope.

| # | Foundation area | Frozen in | Classification | Basis / caveat |
|---|---|---|---|---|
| 1 | **Package layout** | 1.0–1.7.5 (1.11 spec) | **Ready** | Fixed directory constants; `tracks/review_events.jsonl` slots in as one more canonical track with no layout change. Additive by construction. |
| 2 | **manifest.json** | 1.7.x (1.11 spec) | **Ready with caveats** | `extra="forbid"` everywhere is exactly the guarantee we want. Caveat: Phase 2 must add the `review_events` `TrackDescriptor` (and only that) — an additive change already sanctioned as non-breaking by the versioning policy (§12 of the freeze draft). No new top-level manifest section is required. |
| 3 | **Tracks (EventEnvelope-based)** | 1.0–1.7.x | **Ready** | Every track is one `EventEnvelope` per line; review reuses the same container with only a new `type` set and payload shape. No new track *mechanism* is needed. |
| 4 | **EventEnvelope** | 1.0 (event.py) | **Ready** | `extra="forbid"` at envelope + producer level, `ge=0` timestamps, `t_end_ms >= t_start_ms` and confidence-range validators already carry every review event unchanged. Review adds **zero** envelope-level fields — `source_event_ids`/`supersedes_event_ids` live in `payload`. |
| 5 | **Validation** | 1.7.1 (Tier 1) | **Ready with caveats** | Tier 1 (envelope, duration bounds, path safety, JSONL limits, index/receipts cross-check) is implemented and must not weaken. Caveat: Phase 2 must *add* the Tier 4 review rules (referential integrity, acyclic supersedes, `original_payload` drift, producer-prefix, additive-only) as strictly additive checks. New validation may only *add* failure modes, never remove existing guarantees. |
| 6 | **Duration bounds** | 1.7.1 | **Ready** | `source.duration_ms ± EVENT_DURATION_TOLERANCE_MS` (1000 ms) applies to review events unchanged — review events sit on the media timeline and obey the same bound (whole-package notes anchor at `[0,0]`). No change needed. |
| 7 | **Receipts** | 1.7.1 | **Ready** | Append-only `receipts/ingest.jsonl` with the `errors`/`warnings` split is exactly the audit surface a `review` operation needs; the review design already maps onto `ReceiptLog.add(operation="review", …)`. No new receipt shape. |
| 8 | **Integrity locking** | 1.7.5 | **Ready with caveats** | `lock`/`verify-lock`/`lock-status`/`--force` and the no-`unlock` stance are stable and correct. Caveat: Phase 2 review writes must be **lock-aware** — refuse to silently mutate a locked package, and reuse the existing `--force` re-lock (never invent a new bypass). A review track added after lock time correctly surfaces as an `--strict` "extra" file; that is a feature, not a bug. |
| 9 | **Operation locks** | 1.7.5 | **Ready** | The cooperative write guard (`package.operation.lock.json`, `--force-stale-lock`) already wraps `ingest`/`lock`/`reindex`; a `review add` write is one more operation-locked writer using the same mechanism, kept strictly separate from the integrity `--force`. |
| 10 | **Speaker design (1.8)** | 1.8 (design-only) | **Deferred** | Reserved `speaker_events.jsonl`. Review can *reference* speaker event ids if they exist, but Phase 2 does **not** implement diarization runtime. Out of scope by design; no impact on review readiness. |
| 11 | **Review / correction design (1.9)** | 1.9 (design-only) | **Ready with caveats** | This is the spec Phase 2 implements. It is complete: 7 event types, 8-state enum, immutability rule, resolution algorithm, 13 validation rules. Caveat: it is *design-only* — the entire Phase 2 effort is converting it to enforced code and tests. The design being ready is what makes Phase 2 possible; its being unimplemented is precisely what Phase 2 exists to fix. |
| 12 | **Caption import design (1.10)** | 1.10 (design-only) | **Deferred** | Reserved `caption_events.jsonl`. Review may reference caption ids once captions exist, but caption *runtime* is out of Phase 2 scope unless explicitly scheduled. No impact on review readiness. |
| 13 | **Schema freeze draft (1.11)** | 1.11 (spec-only) | **Ready with caveats** | Consolidates the format in one authoritative place and names the exact gaps (its §15) Phase 2 closes. Caveat: it is a *draft* precisely because review is unshipped and schema-backed (Tier 2) validation is absent; it becomes a true freeze as Phase 2 lands. "Where code and this draft disagree, the code is authoritative" is the correct posture and must be preserved. |
| 14 | **Minimal viewer / inspector design (1.12)** | 1.12 (design-only) | **Ready with caveats** | Read-only viewer mapped onto existing `inspect`/`timeline`/`validate`/`verify-lock` primitives, with reserved review lanes. Caveat: not required to *enter* Phase 2, but review-state display is required to make review usable; the viewer's reserved review lanes give Phase 2 a defined place to surface resolved state without a redesign. |

**Summary of classification**: 6 **ready**, 6 **ready with caveats**, 0
**blocked**, 2 **deferred**. No area blocks Phase 2. Every caveat is an
additive-only extension or a "respect the existing guard" condition — none
requires changing frozen behavior.

---

## 3. What Phase 2 Is Allowed to Implement

Phase 2 may implement, and only implement, the review/trust layer:

- **`tracks/review_events.jsonl` writer** — a real, append-only track file
  emitted by a `clulatent review` write path, operation-locked like
  `ingest`/`lock`/`reindex`, writing *only* the review track and its manifest
  `TrackDescriptor` bookkeeping.
- **Review event factories** — constructors for the 7 Phase 1.9 types
  (`review_status`, `review_approval`, `review_rejection`,
  `review_correction`, `review_override`, `human_note`,
  `review_session_summary`), each producing a valid `EventEnvelope` with the
  `human:<reviewer_id>` producer prefix and `rv_` id namespace.
- **Review validation** — the Tier 4 rules from the freeze draft / Phase 1.9
  §9: referential integrity of `source_event_ids`, non-empty-where-required,
  acyclic `supersedes_event_ids` with no self-supersession,
  `original_payload` drift detection, `human:`-only-in-review-track producer
  enforcement, session-summary count consistency, and conflict *warnings*
  (never silent auto-resolution). Added as **strictly additive** checks.
- **CLI review commands** — `approve` / `reject` / `correct` / `note` (and
  status/override as they fit), each appending review events and recording a
  receipt.
- **Read-time reviewed-state resolver** — a deterministic implementation of
  the Phase 1.9 resolution algorithm (collect judgments for a target, drop
  superseded, resolve to a single state or surface a conflict warning),
  usable by `inspect`/`timeline`/`query` and any future consumer.
- **Additive review events only** — never editing or deleting model tracks,
  source media, or prior review events; a changed mind is a *new* event that
  supersedes.
- **Lock-aware write behavior** — refuse to mutate a locked package silently;
  reuse the existing `--force` re-lock to seal reviewed state; treat a
  post-lock review track as the intended `--strict` "extra" signal.
- **Inspector / query support for review state** — surfacing resolved state,
  conflicts, and correction chains in the read-only viewers (the Phase 1.12
  reserved review lanes).
- **Tests** — full coverage of review/correction workflows (see
  [§6](#6-phase-2-exit-criteria)).

---

## 4. What Phase 2 Must NOT Implement Yet

Phase 2 must **not** expand beyond the review/trust layer. Explicitly out of
scope:

- **Semantic events** — no `semantic_events` generation, no meaning/role/
  sentiment/intent extraction. Reviews judge existing perception, they do not
  create semantics.
- **Real identity recognition** — no face/voice biometric identity. Reviewer
  ids and speaker ids remain opaque, in-package handles.
- **Diarization runtime** — no `speaker_events` producer; Phase 1.8 stays
  design-only. Review may reference speaker ids only if some other tool
  produced them.
- **Caption import runtime** — no `caption_events` writer unless captions are
  explicitly scheduled as a separate Phase 2 work item. Review may reference
  caption ids only if they already exist.
- **Cloud review service / network** — review stays local, file-only. No
  fetch, upload, or remote validation.
- **Collaboration / accounts / permissions** — no auth, login, assignment,
  queues, SLAs, or multi-stage routing. `supersedes_event_ids` is the only
  escalation trail.
- **Cryptographic signatures** — review events are not signed; trust equals
  filesystem-access trust, same caveat as the integrity lock. Signing is a
  later, additive layer.
- **CLUBIN** — no compiled binary, no Rust runtime. The resolver is
  *specified and implemented in the tool*, not compiled into a runtime.
- **Full Studio app / UI** — no review frontend, web viewer, or annotation
  GUI. Read-only inspector enrichment only.
- **Destructive editing of model tracks** — the immutability rule is
  absolute: model tracks and source media are byte-frozen with respect to
  review.
- **Silent lock replacement** — never re-lock a package without explicit
  `--force`; never invent a review-specific unlock/bypass.
- **Auto-truth merging** — no automatic blending of Whisper / caption /
  review evidence into a single "truth." Every reviewed state is an explicit
  human-authored assertion; conflicts are surfaced, not averaged.

---

## 5. Phase 2 Entry Criteria

All of the following must hold *before* Phase 2 implementation begins. Status
as of this review is noted.

| # | Entry criterion | Status |
|---|---|---|
| 1 | **Clean test suite** — the suite passes with no new failures attributable to design work. | Met, with a standing caveat (see [§9](#9-final-recommendation) on the observed-vs-claimed baseline). |
| 2 | **Schema freeze draft exists** — one authoritative consolidated spec of the format. | Met — `docs/spec/CLULATENT_SCHEMA_FREEZE_DRAFT.md`. |
| 3 | **Review design exists** — complete, normative review/correction spec. | Met — `docs/design/PHASE_1_9_HUMAN_REVIEW_CORRECTION.md`. |
| 4 | **Locking behavior stable** — `lock`/`verify-lock`/`lock-status`/`--force` and no-`unlock` are frozen and documented. | Met — Phase 1.7.5 + `docs/LOCKING.md`. |
| 5 | **Operation lock behavior stable** — cooperative write guard + `--force-stale-lock`, separate from integrity `--force`. | Met — Phase 1.7.5. |
| 6 | **Package writes are safe and additive** — the existing write paths (`ingest`/`lock`/`reindex`) are operation-locked and containment-checked, giving review a proven pattern to follow. | Met. |
| 7 | **Validation can be extended without weakening current guarantees** — Tier 1 is additive-friendly; new tiers add failure modes only. | Met — validation tiers are explicitly layered (freeze draft §11). |

All seven entry criteria are met. The only asterisk is criterion 1's baseline
discrepancy, addressed in [§9](#9-final-recommendation).

---

## 6. Phase 2 Exit Criteria

Phase 2 is complete only when all of the following hold:

1. **`tracks/review_events.jsonl` is implemented** — a real append-only writer
   emits valid review events, with the manifest `TrackDescriptor` recorded.
2. **Review commands exist** — approve / reject / correct / note (and
   status/override), each appending events and recording a receipt.
3. **Review validation exists** — the Tier 4 rules are enforced by
   `clulatent validate` as additive checks; Tier 1 guarantees are unchanged.
4. **Corrections / rejections / approvals are traceable** — every judgment
   resolves back through `source_event_ids` to the exact model evidence it
   judges; nothing is asserted about "nothing."
5. **Model output remains preserved** — `speech_events`/`audio_events`/
   `keyframes`/`speaker_events` are byte-identical before and after review;
   re-running a model reproduces the same track.
6. **Locked-package behavior is safe** — writing review to a locked package
   never silently mutates it; re-locking requires explicit `--force`; a
   post-lock review track surfaces correctly under `--strict`.
7. **Reviewed state can be resolved deterministically** — the resolver yields
   the same answer for the same package state on any conformant consumer, and
   surfaces (never hides) conflicts.
8. **Tests cover the hard cases** — at minimum: tampering (post-lock model
   mutation detected), malformed review events (envelope/bounds rejection),
   missing references (dangling `source_event_ids`), self-supersession,
   locked-package writes (refused without `--force`), and re-lock behavior
   (new lock covers the review track).

---

## 7. Major Risks & Mitigations

| # | Risk | Why it matters | Mitigation |
|---|---|---|---|
| 1 | **Overclaiming reviewed truth** | Presenting a reviewed state as more authoritative than it is (e.g. one reviewer's `approved` shown as ground truth), or auto-resolving conflicts silently. | Reviewed state is *derived*, never stored as its own "truth" event; the resolver surfaces conflicting live reviews as warnings (Phase 1.9 rules 10–11) and never averages or auto-picks. Confidence stays reviewer-self-reported, never blended with model confidence. Inspector labels reviewed state as *derived from N human events*, with the chain visible. |
| 2 | **Accidentally mutating canonical model evidence** | The load-bearing immutability rule; a single in-place edit to `speech_events.jsonl` breaks reproducibility and lock honesty. | Review writer touches *only* `review_events.jsonl` + its manifest entry. Enforce with a validation rule that model-track `record_count`/bytes must match what locking recorded after review (Phase 1.9 rule 13); test that model tracks are byte-identical pre/post review. |
| 3 | **Weak `source_event_ids` validation** | A dangling or wrong reference makes a judgment meaningless or misattributed; silent acceptance erodes traceability. | Referential integrity is a **hard error**: every `source_event_ids` id must resolve to an existing event in some canonical track; `original_payload` must match the referent's current value (drift → hard error) so a correction against stale reality is caught. Test dangling refs and drift explicitly. |
| 4 | **Lock / re-lock confusion** | Conflating the integrity `--force` with the operation-lock `--force-stale-lock`, or silently re-locking, would undermine tamper-evidence. | Reuse Phase 1.7.5 exactly: no `unlock`; re-lock needs explicit `--force`; stale operation lock needs the *separate* `--force-stale-lock`. Never introduce a review-specific bypass. A post-lock review track is the intended `--strict` "extra" signal. Document and test both flags stay distinct. |
| 5 | **Schema drift** | The freeze draft and the code diverging, or a review addition sneaking in a breaking change. | Treat the code as authoritative (freeze draft's stated posture); review additions are additive-only per the versioning policy (new track, new types, optional payload fields = minor bump). Add Tier 2 per-`type` payload validation so the format *enforces* what the draft *describes*. Any breaking change requires a documented migration. |
| 6 | **UI scope creep** | A "small viewer" quietly growing into a Studio app, pulling in frontend deps and delaying the trust layer. | Phase 2 UI is read-only inspector *enrichment* over existing primitives (Phase 1.12), nothing more. No frontend deps, no web server, no editor. Review is authored via CLI commands writing JSONL, not a GUI. Enforce as an explicit non-goal (§4). |
| 7 | **Semantic interpretation sneaking in** | A `review_correction` that "reclassifies" could drift toward asserting meaning/roles rather than judging perception. | Review judges *existing perception events only*; `semantic_events` stays reserved and unproduced. `override_kind` is constrained to structural operations (reclassify/respan/merge/split) over perception, not semantic labeling. Enforce as a non-goal and keep the override schema tight. |
| 8 | **Unclear migration story** | Without a defined upgrade path, a `0.1.0` package plus a later reviewed schema becomes ambiguous to read or migrate. | Before freezing Phase 2.0, define how a `0.1.0` package is read/migrated by a review-aware reader, what a breaking bump requires, and a `clulatent migrate` (or equivalent). Versioning policy is stated (freeze draft §12); Phase 2 must make the executable path exist (this is a named exit-adjacent deliverable, step 9). |

---

## 8. Recommended Phase 2 Implementation Order

Ordered so that nothing is written before the thing that validates it exists,
and so guard-rails land before the write paths they protect.

1. **Add review event schemas / factories** — the 7 Phase 1.9 types as
   validated `EventEnvelope` constructors (`human:` producer, `rv_` ids). Pure
   data + construction; no I/O.
2. **Add `review_events.jsonl` track support** — read/parse the track (tolerant
   in `reindex`, strict in `validate`), plus the manifest `TrackDescriptor`
   bookkeeping. Still no write command.
3. **Add validation for review events** — the Tier 4 rules as strictly
   additive checks (referential integrity, acyclic supersedes, drift,
   producer-prefix, session-summary counts, conflict warnings). Validation
   exists *before* any writer can produce content it fails to catch.
4. **Add CLI commands** — `approve` / `reject` / `correct` / `note` (+ status/
   override), each operation-locked, appending events, recording receipts, and
   refusing to silently mutate a locked package.
5. **Add read-time reviewed-state resolver** — the deterministic Phase 1.9
   resolution algorithm, independently testable, consumed by read commands.
6. **Add lock-aware write protections** — enforce additive-only against a
   locked package, reuse `--force` re-lock, keep `--force-stale-lock`
   separate, verify model tracks stay byte-stable.
7. **Add inspector / query support for review state** — surface resolved
   state, conflicts, and correction chains in the read-only viewers (Phase
   1.12 reserved lanes).
8. **Add tests and docs** — cover tampering, malformed events, missing
   references, self-supersession, locked-package writes, re-lock; document the
   migration story.
9. **Freeze Phase 2.0 reviewed packages** — promote the freeze draft to a true
   freeze (review shipped, Tier 2/4 enforced, migration path defined), tag it.

---

## 9. Final Recommendation

**GO WITH CAVEATS.**

**Why GO.** The foundation is genuinely ready. The package layout, envelope,
manifest strictness, duration bounds, receipts, and both lock kinds are stable
and were *designed* to accommodate exactly this layer: review reuses the
shared `EventEnvelope` with zero envelope-level change, adds one additive
canonical track, introduces no new dependency, needs no UI, and its validation
extends Tier 1 without weakening it. The Phase 1.9 design is complete and
normative; the Phase 1.11 freeze draft has already named the exact gaps Phase
2 closes; the Phase 1.12 viewer has reserved the review lanes. Every one of
the seven entry criteria is met, and the foundation review found **zero
blocked areas** — only additive extensions and "respect the existing guard"
caveats. There is no architectural reason to wait.

**Why WITH CAVEATS, not an unqualified GO.** Three honest conditions attach:

1. **The reviewed-truth guard-rails are load-bearing and must ship in order.**
   Validation (step 3) and the resolver's conflict-surfacing must exist before
   and alongside the write commands (step 4) — never after. Reviewed state is
   *derived and challengeable*, never a stored truth claim; the immutability
   rule and lock-awareness are non-negotiable. The GO is contingent on Phase 2
   honoring [§8](#8-recommended-phase-2-implementation-order)'s ordering and
   [§7](#7-major-risks--mitigations)'s mitigations, not treating them as
   optional polish.

2. **The freeze is still a draft, and the migration story is unbuilt.** Phase
   2 must land Tier 2 (schema-backed payload) validation and a defined
   `0.1.0`-forward migration path before promoting the draft to a true freeze
   (step 9). Entering implementation is sound; *declaring the schema frozen*
   is not yet.

3. **Test-baseline honesty.** The brief states a clean suite of 228/228. The
   observed suite result in this working environment is **220 passed, 8
   failed** — the 8 failures are pre-existing *environmental* issues
   (torchaudio 2.11.0 requiring the missing `torchcodec`, in
   `test_ingest_phase17`, `test_transcribe`, `test_vad`), unrelated to any
   design work and untouched by this review. Design work has not changed
   behavior. Before Phase 2 code begins, the environment should be repaired
   (install `torchcodec` / align torchaudio) so the suite is genuinely green
   and regressions in the review layer are unambiguous. This is an
   environment-hygiene caveat, not a foundation defect.

**Net**: CLULatent is ready to *enter* Phase 2 implementation now, along the
ordered path in [§8](#8-recommended-phase-2-implementation-order), provided the
review layer stays additive, derived, lock-aware, and conflict-surfacing, and
provided the schema-freeze/migration and test-environment caveats are closed
before Phase 2.0 is itself frozen. **GO WITH CAVEATS.**

---

## Summary

- **Foundation**: 6 ready, 6 ready-with-caveats, 0 blocked, 2 deferred
  (speaker/caption runtime). No blockers.
- **Allowed**: the review/trust layer only — track, factories, validation,
  CLI, resolver, lock-aware writes, inspector support, tests.
- **Forbidden**: semantics, identity, diarization/caption runtime, cloud,
  accounts, signatures, CLUBIN, Studio UI, destructive edits, silent re-lock,
  auto-truth merging.
- **Entry criteria**: all 7 met (with the test-environment asterisk).
- **Exit criteria**: 8 conditions, centered on traceability, preserved model
  output, deterministic resolution, and hard-case test coverage.
- **Recommendation**: **GO WITH CAVEATS** — enter Phase 2 in the ordered
  sequence, keep reviewed truth derived and additive, and close the
  freeze/migration and test-environment caveats before freezing Phase 2.0.
