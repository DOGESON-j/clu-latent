# Phase 3.20 — V1 Open/Ask Demo Kit v0

## What this phase is for

Phase 3.19 made the V1 loop real: a normal video becomes a validated,
evidence-carrying `.clulatent` package (`clulatent build-video`), and a
single command emits a stable agent-context document
(`clulatent agent-context export`).

Phase 3.20 makes that document **usable** without adding a single new
evidence lane:

> "I built a `.clulatent` file from a video. Now I want CLU, Codex, or
> another agent to open that file and tell me about the video **using
> evidence**."

Two small, read-only, non-LLM commands:

- `clulatent agent-context summarize PACKAGE` — a short, human-readable,
  **evidence-only** summary of what a package contains.
- `clulatent agent-context prompt PACKAGE [--output ask_prompt.md]` — a
  ready-to-paste agent prompt that forces a downstream agent to answer
  strictly from evidence.

Both are pure re-presentations of the Phase 3.19 agent context. They open
the package through the Phase 3.18 reader, add no evidence, and never
mutate the package or write a receipt.

## Non-goals (hard boundaries)

- **No new evidence lanes** and **no semantic video understanding.**
- **No LLM / OpenAI / Claude / network / vision-model / new-ML call.** The
  `prompt` command *generates* a prompt; it never sends it anywhere.
- **No claim about people, objects, actions, intent, identity, or scene
  meaning.** The summary emits *candidate evidence only*.
- No mutation, no receipt, no lock change, no FFmpeg/Pillow invocation.

Allowed language in the summary: "visual-change candidate",
"changed-region candidate", "evidence bundle", "agent review",
"candidate evidence", "requires human review", "unsupported by available
evidence", "timeline window contains evidence records", and lane-status
phrasings ("present", "present but empty", "unavailable (never
generated)"). Forbidden: "person appeared", "object moved", "car entered",
"face changed", "someone said", "this means", "intent", "identity",
"confirmed scene", "the video shows X" (unless X is directly a
package/evidence fact).

## Design notes

- **New module: `src/clu_latent/v1_open_ask.py`.** It builds entirely on
  `agent_context.build_agent_context(package_path)` — the same read-only
  path Phase 3.19 uses. It never walks the package folder, never assumes a
  track filename, and never opens a track file directly. Because it reuses
  the agent-context builder, the read-only / no-mutation / no-receipt /
  reader-only guarantees are inherited for free.
- **Two public entry points:** `build_evidence_summary(package_path)` and
  `build_ask_prompt(package_path)`. Each catches
  `agent_context.AgentContextError` and re-raises `OpenAskError` so the CLI
  fails cleanly.
- **Forbidden-language guard.** `assert_summary_no_forbidden_language`
  scans only the strings this module *authors* — section labels, lane
  status lines, and the caveats/next-steps carried over from the agent
  context (already guarded there). It never scans opaque, user-controlled
  data (a package id, a source filename, a track id), so a video literally
  named `identity.mp4` never trips the guard. The banned set is the
  agent-context `FORBIDDEN_CONTEXT_PHRASES` plus `"confirmed scene"`.
- **The ask prompt is authored instruction text and is exempt from the
  scan** — it deliberately *names* the forbidden concepts (people, objects,
  actions, intent, identity, scene meaning) in order to prohibit them to
  the downstream agent.
- **CLI printing uses `typer.echo`, not `console.print`.** The summary
  contains literal `[0-31729 ms]` windows that Rich would try to parse as
  markup.

## Files

- `src/clu_latent/v1_open_ask.py` (new) — summary + prompt rendering,
  forbidden-language guard, `OpenAskError`.
- `src/clu_latent/cli.py` — adds `agent-context summarize` and
  `agent-context prompt` under the existing `agent-context` group, plus the
  `from . import v1_open_ask as v1_open_ask_mod` import.
- `tests/test_v1_open_ask_demo.py` (new).
- `README.md` — Phase 3.20 bullet.

## The V1 demo workflow (copy-paste)

Set a source video and an output directory:

```bash
MOVIE="/path/to/synthetic-or-rights-cleared-demo.mp4"
OUT="/tmp/clulatent-320-open-ask-demo"
mkdir -p "$OUT"
```

### 1. Build a `.clulatent` package from the video (Phase 3.19)

```bash
# Full profile (needs the optional `visual` extra / Pillow for the
# visual-change lane); use --allow-partial to skip visual lanes instead.
clulatent build-video "$MOVIE" -o "$OUT/movie.clulatent" --profile v1
```

### 2. Validate and inspect it (existing commands)

```bash
clulatent validate "$OUT/movie.clulatent"
clulatent inspect  "$OUT/movie.clulatent"
```

### 3. Export the agent context (Phase 3.19)

```bash
clulatent agent-context export "$OUT/movie.clulatent" --output "$OUT/ctx.json"
clulatent agent-context export "$OUT/movie.clulatent" --output "$OUT/ctx.md" --format markdown
```

### 4. Evidence-only summary (Phase 3.20 — new)

```bash
clulatent agent-context summarize "$OUT/movie.clulatent"
```

### 5. Export the agent ask prompt (Phase 3.20 — new)

```bash
clulatent agent-context prompt "$OUT/movie.clulatent" --output "$OUT/ask_prompt.md"
# or print it to stdout:
clulatent agent-context prompt "$OUT/movie.clulatent"
```

### 6. Ask an agent

Open `$OUT/ask_prompt.md`, paste the contents of `$OUT/ctx.md` (or
`ctx.json`) where it says `<<< paste clulatent agent-context here >>>`, and
hand the whole thing to CLU / Codex / any agent. The prompt constrains the
agent to answer only from the evidence and to report unknowns.

## Example: `summarize` on the bundled sample package

```
============================================================
CLULatent evidence summary - 73b37b24-4ef8-408b-8445-e203af7ba594
============================================================
Package facts:
  package id:        73b37b24-4ef8-408b-8445-e203af7ba594
  duration:          31729 ms
  source:            Creation of CLU 2.0 - Cyber (720p, h264).mp4 (20fb756e3093...)
  status:            complete
  clulatent version: 0.1.0
Validation and lock:
  validation:        valid (0 error(s), 0 warning(s))
  lock:              unlocked
Tracks present (4 track(s); 34 event record(s)):
  keyframes: present (32 record(s))
  audio_events: present (2 record(s))
  speech_events: present but empty
  visual_change_candidates: unavailable (never generated)
  changed_region_candidates: unavailable (never generated)
  evidence_bundles: unavailable (never generated)
  agent_review_events: unavailable (never generated)
Evidence (candidate only):
  no candidate evidence lanes present
Major evidence windows:
  [0-31729 ms] timeline window contains 34 evidence records across tracks ['audio_events', 'keyframes']
Evidence bundle IDs (0):
  none
Agent review IDs (0):
  none
Unavailable / missing evidence:
  visual_change_candidates
  changed_region_candidates
  evidence_bundles
  agent_review_events
Caveats:
  - This document describes candidate evidence only; it carries no semantic interpretation.
  - No object, person, face, action, scene, speech, or purpose claim is made or implied.
  - Visual-change and changed-region entries are numeric pixel-difference candidates, not detections.
  - Evidence bundles collect existing package records for a time range; they do not establish what happened.
  - Agent review status reports evidence support and gaps only; it does not confirm any event.
  - Everything here needs human review before it is treated as canonical.
Safe next steps:
  - Run visual-change analysis to add pixel-difference candidates.
  - Build an evidence bundle to collect existing evidence for a time range.
  - Use event_counts and time_coverage to decide where a human should look.
============================================================
```

## Verification

```bash
python -m pytest tests/test_v1_open_ask_demo.py -q
python -m pytest tests/test_agent_context_export.py tests/test_v1_build_pipeline.py \
                 tests/test_package_reader.py tests/test_evidence_bundle.py \
                 tests/test_agent_review.py -q
clulatent agent-context --help
clulatent agent-context summarize --help
clulatent agent-context prompt --help
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
