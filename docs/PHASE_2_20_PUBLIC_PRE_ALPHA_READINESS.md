# Phase 2.20 — Public Pre-Alpha Readiness

Base: Phase 2.19 Adapter Pipeline Release Review is frozen
(tag `phase-2.19-adapter-pipeline-release-review-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase prepares the repository for a **possible** public
pre-alpha preview — documentation, repo hygiene, safety checks, and
readiness tests only. **It does not publish anything**: no remote is added,
nothing is pushed, no release or tag is created, no package is uploaded,
and the package version is left unchanged.

## Core positioning (unchanged)

CLULatent is a **local-first media evidence package format**. It turns
media into timestamped, validated, reviewable machine-readable evidence.

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and lockable.

See [`TRUST_MODEL.md`](TRUST_MODEL.md) for the full trust model.

## What this phase changed

- **README public-readiness pass.** Retitled from "CLULatent (Phase 1)" to
  "CLULatent", and added a concise **Status: pre-alpha (experimental)**
  section a stranger reads first: what it is, what works today, what does
  not work yet, local-first/no-cloud framing, the evidence-not-truth trust
  model, the local test command, and the one-command demo. Wording was
  reviewed to avoid overclaiming (no "understands video", no object
  recognition / OCR / semantic understanding / identity recognition / no
  production-readiness claims).
- **New public-facing docs** (concise, cross-linked rather than
  duplicative):
  - [`TRUST_MODEL.md`](TRUST_MODEL.md) — the evidence-not-truth model and
    what "canonical" does and does not guarantee.
  - [`PUBLIC_PRE_ALPHA_CHECKLIST.md`](PUBLIC_PRE_ALPHA_CHECKLIST.md) — a
    re-runnable hygiene/positioning checklist.
  - This document.
  (No `ADAPTER_PIPELINE_OVERVIEW.md` was added — the Phase 2.19 release
  review already covers the pipeline end-to-end, and is linked instead.)
- **Repo hygiene verification** (findings below). No tracked-file leakage
  or committed runtime junk was found; no destructive cleanup was needed.

## Repo hygiene findings

Reviewed against the actual tracked file set (`git ls-files`), not just the
working directory:

- **Runtime demo output is not tracked.** The `.clulatent` package folders
  and `*.clulatent.failure-receipt.jsonl` present in the working directory
  (`sample.clulatent/`, `clu-clip-test.clulatent/`, etc.) are all matched
  by `.gitignore` (`*.clulatent/`, `*.clulatent.failure-receipt.jsonl`) and
  are **not** committed. A public snapshot would not include them.
- **No private/machine-specific paths in tracked files.** The only
  `/Users/...`-style match in the tracked tree is `C:/Users/x` in
  `tests/test_security_paths.py`, which is a deliberate *path-rejection
  test fixture* (verifying Windows-style absolute paths are refused), not a
  real private path.
- **No secrets/tokens/API keys** found in tracked files.
- **No large committed binaries.** The only tracked data artifact is the
  tiny, deterministic `examples/fixture_adapter_result.json`, which an
  existing test keeps byte-for-byte in sync with its generator.
- **`pyproject.toml` metadata is coherent for pre-alpha:** name
  `clu-latent`, version `0.1.0`, `requires-python >=3.11`, a clear
  description, ML dependencies isolated as optional extras (`vad`,
  `whisper`). The version was intentionally **not** changed by this phase.
- **No stale hardcoded test counts** were introduced; readiness docs
  describe status in stable prose rather than a brittle passing-test
  number.

No hygiene issue required a fix. `.gitignore` already covers every class of
generated output this project produces.

## Public caveats (state these before showing anyone)

- **Pre-alpha / experimental** — not a product, not stable.
- **Local developer tool** — runs entirely offline; no cloud, account, or
  telemetry.
- **API / package format may change** without notice.
- **Only the first real, non-ML adapter exists** (ffmpeg `scdet`
  visual-change), plus a deterministic fixture adapter.
- **Demos use tiny, deterministic media/fixtures** (one-to-two-second
  synthetic clips), not real-world-scale media.
- **No general plugin runtime** — no adapter discovery, no scheduler, no
  multi-adapter orchestration.
- **No ML model integration in the core** adapter pipeline. (An optional,
  separate Whisper/VAD path exists behind install extras and is not part of
  the adapter pipeline.)
- **No Studio UI.**
- **No CLUBIN compiler.**
- **Not production security-audited.**
- **Review/correction exists, but user judgment is still required** —
  nothing an adapter produces is auto-approved; a human decides what
  evidence means.

## Known non-blocking issue (disclose, don't hide)

An optional, unrelated dependency mismatch (`torchaudio` requiring
`torchcodec`) currently fails a handful of **speech-pipeline** tests
(`test_vad.py`, `test_transcribe.py`, `test_ingest_phase17.py`) in some
environments. These are **not part of the adapter pipeline** this phase
prepares for preview, and do not affect ingest, the adapter pipeline, the
demo script, validation, or locking. They should be resolved (pin or fix
the optional dependency) before any broader release, but they are not a
blocker for a clearly-labelled pre-alpha preview that foregrounds the
local, non-ML adapter pipeline.

## Recommendation: GO WITH CAVEATS

The repository is safe to show publicly as a **clearly-labelled pre-alpha
preview**. Positioning is honest and non-overclaiming, the trust model is
prominent, repo hygiene is clean (no tracked secrets, private paths, or
committed runtime output), and the headline capability — a local, offline,
non-ML adapter evidence pipeline — is real, tested, and demonstrable with a
single command.

Caveats and suggested next steps *before* a public launch (not before a
preview):

1. **Keep the pre-alpha label prominent.** Do not let a demo imply
   CLULatent understands media; it detects a low-level scene-change signal
   and treats it as reviewable evidence.
2. **Resolve the optional `torchaudio`/`torchcodec` dependency mismatch**
   so `python -m pytest` is green in a stock environment, or document the
   optional-extra install path clearly so a first-time runner is not
   surprised.
3. **Decide licensing intent before publishing.** *(Resolved in Phase
   2.22: CLULatent is now licensed **BSD 3-Clause** — root `LICENSE`,
   `pyproject.toml` `license = "BSD-3-Clause"`, README.)* At the time of
   this phase, `pyproject.toml` declared `license = "Proprietary"` and a
   deliberate owner decision was still outstanding; Phase 2.20 itself did
   not change it.
4. **Add a second real adapter (still non-ML)** before claiming "an adapter
   pipeline" generalizes — carried over from the Phase 2.19 review.
5. **This phase does not publish.** Any actual public step (adding a
   remote, pushing, creating a release, tagging a public version, uploading
   to an index) remains a separate, explicit, owner-driven decision.

None of these caveats is a safety or correctness blocker; they are scope,
labelling, and pre-launch-hygiene notes appropriate to a pre-alpha preview.

## Non-goals (explicit)

- No publishing, pushing, remote, release, public tag, or PyPI upload.
- No package-version change.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, dynamic plugin loading, Studio UI, or CLUBIN.
- No broad refactors.

## Files changed

- `README.md` (public-readiness status section + retitle + roadmap bullet)
- `docs/PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md` (new, this file)
- `docs/TRUST_MODEL.md` (new)
- `docs/PUBLIC_PRE_ALPHA_CHECKLIST.md` (new)
- `tests/test_phase_2_20_public_pre_alpha_readiness.py` (new)

## Summary

Phase 2.20 makes CLULatent understandable and safer to show publicly as a
pre-alpha preview, without publishing it. It adds honest, non-overclaiming
public-facing positioning (README status section, trust-model doc,
readiness checklist), verifies repo hygiene (no tracked secrets, private
paths, or committed runtime output), documents the pre-alpha caveats, and
recommends **GO WITH CAVEATS**. It adds no intelligence and does not make
CLULatent understand media.
