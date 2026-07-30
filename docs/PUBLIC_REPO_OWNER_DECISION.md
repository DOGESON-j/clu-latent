# Public Repo Owner Decision

A short, practical record of the current live decision on whether
CLULatent should be made public. This is a pointer, not a duplicate of the
launch plan — see [`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md)
for the full manual checklist and command templates, and
[`PHASE_2_25_PUBLIC_REPO_OWNER_DECISION.md`](PHASE_2_25_PUBLIC_REPO_OWNER_DECISION.md)
for the full decision write-up.

## Current decision: GO WITH CAVEATS

Decided in Phase 2.25 (2026-07-10), base: Phase 2.24 frozen
(tag `phase-2.24-public-repo-launch-plan-freeze`). The repository is
hygienically clean, honestly positioned, and BSD-3-Clause licensed; no
release blocker remains.

## Caveats (must stay visible)

- Pre-alpha / experimental — API and package format may change.
- Only one real, non-ML adapter exists (plus a fixture adapter).
- Optional `torchaudio`/`torchcodec` speech-pipeline dependency issue is
  separate and non-blocking.
- No Studio UI. No CLUBIN. Not production security-audited.

## Launch checklist

See [`PUBLIC_REPO_LAUNCH_PLAN.md`](PUBLIC_REPO_LAUNCH_PLAN.md) for the
full pre-launch checklist, GitHub repo setup, command templates, and
post-push verification.

## Not yet published by this phase

No remote was added or changed. Nothing was pushed. No GitHub repository,
release, or PyPI package was created. No package version changed.
Publishing remains a separate, manual, owner-driven action.
