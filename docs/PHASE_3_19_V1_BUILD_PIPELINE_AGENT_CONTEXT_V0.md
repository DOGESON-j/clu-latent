# Phase 3.19 — V1 Build Pipeline + Agent Context Export v0

## Summary

Everything before this phase gave `.clulatent` its *parts*: an ingest
step, a validator, a visual-change lane, a changed-region lane, evidence
bundles, agent review, and (Phase 3.18) a read-only reader. Using them
meant running six or seven separate commands in the right order and
knowing which lane feeds which. This phase adds the two pieces that make
`.clulatent` feel like a **real, openable knowledge file** to a person
*and* to another agent:

1. **`clulatent build-video`** — one command that takes a normal video
   and runs the whole deterministic local pipeline end to end (ingest →
   validate → visual-change → changed-regions → evidence bundle → agent
   review → validate), then prints a concise build summary. New module
   `src/clu_latent/v1_build.py`.
2. **`clulatent agent-context export`** — one command that opens a built
   package *through the Phase 3.18 reader* (never by walking folders) and
   emits a single stable JSON (or Markdown) document another agent —
   CLULatent, Codex, anything — can read to understand **what evidence
   exists**, **what it is not allowed to conclude**, and **what to safely
   do next**, without knowing the internal folder layout. New module
   `src/clu_latent/agent_context.py`.

This is the smallest serious **V1 slice**. It adds **no** new evidence
lane, no semantic interpretation, no object/person/action/scene/speech/
intent claim, no network/model/LLM/vision call, and no new ML
dependency. It only *orchestrates* writers that already exist and
*reads* records they already wrote.

## Why this phase exists

- **The parts existed; the product didn't.** A user should be able to go
  from a raw video to an inspectable, validated, evidence-carrying
  package in a single command, and an agent should be able to open that
  package and get its bearings from one document. Until now both required
  insider knowledge of the pipeline order and the folder layout.
- **Agents need a contract, not a folder.** An external agent that reads
  `tracks/*.jsonl` directly is coupled to the format's private shape and
  is one rename away from breaking. The agent-context export is a stable,
  versioned surface (`schema_id: clulatent.agent_context.v0`) built on the
  reader, so the folder layout stays private.
- **"Untrusted, candidate, needs-review" has to travel with the data.**
  The most important field in the export is not the evidence — it is the
  **caveats**. A downstream agent must be told, in the same document, that
  everything here is candidate evidence only, carries no semantic meaning,
  and needs human review before it is treated as canonical.

## Command 1 — `clulatent build-video`

```
clulatent build-video INPUT_VIDEO -o OUTPUT_PACKAGE --profile v1 [--force] [--allow-partial]
```

Deterministic, local-only pipeline (`v1_build.build_v1_package`):

1. **ingest** the video into a `.clulatent` package (`ingest_video`).
2. **validate** the freshly-ingested package (`validate_package`).
3. **visual-change** analysis — *only if* the optional `visual` extra
   (Pillow) is available (`visual_change.is_available()`).
4. **changed-regions** analysis — only if step 3 produced a visual-change
   track (`changed_region.is_available()`).
5. **evidence bundle** — build one bundle over the full package window
   `[0, duration_ms]` (`build_evidence_bundle`). One full-package bundle
   is the simplest "≥1 bundle" that always has a well-defined range.
6. **agent review** — run one rule-based review over the bundle from
   step 5 (`run_agent_review`).
7. **validate again** — confirm the package is still valid after all the
   additive writes.
8. **print a concise build summary** — package path, duration, per-track
   record counts, receipts written, validation status before *and* after,
   and the generated bundle / review ids.

### Pillow (visual extra) policy — fail clearly, never silently degrade

The default `--profile v1` promises the visual lanes. If Pillow is not
installed, `build-video` **fails clearly** with an actionable message
(install the `visual` extra) and writes nothing beyond the ingested
package — it does **not** silently produce a weaker package while still
claiming `--profile v1`. The explicit escape hatch is `--allow-partial`:
with it, the visual lanes are skipped, the build continues (still
producing an evidence bundle + agent review over whatever evidence
exists), and the summary is clearly marked `partial: true` with the
skipped lanes named. Silent degradation is the one thing this command
must never do.

### Lock / force behavior

`build-video` writes through the existing writers, so it inherits their
lock behavior unchanged: a valid **integrity lock** refuses every write
(no `--force` escape hatch for that), the **operation lock** serializes
writers, and `--force` only affects ingest overwrite + re-running an
already-present lane/bundle. `--force-stale-lock` is threaded through for
the ingest step's stale-lock recovery.

## Command 2 — `clulatent agent-context export`

```
clulatent agent-context export PACKAGE --output agent_context.json [--format json|markdown]
```

`agent_context.build_agent_context(package_path)` opens the package with
`package_reader.open_package` and assembles a single plain-JSON document
(dicts / lists / strings / numbers / booleans / null only — nothing that
requires custom decoding). It **never** walks the folder tree itself and
**never** mutates the package or writes a receipt.

Stable envelope: `schema_id = "clulatent.agent_context.v0"`,
`schema_version = "0.1.0"`.

Top-level sections:

| key | what it carries |
|---|---|
| `package` | package id, status, created_at, clulatent version, duration, has_audio, and the **source** filename + sha256 |
| `validation` | `valid`, `error_count`, `warning_count` (from the validator, via the reader) |
| `lock` | lock status + `hash_valid` |
| `tracks` | one entry per declared track: name, record_count, `known` |
| `track_summary` | known/unknown track counts, unknown track names, total events |
| `event_counts` | per-track event count, keyed by track **name** |
| `time_coverage` | package `[start_ms, end_ms]` and which tracks carry events |
| `receipts` | receipt files present in the package |
| `evidence` | visual-change / changed-region strength counts, evidence-bundle summaries (counts/coverage/missing), agent-review statuses |
| `timeline` | a compact, bounded, chronological list of events (track name + id + type + time), truncation-flagged |
| `caveats` | **the load-bearing section** — candidate-only, no-semantics, needs-review |
| `unavailable_evidence` | canonical lanes *not* present in this package |
| `next_steps` | safe, non-semantic suggested actions (e.g. "run visual-change analysis", "have a human review bundle X") |

`--format markdown` renders the same document as a readable brief; the
JSON is the contract, the Markdown is a convenience view.

### Forbidden-language guard

`agent_context` carries a guard (`assert_no_forbidden_language`) run over
every string the module **authors** (caveats, next-steps, section notes —
*not* opaque data like a source filename, which a user controls). It
rejects the banned semantic vocabulary — "person appeared", "object
moved", "car entered", "face changed", "someone said", "the video shows",
"this means", "confirmed event", and the bare claims "intent" /
"identity" / "truth" — so a future edit that smuggles a semantic claim
into the authored prose fails a test instead of shipping. The guard
deliberately does **not** scan user-controlled data fields, so a video
literally named `truth.mp4` never trips it.

## Example agent prompt

A downstream agent should be handed the exported JSON with a framing like:

> You are given a `clulatent.agent_context.v0` document describing a
> `.clulatent` package. It lists **candidate evidence only**. It contains
> **no** semantic interpretation: no object, person, face, action, scene,
> speech, or intent claim is made or implied. Visual-change and
> changed-region entries are numeric pixel-difference candidates, not
> detections. Do **not** infer what the video depicts, who or what is
> present, or what anything means. Use `event_counts`, `time_coverage`,
> and `evidence` to decide *where* a human should look, and use
> `next_steps` for safe follow-up actions. Treat nothing here as
> confirmed; everything needs human review before it is canonical.

## Tests

- `tests/test_v1_build_pipeline.py` — build order, validate-before /
  validate-after, writer invocation when the visual extra is available,
  clear failure when Pillow is absent under default `--profile v1`,
  `--allow-partial` continues and marks the result partial, bundle +
  review generation, summary contents, lock safety, and no semantic
  claim in the summary. Writers are monkeypatched so the suite is fast
  and needs no real movie / no Pillow / no FFmpeg.
- `tests/test_agent_context_export.py` — schema id/version stability,
  every section present, `caveats` non-empty, `next_steps` present, the
  reader (not folder walking) is the source, no forbidden semantic
  language, Markdown render, and that export mutates nothing and writes
  no receipt.

## Non-goals

- No new evidence lane, track type, writer, or format change.
- No semantic interpretation; no object/person/action/scene/speech/intent
  claim, ever.
- No network, model, LLM, ML, FFmpeg, or Pillow call *added* by these two
  modules (the visual lanes still use Pillow via the existing writers,
  gated by `is_available()`).
- The agent-context module never mutates a package, writes a receipt, or
  touches lock state.
- Not a stability promise beyond the v0 envelope — this is the baseline a
  future `agent_context.v1` is measured against.

## Verification

```bash
python -m pytest tests/test_v1_build_pipeline.py tests/test_agent_context_export.py -q
clulatent build-video --help
clulatent agent-context export --help
python -m pytest -q
```
