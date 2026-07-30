# License Decision (Resolved)

**This is not legal advice.** This document records the license decision
for the CLULatent repository. The decision has been **made**: CLULatent is
licensed under the **BSD 3-Clause License**.

## Decision

- **Chosen license:** BSD 3-Clause License.
- **Copyright line:** `Copyright (c) 2026 Jayden Kambule`.
- **Decided in:** Phase 2.22 (License Selection and Metadata).
- **Prior state:** Phase 2.21 audited the repo as `Proprietary` with no
  `LICENSE` file and no reuse grant (decision pending). That state is now
  superseded.

## Current declared state (Phase 2.22)

| Where | What it says |
|---|---|
| `LICENSE` (root) | Full BSD 3-Clause text, `Copyright (c) 2026 Jayden Kambule` |
| `pyproject.toml` | `license = "BSD-3-Clause"` (SPDX expression) + `license-files = ["LICENSE"]` |
| `README.md` | States CLULatent is licensed under the BSD 3-Clause License, links `LICENSE` |
| Other docs | Consistent; no conflicting or "pending" license claim remains active |

**Interpretation:** BSD 3-Clause is a permissive open-source license. Use,
copying, modification, and redistribution are permitted provided the
copyright notice, the list of conditions, and the disclaimer are retained,
and the copyright holder's/contributors' names are not used to endorse
derived products without permission. The pre-alpha/experimental status is
independent of the license — the software may still change.

## Checklist (all complete)

- [x] Decide direction — **BSD 3-Clause** (permissive OSS).
- [x] Add a root `LICENSE` file with exact BSD 3-Clause text and the
      copyright line.
- [x] Make `pyproject.toml` `license` match (SPDX `BSD-3-Clause`).
- [x] Make `README.md` license status match.
- [x] Confirm no conflicting license statements remain active in docs.

## Notes for any future change

- Do not change the license family without an explicit, deliberate owner
  decision; if changed, update `LICENSE`, `pyproject.toml`, `README.md`,
  and this document together.
- If third-party code/assets are ever vendored, confirm their licenses are
  compatible and carry their required notices.

See [`PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md`](PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md)
and [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md).
