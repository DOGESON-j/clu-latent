# Phase 2.25 — Public Repo Owner Decision

Base: Phase 2.24 Public Repo Launch Plan is frozen
(tag `phase-2.24-public-repo-launch-plan-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase **records the owner's launch decision** for
whether CLULatent should proceed toward public pre-alpha exposure.
**It does not execute that decision**: no remote was added, nothing was
pushed, no GitHub repository or release was created, and nothing was
published to PyPI. **This is not legal advice.**

## Decision

- **Decision: GO WITH CAVEATS.**
- **Date: 2026-07-10.**
- **Base phase (frozen):** Phase 2.24 Public Repo Launch Plan
  (tag `phase-2.24-public-repo-launch-plan-freeze`).

This decision authorizes the owner to *proceed toward* a public pre-alpha
launch, following the manual steps in
[`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md), when the owner
chooses to. It does not itself make the repository public.

## 1. Is the repo ready to be made public as a pre-alpha?

**Yes, with caveats.** Phases 2.19–2.24 established, in order:

- an honest, conservative review of the adapter pipeline (Phase 2.19: GO
  WITH CAVEATS);
- public pre-alpha positioning and repo hygiene (Phase 2.20: GO WITH
  CAVEATS);
- a license/repo-safety gate that initially **HOLD**-ed on the single
  blocker of no chosen license (Phase 2.21);
- resolution of that blocker via the owner's **BSD 3-Clause** choice
  (Phase 2.22), which moved the gate to GO WITH CAVEATS;
- a final conservative sweep confirming no hygiene, consistency, or
  packaging issue remained (Phase 2.23: GO WITH CAVEATS);
- a manual, not-yet-executed launch plan (Phase 2.24).

No release blocker remains. The repository is hygienically clean, honestly
positioned, and correctly licensed.

## 2. License status

**BSD-3-Clause.** Root `LICENSE` (full text, `Copyright (c) 2026 Jayden
Kambule`), `pyproject.toml` (`license = "BSD-3-Clause"`,
`license-files = ["LICENSE"]`), and `README.md` all agree. See
[`LICENSE_DECISION.md`](LICENSE_DECISION.md) (state: **Resolved**, not
pending).

## 3. Public readiness status

- **Pre-alpha / experimental** — a local developer tool, not a product.
  Package format and Python API may change without notice.
- **Local-first** — no cloud, no account, no telemetry.
- **BSD-3-Clause licensed**, resolved and consistent (see above).
- **No CLUBIN** — not built.
- **No Studio UI** — not built.
- **No core ML requirement** for the current demo; an optional, separate
  Whisper/VAD extra exists behind install flags and is not part of the
  adapter pipeline.
- **First real, non-ML adapter pipeline exists** (ffmpeg `scdet`
  visual-change detector), alongside a deterministic fixture adapter.
- **Public repo gate:** GO WITH CAVEATS
  ([`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md)).
- **Final repo sweep:** GO WITH CAVEATS
  ([`PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md`](PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md)).
- **Launch plan:** written, not executed
  ([`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md)).
- **Nothing has been published yet.**

## 4. Remaining caveats

- **Pre-alpha / experimental** — not stable; the API and package format
  may change without notice.
- **Only minimal real adapter coverage** — one real, non-ML adapter
  (ffmpeg `scdet` visual-change) plus a deterministic fixture adapter.
- **Optional speech-pipeline dependency issue is separate** — an optional
  `torchaudio`/`torchcodec` mismatch fails a handful of speech-pipeline
  tests in some environments; it is not part of the adapter pipeline and
  does not affect ingest, the adapter pipeline, the demo, validation, or
  locking.
- **No Studio UI.**
- **No CLUBIN.**
- **Not production security-audited.**
- This decision record does not itself constitute publication; the manual
  steps below remain.

## 5. Manual launch steps still required (unexecuted by this phase)

From [`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md):

1. Re-run the pre-launch checklist (`python -m pytest`, `git status`,
   `git remote -v`, `git tag --list`, re-read
   [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md)).
2. Create the GitHub repository (private-first recommended), without a
   generated README/`.gitignore`/license.
3. Add the real remote and push the intended public default branch
   (`git remote add origin ...`, `git push -u origin main`) — using the
   owner's real account, replacing the `YOUR_USERNAME` placeholder.
4. Decide whether to push any `phase-*-freeze` tags, and push them
   separately and deliberately.
5. Flip repository visibility to public deliberately, once satisfied.
6. Run the post-push verification checklist.

None of these steps were taken by this phase.

## 6. What must not be claimed publicly

- "CLULatent understands video."
- "AI video memory solved."
- "Production-ready" or that a "V1" / stable release is ready (the package
  version remains `0.1.0`, pre-alpha).
- "CLUBIN is implemented."
- "Studio UI exists."
- "A general plugin runtime exists."
- "Object recognition / OCR / semantic understanding exists" in the core
  adapter pipeline (it does not).
- Any specific passing-test count stated as a permanent fact.

## 7. What is safe to claim publicly

- CLULatent is a **local-first media evidence package format** that turns
  media into timestamped, validated, reviewable, machine-readable
  evidence.
- **Adapter outputs are evidence, not truth**; generated does not mean
  canonical.
- It is **pre-alpha / experimental**, offline-only, with no cloud
  requirement.
- It is **BSD-3-Clause licensed**.
- One **real, non-ML** adapter (ffmpeg scene-change detection) exists
  today, alongside a deterministic fixture adapter.
- Tests pass locally via `python -m pytest`, and a one-command demo exists
  (`python scripts/demo_real_adapter_workflow.py`).

## 8. Rollback / private-again notes

Unchanged from [`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md)
section 12: if anything looks wrong after a future push, the fastest safe
response is to flip GitHub visibility back to private; prefer a follow-up
commit over history rewriting; only treat a genuinely leaked secret as
warranting more drastic action (rotate the secret regardless of git
history, decide separately about a force-push or fresh repository). This
decision record does not pre-authorize any force-push.

## This phase did not publish anything

No remote was added or modified. No push occurred. No GitHub repository,
release, or PyPI package was created. No package version changed. No
release artifact was created. This phase only records a decision; the
decision's execution remains a separate, manual, owner-driven action.

## Non-goals (explicit)

- No publishing, pushing, remote added/modified, GitHub release, public
  tag, or PyPI upload.
- No package-version change; no license-family change.
- No release artifacts.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, dynamic plugin loading, Studio UI, or CLUBIN.
- No broad refactors.

## Files changed

- `docs/PHASE_2_25_PUBLIC_REPO_OWNER_DECISION.md` (new, this file)
- `docs/PUBLIC_REPO_OWNER_DECISION.md` (new, short living decision record)
- `README.md` (Phase 2.25 roadmap bullet)
- `tests/test_phase_2_25_public_repo_owner_decision.py` (new)

## Summary

Phase 2.25 records the owner's decision on public pre-alpha exposure:
**GO WITH CAVEATS**. It restates the current readiness status, the
caveats that must remain visible, what must not be claimed publicly, what
is safe to claim, the manual launch steps still required, and rollback
notes — without executing any of them. It publishes nothing, pushes
nothing, adds no remote, changes no package version, adds no intelligence,
and does not make CLULatent understand media.
