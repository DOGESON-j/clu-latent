# Public Pre-Alpha Checklist

A concrete, re-runnable checklist for anyone considering showing CLULatent
publicly as a **pre-alpha preview**. This is a readiness aid, not a
publish button — nothing here pushes, tags, or releases anything.

## Positioning (must be clear before showing anyone)

- [x] CLULatent is described as **pre-alpha / experimental**, a
      local developer tool, not a product.
- [x] Package format and Python API are stated as **subject to change**.
- [x] The **evidence-not-truth** trust model is stated up front
      (README status section + [`TRUST_MODEL.md`](TRUST_MODEL.md)).
- [x] No overclaiming: the README does **not** say CLULatent understands
      video, does object recognition, OCR, semantic understanding,
      identity recognition, or is production-ready.

## Repo hygiene (re-check before any public snapshot)

- [x] Runtime demo output (`*.clulatent/` packages,
      `*.clulatent.failure-receipt.jsonl`) is gitignored and **not
      tracked**. Verify:

      ```sh
      git ls-files | grep -E '\.clulatent(/|\.failure)' || echo "clean"
      ```

- [x] No machine-specific / private absolute paths in tracked files
      (e.g. `/Users/<name>`, home directories). Verify:

      ```sh
      git grep -nE '/Users/[a-z]' -- ':!tests/test_security_paths.py' \
        || echo "clean"
      ```

      (The one intentional match, `C:/Users/x` in
      `tests/test_security_paths.py`, is a path-rejection *test fixture*,
      not a real path.)

- [x] No secrets, tokens, or API keys committed. Verify (spot check):

      ```sh
      git grep -niE '(api[_-]?key|secret|token|password)\s*[=:]' -- \
        ':!tests' ':!docs' || echo "clean"
      ```

- [x] No large committed binaries or generated junk beyond the tiny
      tracked `examples/fixture_adapter_result.json`.

## Docs accuracy

- [x] README status section reflects current capability (adapter pipeline
      + one real non-ML adapter), not stale "Phase 1 only" framing.
- [x] No doc hardcodes a specific passing-test count that can silently go
      stale (readiness docs describe status in stable prose instead).
- [x] `docs/PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md` records the
      readiness verdict and caveats.

## Runnable proof (local, offline)

- [x] Tests pass locally:

      ```sh
      pip install -e ".[dev]"
      python -m pytest
      ```

      (Note: an optional, unrelated Whisper/VAD dependency mismatch —
      `torchaudio`/`torchcodec` — currently fails a handful of
      speech-pipeline tests in some environments. These are **not** part
      of the adapter pipeline; see the Phase 2.20 readiness doc.)

- [x] The one-command adapter demo runs and prints an evidence-not-truth
      summary:

      ```sh
      python scripts/demo_real_adapter_workflow.py
      ```

## Explicitly out of scope for pre-alpha

- [ ] Publishing to PyPI — **not done, not planned for this phase.**
- [ ] Creating a GitHub release / tagging a public version — **not done.**
- [ ] Studio UI — **not built.**
- [ ] CLUBIN compiler — **not built.**
- [ ] ML model integration in the core adapter pipeline — **not built.**
- [ ] Production security audit — **not done.**

See [`PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md`](PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md)
for the full readiness write-up and recommendation.
