# Phase 2.21 — License and Public Repo Gate

> **Superseded (Phase 2.22):** the license blocker below was **resolved**.
> CLULatent is now licensed **BSD 3-Clause** (root `LICENSE`,
> `pyproject.toml` `license = "BSD-3-Clause"`, README). The **HOLD** verdict
> recorded here reflects the state *at Phase 2.21* (no license chosen yet);
> it is preserved as history. The current gate verdict is **GO WITH
> CAVEATS** — see
> [`PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md`](PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md)
> and [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md).

Base: Phase 2.20 Public Pre-Alpha Readiness is frozen
(tag `phase-2.20-public-pre-alpha-readiness-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase adds a conservative **license/public-repository
gate** before CLULatent could be shown publicly — documentation, a hygiene
scan, and readiness tests only. **It publishes nothing**: no remote,
push, release, public tag, PyPI upload, or version change. **This is not
legal advice.**

## Core positioning (unchanged)

CLULatent is a **local-first media evidence package format**. It turns
media into timestamped, validated, reviewable machine-readable evidence.
Adapters produce **evidence**, not truth. Generated does not mean
canonical. Canonical means validated, bounded, receipted, reviewable, and
lockable.

## 1. License audit result

| Source | Finding |
|---|---|
| `pyproject.toml` | `license = { text = "Proprietary" }` |
| `LICENSE` / `COPYING` / `NOTICE` | **none present** — not in the working tree, not tracked |
| `README.md` (before this phase) | **silent on license** |
| Other docs | no conflicting license claim |

**Conclusion:** the project is declared **Proprietary** with **no license
file** and **no explicit grant of reuse rights**. The default legal
position is therefore **all rights reserved**. No open-source or public-use
license has been chosen.

Per this phase's constraints, **no license was invented or added**: no MIT/
Apache/GPL/etc. text was introduced, and the declared `Proprietary` family
was not changed. Instead the pending decision is documented for the owner
in [`LICENSE_DECISION.md`](LICENSE_DECISION.md), and the README was updated
to communicate the real state plainly (all rights reserved, no reuse
granted, decision pending).

## 2. Public repo gate checklist

Full, re-runnable version in [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md).
Summary:

| Area | Result |
|---|---|
| License status | ⛔ **blocker** — no public-use license chosen |
| Secrets / tokens / API keys | ✅ none in tracked files |
| Private / machine-specific paths | ✅ none (only test fixtures + guard tests) |
| Large generated files | ✅ none tracked |
| Runtime demo output (`.clulatent`) tracked | ✅ gitignored, not tracked |
| README pre-alpha label present | ✅ |
| Trust-model docs present | ✅ `TRUST_MODEL.md` |
| Known caveats documented | ✅ (Phase 2.20 + this doc) |
| Test command documented | ✅ `python -m pytest` |
| Demo command documented | ✅ `python scripts/demo_real_adapter_workflow.py` |
| No overclaiming about video understanding | ✅ README explicitly disclaims |
| No claims CLUBIN / Studio / ML / plugin runtime implemented | ✅ all stated as not built |
| Optional-dependency caveats documented | ✅ `torchaudio`/`torchcodec` |
| Safe public wording | ✅ |

## 3. Repo hygiene scan

Run against the tracked file set (`git ls-files`), not just the working
directory. **No public-release-blocking hygiene issue was found**, and no
architecture was touched:

- **Secrets/tokens/API keys:** none in tracked files.
- **Private absolute paths:** none real. The only `/Users/...`-style tokens
  in the tree are (a) documentation describing the check itself, (b) the
  `C:/Users/x` path-*rejection* test fixture in
  `tests/test_security_paths.py`, and (c) Phase 2.20/2.21 guard tests that
  assert such paths are absent.
- **Private media filenames:** none tracked.
- **Accidentally tracked `.clulatent` packages:** none — the working-tree
  demo packages are gitignored (`*.clulatent/`,
  `*.clulatent.failure-receipt.jsonl`).
- **Large binaries:** none tracked; the only tracked data file is the tiny,
  deterministic `examples/fixture_adapter_result.json`.
- **Stale hard-coded test counts as truth:** none introduced; readiness/gate
  docs use stable prose.
- **Production-ready / "understands video" / identity-object-OCR-ML-implemented
  claims:** none — the README explicitly disclaims each.

Only documentation was added in this phase; no code, config, or hygiene fix
was necessary because the tree was already clean.

## 4. Blockers

1. **License decision pending (gating).** No public-use license has been
   chosen and no `LICENSE` file exists. Until the owner decides
   (proprietary-with-explicit-terms, an OSS license, or stay private) and
   adds a matching `LICENSE` file, the repo should not be opened for reuse.
   See [`LICENSE_DECISION.md`](LICENSE_DECISION.md).

## 5. Caveats

- This is a **documentation/hygiene gate**, not legal advice; the license
  choice and its implications are the owner's (with counsel as needed).
- A *source-visible, all-rights-reserved* public preview is technically
  possible under the current `Proprietary` declaration, but that is a
  deliberate posture decision the owner must make — this gate does not
  assume it.
- The unrelated optional `torchaudio`/`torchcodec` dependency mismatch
  (speech-pipeline tests only, not the adapter pipeline) remains a
  documented non-blocker, carried over from Phase 2.20.
- Everything else (positioning, trust model, hygiene) is already in good
  shape from Phases 2.19–2.20; this phase adds only the license/repo gate
  layer on top.

## 6. Recommendation: HOLD

**HOLD** on making the repository public *for reuse*.

The repository is hygienically clean (no secrets, no private paths, no
tracked runtime output or large binaries), honestly positioned (pre-alpha,
evidence-not-truth, no overclaiming, no false CLUBIN/Studio/ML claims), and
technically demonstrable offline. The **single gating blocker is
licensing**: no public-use license has been chosen and no `LICENSE` file
exists, so reuse rights are undefined.

Path to change the verdict:

1. Owner makes an explicit license decision
   ([`LICENSE_DECISION.md`](LICENSE_DECISION.md)).
2. Add a matching `LICENSE` file; align `pyproject.toml` and `README.md`.
3. Re-run the [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md) checklist.

Once licensing is resolved, the expected verdict becomes **GO WITH
CAVEATS** (the pre-alpha/labelling caveats from Phase 2.20), since no other
blocker was found.

## Non-goals (explicit)

- No publishing, pushing, remote, release, public tag, or PyPI upload.
- No package-version change.
- **No invented license** — no OSS license text added, license family
  unchanged.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, dynamic plugin loading, Studio UI, or CLUBIN.
- No broad refactors.

## Files changed

- `README.md` (license-status paragraph + roadmap bullet)
- `docs/PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md` (new, this file)
- `docs/LICENSE_DECISION.md` (new)
- `docs/PUBLIC_REPO_GATE.md` (new)
- `tests/test_phase_2_21_license_and_public_repo_gate.py` (new)

## Summary

Phase 2.21 gates CLULatent's public exposure from a license and repository-
safety standpoint. It audits the license state (Proprietary, no `LICENSE`
file, no reuse grant → all rights reserved), documents the pending owner
decision without inventing one, confirms the tree is hygienically clean,
surfaces the license status plainly in the README, and recommends **HOLD**
until an explicit license decision is made. It publishes nothing, adds no
intelligence, and does not make CLULatent understand media.
