# Phase 3.21 — V1 Ask Bundle / Agent Handoff v0

## What this phase is for

- Phase 3.19 made a `.clulatent` package **buildable** from a video.
- Phase 3.20 made it **explainable** (an evidence-only summary and a safe
  ask prompt).
- Phase 3.21 makes it **askable**:

> "I have a `.clulatent` package. I want to ask a question about the video,
> and CLULatent should produce a safe, evidence-grounded ask bundle that
> Codex/CLU/another agent can use without spelunking through package
> internals."

One command:

```bash
clulatent ask PACKAGE "QUESTION" --output ask_bundle.md
```

It produces a ready-to-paste **ask bundle**: the user's question, package
facts, what candidate evidence exists (and what is missing), a relevant
evidence window when the question names a timestamp, and the rules an agent
must follow to answer strictly from evidence.

**This is not an answer.** It is the safe packet that lets an AI answer
from evidence.

## Non-goals (hard boundaries)

- **No LLM / OpenAI / Claude / network / local vision model / new ML
  dependency.** The command *generates* a bundle; it never answers.
- **No semantic interpretation** and **no new evidence lanes.**
- **No claim about people, objects, actions, intent, identity, or scene
  meaning.**
- No mutation, no receipt, no lock change, no FFmpeg/Pillow invocation.
- No unrelated refactors.

Allowed language: "available evidence", "candidate evidence",
"visual-change candidate", "changed-region candidate", "evidence bundle",
"agent review", "unsupported by this package", "unknown from current
evidence", "requires human review". Forbidden in authored prose: "person
appeared", "object moved", "car entered", "face changed", "someone said",
"intent", "identity", "scene meaning", "confirmed scene", "the video shows
X" (unless X is directly a package/evidence fact).

## Design notes

- **New module: `src/clu_latent/v1_ask_bundle.py`.**
  - Package facts come from `agent_context.build_agent_context(package)` —
    the same read-only path Phases 3.19/3.20 use (which opens the package
    through the Phase 3.18 reader). No folder walking, no filename
    assumptions.
  - A timestamp window, when present, is resolved with
    `PackageReader.query_time(start_ms, end_ms)`. Each returned record is
    presented with its opaque fields only (id, track, type, timestamps) —
    never any interpretation.
  - Public entry point `build_ask_bundle(package, question, *,
    output_format="markdown"|"json")`; raises `AskBundleError` on failure.

- **Question handling is deliberately small (no NLP).**
  - `classify_question` picks one broad, non-semantic intent:
    `package_overview`, `timeline_overview`, `evidence_near_time`,
    `unsupported_or_unknown`, `general_question`. A timestamp wins first;
    then a semantic "what happened / who / meaning" question is flagged
    `unsupported_or_unknown`; then overview/timeline keywords; otherwise
    `general_question`. The classifier never answers or interprets — it
    only chooses a framing.
  - `parse_time_query` recognizes `00:04` / `0:04` (mm:ss), `hh:mm:ss`,
    `4s`, `4000ms`, and ranges like `0:04-0:10`. A single instant is
    padded ±2 s (`DEFAULT_WINDOW_PAD_MS`) into a window and clamped to
    `[0, duration_ms]`. **Bare integers are intentionally not matched** so
    "I saw 4 objects" is not read as a timestamp.
  - A "what happened" style question is translated into evidence-safe
    framing ("Summarize available evidence records and caveats. Do not
    infer scene meaning.") rather than answered.

- **Forbidden-language guard.** `assert_bundle_no_forbidden_language`
  scans only the strings this module *authors* — section labels and the
  caveats carried over from the agent context (already guarded there). It
  never scans:
  - **the user's question** (quoted verbatim as a blockquote — user input,
    not CLULatent's claim; it may legitimately contain banned words), or
  - the **Safe Answering Instructions / Forbidden Claims / Question
    Framing** blocks, which *name* the forbidden concepts on purpose in
    order to prohibit them to the downstream agent, or
  - opaque interpolated data (package id, source filename, track name,
    event type).

  (Note: the visible section is titled **"Question Framing"**, not
  "Interpreted Intent" — the latter contains the substring "intent", a
  banned phrase, and would false-positive the guard. The internal API
  constants are still named `INTENT_*`.)

- **CLI: top-level `clulatent ask`.** A natural verb, printed with
  `typer.echo` (not `console.print`) because the bundle contains literal
  `[…]` windows and markdown that Rich would try to parse as markup.

## Files

- `src/clu_latent/v1_ask_bundle.py` (new) — classifier, timestamp parser,
  bundle rendering (markdown + JSON), guard, `AskBundleError`.
- `src/clu_latent/cli.py` — adds the `ask` command and the
  `from . import v1_ask_bundle as v1_ask_bundle_mod` import.
- `tests/test_v1_ask_bundle.py` (new).
- `README.md` — Phase 3.21 bullet.

## Bundle sections

```
# CLULatent Ask Bundle
## User Question              (verbatim, quoted)
## Question Framing           (broad non-semantic class + evidence-safe framing)
## Package Facts              (id, duration, source, status, version, validation, lock)
## Track Summary              (per-core-lane presence + counts)
## Evidence Available         (candidate counts only)
## Relevant Evidence Window   (query_time results, if a timestamp was given)
## Evidence Bundles           (IDs + ranges)
## Agent Reviews              (IDs + review status)
## Unavailable / Missing Evidence
## Caveats                    (carried from the agent context)
## Safe Answering Instructions
## Forbidden Claims
## Suggested Answer Format
## Source Context References   (how to fetch the full context)
```

## Demo workflow (copy-paste)

```bash
MOVIE="/path/to/synthetic-or-rights-cleared-demo.mp4"
OUT="/tmp/clulatent-321-ask-bundle-demo"
mkdir -p "$OUT"

# Build a package from the video (Phase 3.19); --allow-partial if Pillow is absent.
clulatent build-video "$MOVIE" -o "$OUT/movie.clulatent" --profile v1 --force

# Ask a question -> safe handoff bundle.
clulatent ask "$OUT/movie.clulatent" "What evidence exists around 00:04?" --output "$OUT/ask_bundle.md"
```

Then open `$OUT/ask_bundle.md` and paste it into Codex / CLU / any agent.
The bundle constrains the agent to answer only from the package's evidence
(citing ids/timestamps), report unknowns as *unsupported by this package*,
and never infer people/objects/actions/intent/identity/scene meaning — so
the agent can answer without ever reading the package's internal file
structure.

JSON is also available for programmatic handoff:

```bash
clulatent ask "$OUT/movie.clulatent" "around 0:04-0:10" --format json --output "$OUT/ask_bundle.json"
```

## Verification

```bash
python -m pytest tests/test_v1_ask_bundle.py -q
python -m pytest tests/test_v1_open_ask_demo.py tests/test_agent_context_export.py \
                 tests/test_v1_build_pipeline.py -q
python -m pytest tests/test_package_reader.py -q
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
