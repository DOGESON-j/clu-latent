# Phase 2.24 — Public Repo Launch Plan

Base: Phase 2.23 Public Repo Final Sweep is frozen
(tag `phase-2.23-public-repo-final-sweep-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase creates a **conservative public repository launch
plan** — a manual checklist and command-template document the owner can
follow later, whenever they decide to actually make CLULatent public.
**It does not launch anything**: no remote was added, nothing was pushed,
no GitHub repository or release was created, and nothing was published to
PyPI. **This is not legal advice.**

## Core positioning (unchanged)

CLULatent is a **local-first media evidence package format**. It turns
media into timestamped, validated, reviewable machine-readable evidence.
Adapter outputs are **evidence, not truth**. Generated does not mean
canonical. **Canonical** means validated, bounded, receipted, reviewable,
and lockable.

## What this phase produced

- [`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md) — the
  re-runnable launch plan itself: pre-launch checklist, GitHub repo setup
  checklist, suggested repo-visibility flow (private-first, then flip to
  public), placeholder remote/push command templates, branch/tag push
  strategy, what to push vs. not push, suggested first public README/
  status content, caveats to keep visible, what not to claim publicly, a
  suggested public repo description, an explicitly optional/draft first
  announcement text, a post-push verification checklist, and rollback/
  private-again notes.
- This file — the Phase 2.24 record.
- A Phase 2.24 roadmap bullet in `README.md`.
- `tests/test_phase_2_24_public_repo_launch_plan.py` — lightweight
  keyword/section guard tests.

## Recommendation: GO WITH CAVEATS

Consistent with Phases 2.19–2.23: the repository has no outstanding
release blocker (license resolved, hygiene clean, positioning honest), so
the launch plan itself is rated **GO WITH CAVEATS** — the caveats being
pre-alpha/experimental scope, not safety or licensing. See
[`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md) and
[`PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md`](PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md)
for the underlying gate/sweep this plan builds on.

## Command templates use placeholders only

Every remote/push command in `PUBLIC_REPO_LAUNCH_PLAN.md` uses the literal
placeholder `YOUR_USERNAME` (e.g.
`git remote add origin git@github.com:YOUR_USERNAME/clu-latent.git`) and is
explicitly labelled as a **manual command for the owner to run later**, not
an instruction this phase executes. No real GitHub username was introduced
by this phase's tracked-file changes.

Note: this local working copy already has a `git remote` named `origin`
configured to `https://github.com/YOUR_USERNAME/clu-latent.git` — that URL
was already present in the local git configuration before this phase and
is itself only the same literal placeholder, not a real account; local git
remote configuration is not a tracked file and is outside this document's
scope. This phase did not add, modify, or push to any remote.

## What was checked (hygiene, for this phase's own new docs)

- ✅ No real private absolute / home-directory paths in the new docs.
- ✅ No real GitHub username — only the literal `YOUR_USERNAME` placeholder.
- ✅ No secrets/tokens/API keys.
- ✅ No command in the new docs would publish anything if copy-pasted
  verbatim during this phase — every remote/push example is presented as
  a future, manual, owner-run step, and this phase did not execute any of
  them.
- ✅ No overclaiming — the suggested public description, first-README
  content, and draft announcement text all stay within the same
  evidence-not-truth, pre-alpha framing already established.
- ✅ No stale "license pending" wording presented as an active state — the
  plan states BSD 3-Clause as resolved, consistent with
  `LICENSE_DECISION.md`.
- ✅ No claim that a "version 1" or stable release is ready — the plan
  explicitly calls out that the package version remains `0.1.0` and
  pre-alpha.

No hygiene issue required a fix beyond writing the new docs conservatively
in the first place.

## Caveats

- **Pre-alpha / experimental** — not a product, not stable; API/format may
  change without notice.
- **Only minimal real adapter coverage** — one real, non-ML adapter plus a
  deterministic fixture adapter.
- **Optional speech-pipeline dependency issue remains separate** —
  `torchaudio`/`torchcodec`, not part of the adapter pipeline.
- **No Studio UI.**
- **No CLUBIN.**
- **Not production security-audited.**
- This plan does not itself constitute authorization to publish; actually
  making the repository public remains a separate, explicit, owner-driven
  decision, taken manually and later.

## Non-goals (explicit)

- No publishing, pushing, remote added, GitHub release, public tag, or
  PyPI upload.
- No package-version change; no license-family change.
- No release artifacts.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, dynamic plugin loading, Studio UI, or CLUBIN.
- No broad refactors.

## Files changed

- `docs/PUBLIC_REPO_LAUNCH_PLAN.md` (new)
- `docs/PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md` (new, this file)
- `README.md` (Phase 2.24 roadmap bullet)
- `tests/test_phase_2_24_public_repo_launch_plan.py` (new)

## Summary

Phase 2.24 writes the manual, conservative plan the owner can follow
whenever they choose to actually make CLULatent public: what to check
before launch, how to create the GitHub repo safely, what to push and what
not to, suggested first-public wording, caveats to keep visible, a
post-push verification checklist, and rollback notes — all using
placeholder commands only. It recommends **GO WITH CAVEATS**, publishes
nothing, adds no remote, pushes nothing, changes no package version, adds
no intelligence, and does not make CLULatent understand media.
