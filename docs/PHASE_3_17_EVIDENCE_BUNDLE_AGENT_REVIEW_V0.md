# Phase 3.17 — Evidence Bundle + Agent Review v0

## Summary

Phase 3.17 adds two bounded, non-semantic lanes on top of everything
CLULatent has already computed:

- **Evidence bundles** (`evidence_bundle.py`) answer *"what evidence
  already exists in this package for a given time range?"* — they
  collect already-computed keyframes, visual change candidates,
  changed-region candidates, audio/speech events, audio digest events,
  review events, analysis lane events, receipt paths, and validation
  status that overlap a requested range into one small, bounded,
  timestamped record.
- **Agent review v0** (`agent_review.py`) answers *"given one evidence
  bundle, what is supported, what is missing, and what needs a human
  (or a later, more capable agent) to look at it?"* — a bounded,
  deterministic, rule-based review over exactly one evidence bundle.

Neither lane ever answers **"what happened?"**, **"who is in the
video?"**, or **"what does the scene mean?"**.

## Core rule

> **Agent review is review of evidence, not invention of truth.**

Evidence bundles collect; they do not interpret. Agent review reviews
support and gaps against a bundle's already-computed counts; it does
not decide what is true. Phase 3.17 uses **no external model, no
network call, and no visual/audio AI of any kind** — every finding is
produced by fixed, deterministic rules over numbers and record ids
that already exist in the package.

## What it does

### Evidence bundles — `src/clu_latent/evidence_bundle.py`

- A fixed, closed catalog of 8 evidence categories: `keyframes`,
  `visual_change_candidates`, `changed_region_candidates`,
  `audio_events`, `speech_events`, `audio_digest_events`,
  `review_events`, `analysis_events`. Every one of `evidence_refs`,
  `evidence_counts`, `coverage`, and `missing_evidence` is keyed off
  this same list.
- `validate_evidence_bundle_event` / `validate_evidence_bundle_track` /
  `validate_evidence_bundle_receipt(s)` — non-raising `(errors,
  warnings)` shape validators: closed payload key whitelist, per-
  category ref bounds, internal consistency between `evidence_refs`
  counts and `evidence_counts`/`coverage`/`missing_evidence`, a closed
  set of known receipt paths (`KNOWN_RECEIPT_FILES`), and the fixed,
  exact three-line caveat triple below.
- A forbidden-language check (`FORBIDDEN_EVIDENCE_BUNDLE_PHRASES`)
  applied to genuinely free-text fields only (e.g.
  `validation.validation_summary`), never against `caveats`, which is
  checked by exact equality instead.

`src/clu_latent/evidence_bundle_writer.py`:

- `gather_evidence_bundle_payload(package, manifest, *, start_ms,
  end_ms, ...)` — pure, read-only: reads whatever tracks/receipts
  already exist (an absent track contributes zero evidence, never an
  error) and returns the bundle payload without writing anything. Used
  directly by `evidence-bundles preview`.
- `build_evidence_bundle(package, *, start_ms, end_ms, force=False,
  write_receipt=True, ...)` — appends one new bundle record (id
  `eb_{start_ms:012d}_{end_ms:012d}`, deterministic per range) to
  `tracks/evidence_bundles.jsonl`. Unlike Phase 3.15/3.16's
  whole-track recompute, a package can hold many bundles side by side;
  `build` only refuses on a *duplicate bundle id* (same range built
  twice) unless `--force`. Atomic, lock-aware, rollback-on-failure
  writes (track, then manifest, then receipt).

`src/clu_latent/evidence_bundle_retrieval.py` (read-only):
`get_evidence_bundle_by_id`, `query_evidence_bundles_by_time_range`,
`summarize_evidence_bundles`.

### Agent review v0 — `src/clu_latent/agent_review.py`

- `REVIEW_STATUSES`: `bundle_valid`, `bundle_invalid`,
  `needs_human_review`, `insufficient_evidence`.
- `RECOMMENDED_NEXT_STEPS`: `none`, `human_review`,
  `collect_more_evidence`, `run_candidate_lane`, `validate_package`.
- `CONFIDENCE_LEVELS`: `low`, `medium`, `high`. `SEVERITIES`: `info`,
  `warning`, `error`.
- `ALLOWED_AGENT_REVIEW_LABELS` (closed, 11 labels):
  `evidence_present`, `evidence_missing`, `evidence_incomplete`,
  `unsupported_claim`, `needs_human_review`,
  `ready_for_candidate_review`, `insufficient_evidence`,
  `package_validation_failed`, `bundle_valid`, `bundle_invalid`,
  `no_semantic_claim_made`.
- `FORBIDDEN_AGENT_REVIEW_LABELS` (explicitly named, 10 labels):
  `person_detected`, `face_detected`, `object_detected`,
  `action_detected`, `song_identified`, `weapon_detected`,
  `emotion_detected`, `intent_detected`, `scene_understood`,
  `summary_generated` — a hand-edited or future-agent-produced record
  can never smuggle one of these in; it is rejected outright.
- `compute_agent_review_findings(bundle, *, claims=None)` — the pure,
  deterministic rule engine. Given one evidence bundle record: counts
  which categories are present/missing, flags a bundle whose
  `validation.package_valid_at_build_time` was `False`, checks any
  supplied claims against the bundle's evidence (see below), and
  derives `review_status`, `recommended_next_step`, and `confidence`
  from those facts alone. Calling it twice on the same bundle produces
  an identical result.
- `validate_agent_review_event` / `validate_agent_review_track` /
  `validate_agent_review_receipt(s)` — mirrors the evidence bundle
  validators, plus a cross-check that `payload.evidence_bundle_id`
  actually resolves to a known bundle (when a `bundles_by_id` map is
  supplied) and that the review's own time range fits inside its
  linked bundle's range.

`src/clu_latent/agent_review_writer.py`:

- `load_and_sanitize_claims(claim_file, *, limits=...)` — reads an
  optional, bounded, untrusted claim file (`{"claims": [...]}` only),
  enforcing a file-size bound (256 KiB), a claim-count bound (100), and
  no duplicate claim ids. Fails closed (raises
  `AgentReviewWriteError`) on any shape problem, before any lock is
  acquired.
- `run_agent_review(package, bundle_id, *, claim_file=None,
  force=False, write_receipt=True, ...)` — looks up the bundle, runs
  `compute_agent_review_findings`, and appends one
  `agent_review_event` (id `ar_{bundle_id}`, deterministic per bundle)
  to `tracks/agent_review_events.jsonl`. Refuses a duplicate review id
  unless `--force`. Atomic, lock-aware, rollback-on-failure writes.

`src/clu_latent/agent_review_retrieval.py` (read-only):
`get_agent_review_by_id`, `query_agent_reviews_by_bundle`,
`query_agent_reviews_by_time_range`, `summarize_agent_reviews`.

### Claim checking (optional, `--claim-file`)

A claim is flagged **unsupported** if it:

- cites evidence ref ids that are not present in the linked bundle;
- cites no evidence ref ids at all;
- uses forbidden semantic language anywhere in its free text (checked
  against `FORBIDDEN_AGENT_REVIEW_PHRASES`);
- asserts a semantic claim type this lane can never confirm from
  non-semantic evidence alone (`object`, `person`, `face`, `action`,
  `song`, `intent`, `emotion`, `scene`, `identity`); or
- asserts certainty beyond what bounded, non-semantic evidence can
  support (`definitely`, `certainly`, `confirmed`, `proves`, `proven`,
  `guaranteed`).

A claim that references real evidence, avoids forbidden language, and
makes no semantic/certainty assertion is accepted as supported (not
added to `unsupported_claims`).

### CLI

```
clulatent evidence-bundles build PACKAGE --start-ms 0 --end-ms 5000 [--force] [--no-receipt]
clulatent evidence-bundles preview PACKAGE --start-ms 0 --end-ms 5000
clulatent evidence-bundles summary PACKAGE
clulatent evidence-bundles get PACKAGE eb_000000000000_000000005000
clulatent evidence-bundles query-time PACKAGE --start-ms 0 --end-ms 5000

clulatent agent-review run PACKAGE eb_000000000000_000000005000 [--claim-file claims.json] [--force] [--no-receipt]
clulatent agent-review preview PACKAGE eb_000000000000_000000005000 [--claim-file claims.json]
clulatent agent-review summary PACKAGE
clulatent agent-review get PACKAGE ar_eb_000000000000_000000005000
clulatent agent-review query-bundle PACKAGE eb_000000000000_000000005000
clulatent agent-review query-time PACKAGE --start-ms 0 --end-ms 5000
```

`build` and `run` are the only commands in either group that write to
a package (track, manifest, and — by default, disable with
`--no-receipt` — a receipt). Every other command (`preview`,
`summary`, `get`, `query-time`, `query-bundle`) is strictly read-only
and never creates a receipt; `preview` calls the same pure gather/
review function the write path uses, without acquiring a lock or
touching disk.

## Record shapes

Track: `tracks/evidence_bundles.jsonl`.

```json
{
  "id": "eb_000000000000_000000005000",
  "type": "evidence_bundle",
  "t_start_ms": 0,
  "t_end_ms": 5000,
  "payload": {
    "evidence_refs": {"keyframes": [{"id": "kf_000000", "t_start_ms": 0, "t_end_ms": 0}], "...": []},
    "evidence_counts": {"keyframes": 1, "...": 0},
    "coverage": {"has_keyframes": true, "...": false},
    "missing_evidence": ["audio_events", "speech_events", "..."],
    "receipts_summary": {"receipt_paths": ["receipts/ingest.jsonl"]},
    "validation": {"package_valid_at_build_time": true, "validation_summary": "0 error(s), 0 warning(s)"},
    "caveats": [
      "Evidence bundle, not semantic interpretation.",
      "Collects existing package evidence only.",
      "Does not identify objects, people, text, actions, intent, songs, or scene meaning."
    ],
    "created_by": "clulatent 0.x.x",
    "method": "package_evidence_collection"
  }
}
```

Track: `tracks/agent_review_events.jsonl`.

```json
{
  "id": "ar_eb_000000000000_000000005000",
  "type": "agent_review_event",
  "t_start_ms": 0,
  "t_end_ms": 5000,
  "payload": {
    "evidence_bundle_id": "eb_000000000000_000000005000",
    "review_status": "bundle_valid",
    "findings": [
      {"label": "evidence_present", "severity": "info", "message": "keyframes evidence is present in this bundle (1 record(s)).", "evidence_ref_ids": []}
    ],
    "evidence_present": ["keyframes"],
    "evidence_missing": ["audio_events", "speech_events"],
    "unsupported_claims": [],
    "recommended_next_step": "run_candidate_lane",
    "confidence": "medium",
    "caveats": [
      "Agent review of evidence, not ground truth.",
      "Does not identify objects, people, text, actions, intent, songs, or scene meaning.",
      "Does not use an external model in Phase 3.17."
    ],
    "created_by": "clulatent 0.x.x",
    "method": "rule_based_evidence_bundle_review_v0"
  }
}
```

## Allowed vs. forbidden claims

Allowed: "evidence bundle", "evidence reference",
"evidence present/missing/incomplete", "coverage", "linked record",
"unsupported claim", "needs human review", "ready for candidate
review", "insufficient evidence", "package validation failed",
"bundle valid/invalid", "no semantic claim made".

Forbidden (enforced by `FORBIDDEN_EVIDENCE_BUNDLE_PHRASES` /
`FORBIDDEN_AGENT_REVIEW_PHRASES` on free-text fields, and by
`FORBIDDEN_AGENT_REVIEW_LABELS` on findings): "person", "face",
"object", "car", "weapon", "text", "logo", "action", "intent",
"emotion", "scene meaning", "song", and similar semantic claims — "the
clip shows...", "the model understands...", "who is in the video".

## What it deliberately does NOT do

- No visual AI, no audio AI, no object/face/people/text/logo
  detection, no OCR, no captions, no song identification, no
  scene/action/intent/emotion inference.
- No network access, no external model dependency — verified
  structurally by a test that greps `agent_review_writer.py`'s own
  source for network/model client imports.
- Agent review never invents evidence: every finding traces back to a
  count, a coverage flag, or a ref id that already existed in the
  linked evidence bundle before the review ran.
- Read-only commands (`preview`, `summary`, `get`, `query-time`,
  `query-bundle`) never mutate a package or create a receipt.

## Safety properties

- `build` and `run` are guarded by the package integrity lock and an
  operation lock, and perform ordered atomic writes (track, then
  manifest, then receipt) with best-effort rollback on failure.
- `build` refuses to write a duplicate bundle id (same time range) and
  `run` refuses to write a duplicate review id (same bundle) unless
  `--force` — and even then only touches that lane's own track plus
  the manifest.
- `validate_evidence_bundle_track` and `validate_agent_review_track`
  are wired into `clulatent validate`, including the cross-track check
  that an agent review's `evidence_bundle_id` resolves to a real
  bundle in the package (a dangling reference is a validation error,
  not a silent pass). `receipts/evidence_bundle.jsonl` and
  `receipts/agent_review.jsonl`, if present, get the same bounded
  parse. Absence of either track or receipt file is never an error.
- The claim file is untrusted input: bounded in size and item count,
  shape-checked, and rejected outright (fail closed) on anything
  unexpected — before the operation lock is ever acquired.
- Console output is escaped/sanitised via `safe_console_text`.

## Degraded-input handling

- **Missing id** → `get` returns `None` / prints "no result" (exit 0).
- **Empty / non-overlapping time range** → clean empty result (exit 0).
- **Inverted time range** → clean error (exit 1).
- **No evidence bundle / agent review track yet** → zeroed-out summary
  (exit 0).
- **`agent-review run` against an unknown bundle id** → clean error
  (exit 1), no partial track written.
- **Existing bundle/review id, no `--force`** → clean error (exit 1).
- **Claim file missing, oversized, malformed, or containing duplicate
  claim ids** → clean error (exit 1), refused before any write.
- **Corrupted/hand-edited track, or a dangling `evidence_bundle_id`
  link** → `clulatent validate` reports the specific validation error.

## Files

- `src/clu_latent/evidence_bundle.py` — schema, bounded gather-target
  catalog, validators.
- `src/clu_latent/evidence_bundle_writer.py` — pure gather function
  plus lock-aware `build` writer and receipts.
- `src/clu_latent/evidence_bundle_retrieval.py` — read-only retrieval.
- `src/clu_latent/agent_review.py` — schema, rule-based review engine,
  validators.
- `src/clu_latent/agent_review_writer.py` — claim-file sanitization,
  lock-aware `run` writer and receipts.
- `src/clu_latent/agent_review_retrieval.py` — read-only retrieval.
- `src/clu_latent/validate.py` — wires both new tracks and receipts,
  including the cross-track `evidence_bundle_id` link check, into
  `clulatent validate`.
- `src/clu_latent/cli.py` — the `evidence-bundles` and `agent-review`
  command groups.
- `tests/test_evidence_bundle.py`, `tests/test_agent_review.py` —
  schema/validator tests, forbidden-language tests, claim-checking
  tests, e2e build/run/preview/summary/get/query tests, receipt tests,
  cross-track validation tests, and clean-failure tests.

## Trust position

Adapters produce evidence, not truth; generated does not mean
canonical. This phase makes it possible to gather what evidence
already exists for a moment, and to get a bounded, rule-based read on
what is supported, what is missing, and what still needs a human —
without CLULatent ever claiming to know what happened.
