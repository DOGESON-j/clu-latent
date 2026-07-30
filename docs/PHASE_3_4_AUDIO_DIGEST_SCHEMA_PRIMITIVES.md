# Phase 3.4 — Audio Digest Schema Primitives

Status: **code-level validation primitives only — no adapter, model,
or runtime wiring**. Base: Phase 3.3 Audio Evidence Digest Contract is
frozen (tag `phase-3.3-audio-evidence-digest-contract-freeze`). Builds
directly on `docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md`, the shared
`EventEnvelope`, and the non-raising `(errors, warnings)` validation
convention already used by `analysis_lanes.py`, `analysis_adapters.py`,
and `review.py`.

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Phase 3.3 defined this rule as a design contract. Phase 3.4 implements
the first piece of *code* that actually enforces it: given a raw,
untrusted, JSON-shaped record claiming to be one of the three digest
record shapes, these primitives check that the record is shaped
safely — bounded, evidence-linked, hedged, and free of raw dense
arrays — before anything downstream ever trusts it.

## What this phase implements

- `src/clu_latent/audio_digest.py` — the module. Exposes:
  - `AUDIO_DIGEST_RECORD_TYPES` — the three supported digest record
    type names, in pyramid order.
  - `is_supported_audio_digest_type(record_type)` — non-raising type
    check.
  - `validate_audio_feature_series(event, *, package_root=None, limits=DEFAULT_LIMITS)`
  - `validate_audio_digest_segment(event, *, limits=DEFAULT_LIMITS)`
  - `validate_audio_llm_context_packet(event, *, limits=DEFAULT_LIMITS)`
  - `validate_audio_digest_event(event, *, package_root=None, limits=DEFAULT_LIMITS)`
    — dispatches to the correct per-type validator based on
    `event["type"]`.
  - `validate_audio_digest_track(events, *, package_root=None, limits=DEFAULT_LIMITS)`
    — validates a full mixed-type track and rejects duplicate ids.
- `tests/test_audio_digest.py` — direct tests of the module.
- This document.
- A Phase 3.4 roadmap bullet in `README.md`.

Every validator follows the same non-raising `(errors: list[str],
warnings: list[str])` convention as the rest of the codebase: nothing
in this module raises for a malformed *record*. `AudioDigestEventError`
exists only for the narrow `normalize_audio_digest_type` helper, never
from a validator.

Every payload field set is a **closed whitelist**, not an open,
free-form dict. This is intentional and stricter than most Phase
2.5/2.6 analysis-lane payloads: the whole point of the digest contract
is to keep dense, unbounded material *out* of these records, so an
unrecognized payload key — a smuggled raw array, an identity field, or
anything else out of scope — is always rejected. Keys like `values`,
`samples`, `frames`, `raw`, `dense_values`, `series_values`,
`embedding`, and `embeddings` produce a specific "raw dense-array
field" error rather than a generic "unrecognized field" error, so a
caller can tell at a glance *why* the record was rejected.

Dense audio arrays are referenced by a package-relative `data_path`
(checked with the existing `security.paths.resolve_in_package` /
`validate_relative_posix` containment and symlink-safety logic —
absolute paths and parent-traversal segments are rejected) and are
never dumped into an `audio_llm_context_packet` or any other digest
record.

Conservative language is enforced the same way Phase 3.2/3.3 defined
it: a fixed, documented set of forbidden phrases (`"proves intent"`,
`"manipulation"`, `"makes viewer afraid"`, `"clulatent understands
audio"`, `"semantic audio truth"`, `"definitely exact instrument"`,
and related variants) is rejected as a case-insensitive substring
match against every free-text field a human or LLM would actually
read — `label`, `summary`, `caveats`, `top_evidence`, `warnings`,
`omitted_detail_reason`. An `audio_llm_context_packet`'s `caveats`
must additionally include at least one caveat that reads as an
evidence-not-truth statement (mentioning both "evidence" and "not
truth").

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No semantic truth generation. These primitives validate that a
  *record* is shaped safely; they never generate audio evidence and
  never claim CLULatent understands audio.
- No new or changed entry in `ANALYSIS_LANE_NAMES`
  (`src/clu_latent/analysis_lanes.py`).
- Not wired into `validate.py`, `manifest.py`, `tracks.py`, or any CLI
  command. No new track file is written by this phase. A future
  `tracks/audio_digest_events.jsonl` (name TBD), once it exists, would
  use these validators before write, the same way every other track
  type validates before commit — that wiring is deferred to a future
  phase.
- No retrieval CLI command, no `clulatent audio-digest …` command.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## Relation to Phase 3.2 and Phase 3.3

- Phase 3.2 (`docs/AUDIO_EVIDENCE_LANES.md`) designed the 10 Level-2
  lane event types this contract's segments link back to
  (`linked_event_ids`). Phase 3.4 does not change or validate those
  lane events directly — only the three digest-layer shapes above
  them.
- Phase 3.3 (`docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md`) designed the
  six-level pyramid and the three record shapes' *illustrative*
  payload sketches. Phase 3.4's actual enforced field names
  (`feature`/`units`/`hop_ms`/`data_path`/`summary` for
  `audio_feature_series`; `label`/`recommended_for_llm_context`/
  `dominant_features` for `audio_digest_segment`;
  `budget_tokens_estimate`/`top_evidence`/`retrieval_hints` for
  `audio_llm_context_packet`) are the concrete code-level schema and
  supersede the design sketch's exact field names where they differ —
  the *rules* (bounded, evidence-linked, hedged, path-referenced dense
  data, mandatory caveats) are unchanged.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new `tests/test_audio_digest.py`
suite, with no regression in existing report/demo/adapter/validation/
locking/review/audio-lane-design/audio-digest-contract tests.
