# Public Repo Launch Plan

A concrete, re-runnable plan for the project owner to follow **manually,
whenever they decide to actually make CLULatent public.** This document
does not launch anything itself — it is a checklist plus command
templates, written so a future launch is deliberate, safe, and
conservative rather than accidental.

**This is not legal advice.** Nothing in this document has been executed
by this phase: no remote was added, nothing was pushed, no GitHub repo or
release was created, and nothing was published to PyPI.

## Recommendation: GO WITH CAVEATS

Phases 2.19–2.23 already established that CLULatent is hygienically clean,
honestly positioned, correctly licensed (BSD 3-Clause), and technically
demonstrable offline, with no release blocker remaining. The
recommendation carried into this launch plan is the same: **GO WITH
CAVEATS** — the caveats being pre-alpha/experimental status and scope, not
safety, hygiene, or licensing. See
[`PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md`](PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md)
for the phase record, and
[`PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md`](PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md)
/ [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md) for the underlying sweep and
gate.

---

## 1. Before creating the public repo (pre-launch checklist)

Re-run these locally, in the repo root, before doing anything else:

- [ ] `python -m pytest` — tests pass (the optional `torchaudio`/
      `torchcodec` speech-pipeline mismatch is a known, documented,
      non-blocking exception; see caveats below).
- [ ] `git status` — working tree is clean; no unintended local edits.
- [ ] `git remote -v` — confirm what remotes (if any) already exist before
      touching remote configuration.
- [ ] `git tag --list | tail` — confirm the expected phase-freeze tags are
      present and no unexpected tag exists.
- [ ] Re-run the [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md) checklist —
      confirm it still reads **GO WITH CAVEATS**.
- [ ] Re-read the README status section top to bottom as a stranger would.
- [ ] Confirm `LICENSE` (root, BSD 3-Clause) and `pyproject.toml`
      (`license = "BSD-3-Clause"`) still match.
- [ ] Decide the exact repository name (this plan assumes `clu-latent`,
      matching `pyproject.toml`'s `name`, but the owner may choose
      differently).
- [ ] Decide the exact GitHub account/organization that will host it. Do
      not fill this into any tracked file until the decision is final —
      use the `YOUR_USERNAME` placeholder shown below until then.

## 2. Creating the repo safely (GitHub repo setup checklist)

- [ ] Create a **new, empty** GitHub repository (do not initialize it with
      a README, `.gitignore`, or license — this repo already has all
      three, and letting GitHub generate its own would create an
      unnecessary merge).
- [ ] Set the repository visibility deliberately (see the visibility flow
      below) — do not default to "public" without an explicit choice.
- [ ] Do **not** enable GitHub features that imply more than this phase
      supports yet (e.g. Discussions framed as a support channel, a
      Releases page implying a stable release, GitHub Pages implying a
      hosted product) until the owner is ready for that surface area.
- [ ] Set the repository description to the suggested public description
      below (or the owner's own conservative variant).
- [ ] Add topics/tags conservatively (e.g. `media`, `evidence`,
      `local-first`, `cli`) — avoid hype tags (`ai`, `video-understanding`,
      `production-ready`).

### Suggested repo visibility flow

1. **Private first (recommended).** Create the GitHub repository as
   **private**, push there, and sanity-check everything renders correctly
   (README, LICENSE badge/link, file tree) with zero audience risk.
2. **Flip to public deliberately.** Once satisfied, switch the repository
   visibility from private to public in GitHub's settings — a single,
   explicit, reversible action taken by the owner, not by any command in
   this plan.
3. **Alternative: public from the start.** If the owner prefers to skip
   step 1, that is a valid choice given the Phase 2.19–2.23 gate/sweep
   results — but it removes the private dry-run safety net, so the
   pre-launch checklist above should be followed extra carefully first.

## 3. Suggested remote and push commands (templates only — do not run yet)

These are **manual commands for the owner to run later**, not instructions
this phase executes. `YOUR_USERNAME` is a placeholder — replace it with the
real account only when actually launching.

```bash
# Add the GitHub remote (only after the GitHub repo above exists):
git remote add origin git@github.com:YOUR_USERNAME/clu-latent.git

# Push the intended default branch (commonly `main`) first:
git push -u origin main

# Push tags separately and deliberately — review the tag list first
# (see the verification commands below) so only intended phase-freeze
# tags go public:
git push origin --tags
```

Notes on this template:

- Use SSH (`git@github.com:...`) or HTTPS
  (`https://github.com/YOUR_USERNAME/clu-latent.git`) consistent with the
  owner's existing GitHub auth setup — either is fine, this plan does not
  prescribe one.
- If the current working branch is a phase branch (e.g.
  `phase-2.24-public-repo-launch-plan`) rather than `main`, merge/fast-
  forward `main` locally first so the *public* default branch is `main`,
  not an internal phase-numbered branch name.
- Do not push every local branch by default (`git push --all`) — push the
  intended public default branch deliberately, then decide case-by-case
  whether any other branch is meant to be public.

### Suggested branch/tag push strategy

- Push **`main`** (or whichever branch the owner designates as the public
  default) first, alone, and verify it on GitHub before pushing anything
  else.
- Push **tags** only after `main` looks correct. Review `git tag --list`
  first — every `phase-*-freeze` tag is internal development history; the
  owner should decide whether all of it, or only a curated subset, should
  be public. Tags are not required for a pre-alpha preview to be useful.
- Do **not** push internal-only, half-finished, or experimental branches
  that were never intended for outside eyes.

## 4. What to push

- The default branch's full tracked history (source, tests, docs,
  `LICENSE`, `pyproject.toml`, `README.md`).
- Optionally, the curated set of `phase-*-freeze` tags, if the owner wants
  the phase history visible.

## 5. What not to push

- Any local `.venv`/`.venv-base` virtual environment (already untracked
  and gitignored).
- Any generated `*.clulatent` package directories or
  `*.clulatent.failure-receipt.jsonl` files from local demo/test runs
  (already gitignored, verified not tracked in Phase 2.20–2.23).
- Any local `__pycache__`, `.pytest_cache`, `*.egg-info`, `build/`,
  `dist/`, or wheel/sdist artifacts (none are tracked today; keep it that
  way — do not `git add -f` around `.gitignore`).
- Any branch or tag not reviewed under the pre-launch checklist above.
- Anything containing a real private absolute path, secret, token, or
  private media filename (Phases 2.20–2.23 already verified none exist in
  the tracked tree; re-run the gate before pushing regardless, since the
  tree can change between phases).

## 6. What the first public README/status should say

The README already carries this positioning (Phases 2.20–2.23); the first
public push should **not** need to change it, only keep it current:

> CLULatent is a local-first media evidence package format. It turns media
> into timestamped, validated, reviewable machine-readable evidence.
> Adapter outputs are evidence, not truth.

Alongside that, the first thing a public visitor should see:

- **Status: pre-alpha (experimental).**
- **Local-first — no cloud, no account, no telemetry.**
- **BSD 3-Clause license**, linked to `LICENSE`.
- What works today (ingest, inspect/query/timeline, validate/lock, the one
  real non-ML adapter, review/correction) vs. what does not (no semantic
  understanding, no Studio UI, no CLUBIN, not production-ready).
- The local test command and the one-command demo.

## 7. Known caveats to keep visible after launch

- **Pre-alpha / experimental** — not a product, not stable; the API and
  package format may change without notice.
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
- **This launch plan itself changes nothing about license, version, or
  scope** — it is a checklist, not a release.

## 8. What not to claim publicly

Avoid all of the following, in the repo description, README, and any
announcement:

- "CLULatent understands video."
- "AI video memory solved."
- "Production-ready."
- "CLUBIN is implemented."
- "Studio UI exists."
- "A general plugin runtime exists."
- "Object recognition / OCR / semantic understanding exists" (in the core
  adapter pipeline — it does not).
- Any specific passing-test count stated as a permanent fact (test counts
  drift; prefer "tests pass locally via `python -m pytest`").
- Any claim that "version 1" or a stable release is ready — the package
  version remains `0.1.0` and pre-alpha.

## 9. Suggested public repo description

A short, conservative, one-line GitHub repository description:

> Local-first media evidence package format (pre-alpha). Turns video into
> timestamped, validated, reviewable evidence — not truth.

## 10. Suggested first announcement text (OPTIONAL / DRAFT — not required, not sent)

This text is a **draft only**. It is not required for this phase, is not
posted anywhere by this phase, and should be reviewed and edited by the
owner (or not used at all) before any actual posting.

> I'm sharing an early, pre-alpha preview of CLULatent — a local-first
> tool that turns a video file into a timestamped, validated,
> machine-readable evidence package on disk. It's offline-only (no
> cloud, no account), BSD-3-Clause licensed, and intentionally modest
> right now: one real, non-ML adapter (an ffmpeg scene-change detector),
> a small CLI, and a trust model where everything an adapter produces is
> "evidence," never "truth," until a human reviews it. Not
> production-ready, API/format may change. Feedback welcome.

## 11. After the repo is public (post-push verification checklist)

- [ ] `git remote -v` from a fresh local clone (or the pushed repo itself)
      — confirm the remote URL is exactly what was intended.
- [ ] Load the GitHub repo page and re-read the rendered README as a
      stranger would (formatting, links, badges all resolve correctly).
- [ ] Click through to `LICENSE` on GitHub and confirm it renders as BSD
      3-Clause.
- [ ] Confirm no unintended file appears in the GitHub file tree (compare
      against `git ls-files` locally).
- [ ] Confirm repository visibility is what was intended (public vs.
      private) in GitHub settings.
- [ ] Confirm no GitHub Action, webhook, or integration was silently
      enabled that the owner did not intend (a stock repo has none by
      default, but re-check if any workflow files exist).
- [ ] Watch for any issue/PR from an outside contributor and respond at
      the owner's own pace — nothing in this plan implies an SLA.

## 12. Rollback / private-again notes

If something looks wrong after pushing (an unintended file, a premature
claim, a mistake in `LICENSE`/`pyproject.toml`, etc.):

- **Flip visibility back to private** in GitHub's repository settings.
  This is immediate and does not require deleting or force-pushing
  anything, and is the safest first response.
- **Fix locally, commit, and push a follow-up commit** — prefer this over
  history rewriting. A public repo's history should be treated as already
  potentially seen/cloned; force-pushing to rewrite history should be a
  last resort, not a routine fix.
- **If a real secret or private path was actually pushed** (not expected,
  given the Phase 2.20–2.23 hygiene sweeps, but treat this as the one case
  that does warrant more drastic action): rotate/invalidate the secret
  immediately regardless of what happens to the git history, then decide
  separately whether history rewriting or a fresh repository is warranted.
  This plan does not pre-authorize a force-push; that remains an explicit,
  separate owner decision made at the time, with the specific incident in
  hand.
- **If public exposure altogether was premature:** delete the GitHub
  repository (a GitHub-side action) or simply leave it private
  indefinitely. The local repository and its history are unaffected
  either way.

---

## Explicitly out of scope for this plan

- No remote was added by this phase.
- No push, GitHub repo, GitHub release, or PyPI publish was performed by
  this phase.
- No package version change.
- No license-family change.
- No new adapter architecture, real adapter, ML dependency, semantic truth
  generation, dynamic plugin loading, Studio UI, or CLUBIN.

See [`PUBLIC_REPO_GATE.md`](PUBLIC_REPO_GATE.md) for the underlying safety
gate this plan builds on, and
[`PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md`](PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md)
for this phase's record.
