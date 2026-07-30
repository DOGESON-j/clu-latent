# Public Repo Gate

A single, re-runnable gate to decide whether the CLULatent repository is
safe to make **public**. This complements
[`PUBLIC_PRE_ALPHA_CHECKLIST.md`](PUBLIC_PRE_ALPHA_CHECKLIST.md) (which
covers positioning/wording) by focusing on the hard **release blockers**:
license and repository safety.

**This is not legal advice.** Nothing here publishes, pushes, tags, or
releases anything.

## Gate status legend

- ✅ pass — no action needed.
- ⛔ blocker — must be resolved by the owner before public reuse.

## 1. License (✅ pass — resolved)

- ✅ **Public-use license chosen: BSD 3-Clause.** A root `LICENSE` file
  carries the full BSD 3-Clause text (`Copyright (c) 2026 Jayden Kambule`);
  `pyproject.toml` declares `license = "BSD-3-Clause"` with
  `license-files = ["LICENSE"]`; the README states the same. Reuse rights
  are granted under BSD 3-Clause terms. See
  [`LICENSE_DECISION.md`](LICENSE_DECISION.md). (Resolved in Phase 2.22;
  the earlier "decision pending" blocker no longer applies.)

## 2. Secrets & credentials (✅ pass)

- ✅ No API keys, tokens, secrets, or passwords in tracked files. Verify:

  ```sh
  git grep -niE '(api[_-]?key|secret|token|password|bearer)\s*[=:]' \
    -- ':!tests' ':!docs' || echo "clean"
  ```

## 3. Private / machine-specific paths (✅ pass)

- ✅ No real private absolute paths in tracked files. The only `/Users/...`
  occurrences are (a) documentation *describing* the check and (b) a
  path-rejection **test fixture** (`C:/Users/x` in
  `tests/test_security_paths.py`) and the Phase 2.20/2.21 guard tests that
  assert such paths are absent. Verify:

  ```sh
  git grep -nE '/Users/[a-z]' \
    -- ':!tests' ':!docs/PUBLIC_PRE_ALPHA_CHECKLIST.md' \
       ':!docs/PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md' \
       ':!docs/PUBLIC_REPO_GATE.md' || echo "clean"
  ```

## 4. Runtime demo output not tracked (✅ pass)

- ✅ `.clulatent` packages and `*.clulatent.failure-receipt.jsonl` are
  gitignored and not committed. Verify:

  ```sh
  git ls-files | grep -E '\.clulatent(/|\.failure)' || echo "clean"
  ```

## 5. No large binaries / private media (✅ pass)

- ✅ No tracked media (`.mp4`/`.mov`/`.wav`/`.jpg`/`.png`) or large
  binaries. The only tracked data artifact is the tiny, deterministic
  `examples/fixture_adapter_result.json`. Verify:

  ```sh
  git ls-files | grep -iE '\.(mp4|mov|wav|jpg|jpeg|png|bin)$' \
    || echo "clean"
  ```

## 6. Honest positioning (✅ pass)

- ✅ README marks the project **pre-alpha / experimental**.
- ✅ README carries the **evidence-not-truth** trust framing and links
  [`TRUST_MODEL.md`](TRUST_MODEL.md).
- ✅ README states **local-first / no cloud**.
- ✅ README does **not** claim CLULatent understands video, or that object
  recognition / OCR / semantic understanding / identity recognition / ML
  are implemented in the core.
- ✅ README states **no CLUBIN**, **no Studio UI**, **no core ML
  requirement** for the current demo.
- ✅ Optional-dependency caveat (`torchaudio`/`torchcodec`) documented.
- ✅ Test command and one-command demo command documented.

## 7. Docs accuracy (✅ pass)

- ✅ No public doc hardcodes a passing-test count as hard truth; readiness
  docs use stable prose.
- ✅ No doc claims production readiness or a completed security audit.

## Overall gate result: GO WITH CAVEATS

The repository is **hygienically** clean and honestly positioned, and the
prior gating blocker — licensing — is now **resolved**: CLULatent is
licensed **BSD 3-Clause** with a matching root `LICENSE`, `pyproject.toml`
metadata, and README statement (Section 1). No release blocker remains.

The recommendation is therefore **GO WITH CAVEATS**, where the caveats are
the pre-alpha / labelling ones from Phase 2.20 (experimental, API/format
may change, one real non-ML adapter, optional `torchaudio`/`torchcodec`
dependency mismatch in the unrelated speech pipeline) — not licensing.

Reminder: passing this gate authorizes nothing automatically. Actually
making the repo public (adding a remote, pushing, creating a release,
tagging a public version, uploading to an index) remains a separate,
explicit, owner-driven step. This document publishes nothing.

See [`PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md`](PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md)
and [`PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md`](PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md).
