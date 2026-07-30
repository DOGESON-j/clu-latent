# Phase 2.22 — License Selection and Metadata

Base: Phase 2.21 License and Public Repo Gate is frozen
(tag `phase-2.21-license-and-public-repo-gate-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase applies the owner's license decision consistently
across repository metadata and public-facing docs. **It publishes
nothing**: no remote, push, release, public tag, PyPI upload, or version
change. **This is not legal advice.**

## Chosen license

- **BSD 3-Clause License** — a permissive open-source license.
- **Copyright line:** `Copyright (c) 2026 Jayden Kambule`.
- Standard upstream text only; **no nonstandard clauses** were added.

Phase 2.21 left the license "decision pending" (declared `Proprietary`,
no `LICENSE` file). The owner selected **BSD 3-Clause**, and this phase
records it everywhere the repo states a license.

## Files updated

| File | Change |
|---|---|
| `LICENSE` (new, root) | Full BSD 3-Clause text with `Copyright (c) 2026 Jayden Kambule` |
| `pyproject.toml` | `license = "BSD-3-Clause"` (SPDX expression) + `license-files = ["LICENSE"]`; `[build-system] requires` bumped to `setuptools>=77.0` (needed for the PEP 639 SPDX `license` string). Version unchanged (`0.1.0`) |
| `README.md` | License paragraph rewritten to state BSD 3-Clause + link `LICENSE`; roadmap bullet added |
| `docs/LICENSE_DECISION.md` | Rewritten from "pending" to "resolved: BSD 3-Clause" |
| `docs/PUBLIC_REPO_GATE.md` | Section 1 now ✅ (license resolved); overall verdict **HOLD → GO WITH CAVEATS** |
| `docs/PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md` | Superseding note added at top (its HOLD verdict preserved as history) |
| `docs/PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md` | Licensing caveat annotated as resolved |
| `docs/PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md` | New (this file) |
| `tests/test_phase_2_22_license_selection_and_metadata.py` | New tests |

## Package metadata status

- Installed metadata now reports `License-Expression: BSD-3-Clause` (PEP
  639 / SPDX), verified via an editable reinstall (`pip install -e .`).
- Package **name** (`clu-latent`) and **version** (`0.1.0`) are unchanged.
- The `[build-system]` `setuptools` floor was raised from `>=68.0` to
  `>=77.0` because the SPDX-string `license` field requires setuptools
  ≥ 77; the installed toolchain (setuptools 83) already satisfies this, so
  the editable build continues to work unchanged. This is the only
  build-metadata change and is not a refactor.

## Public repo gate impact

- The **single gating blocker** identified in Phase 2.21 (no public-use
  license chosen) is **resolved**. Reuse rights are now granted under
  BSD 3-Clause.
- The public repo gate verdict moves from **HOLD** to **GO WITH CAVEATS**
  (see `PUBLIC_REPO_GATE.md`). The remaining caveats are pre-alpha /
  labelling ones from Phase 2.20, not licensing.

## Repo hygiene

- **No duplicate/conflicting license statements:** `LICENSE`,
  `pyproject.toml`, and `README.md` all say BSD 3-Clause.
- **No stale "license pending" wording left as an active claim:** the
  `LICENSE_DECISION.md` and `PUBLIC_REPO_GATE.md` now state resolved;
  historical phase docs (2.20/2.21) carry explicit "resolved/superseded"
  annotations rather than misrepresenting history.
- **No pyproject/README/docs mismatch.**
- **No secrets, private paths (`/Users/...`), or generated junk** were
  introduced; the tree remains hygienically clean (carried from Phase
  2.21's scan).

## Caveats

- BSD 3-Clause grants reuse rights but the software remains **pre-alpha /
  experimental**; the API and package format may still change. The license
  is independent of stability.
- This is **not legal advice**; the license choice was the owner's.
- The unrelated optional `torchaudio`/`torchcodec` dependency mismatch
  (speech-pipeline tests only, not the adapter pipeline) remains a
  documented non-blocker.
- Passing the gate does not publish anything; any public step remains a
  separate, explicit, owner-driven decision.

## Recommendation: GO WITH CAVEATS

With BSD 3-Clause applied consistently across `LICENSE`, `pyproject.toml`,
`README.md`, and the gate docs, and the tree otherwise clean and honestly
positioned, **no release blocker remains**. The recommendation is **GO
WITH CAVEATS** — the caveats being the pre-alpha/labelling ones above, not
licensing. Actually making the repository public remains a separate,
deliberate, owner-driven step this phase does not take.

## Non-goals (explicit)

- No publishing, pushing, remote, release, public tag, or PyPI upload.
- No package-version change.
- No nonstandard license clauses; the license family is exactly the owner's
  choice (BSD 3-Clause), not invented or altered by this phase.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, Studio UI, or CLUBIN.
- No broad refactors.

## Summary

Phase 2.22 records the owner's license decision — **BSD 3-Clause,
Copyright (c) 2026 Jayden Kambule** — consistently across the root
`LICENSE` file, `pyproject.toml` metadata, the README, and the license/
gate docs, and moves the public repo gate from HOLD to **GO WITH CAVEATS**.
It publishes nothing, changes no package version, adds no intelligence, and
does not make CLULatent understand media.
