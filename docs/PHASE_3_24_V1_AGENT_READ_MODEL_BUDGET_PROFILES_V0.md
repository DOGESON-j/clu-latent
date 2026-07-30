# Phase 3.24 — V1 Agent Read Model + Budget Profiles v0

CLULatent now gives agents a progressive, bounded evidence read model. The
playback viewer is for humans. The agent-read model is for agents. Both point
at the same evidence.

Raw track dumps are a poor prompt interface: they spend tokens on repeated
envelopes and paths while encouraging an agent to infer meaning that the
package does not establish. `clulatent agent-read` instead supplies package
facts, time-bounded evidence windows, evidence-safe rankings, retrieval hooks,
caveats, and explicit safe-answer rules.

## Budget profiles

- `micro` is a 50–200 word routing packet with duration, validation, track and
  event counts, a few inspection candidates, and a strict caveat.
- `summary` is a compact overview with tracks, coverage, top windows, and safe
  next actions.
- `standard` is the normal agent map, bounded to 64 one-second windows by
  default, with rankings and retrieval hooks.
- `full` is richer but remains bounded to 256 windows; it does not dump raw
  package tracks.

`--max-windows` can lower the profile cap, and `--window-ms` changes the export
window size. Focused lookup accepts either `--time 17s` or `--start`/`--end`.

## Commands

```text
clulatent agent-read summary PACKAGE
clulatent agent-read export PACKAGE --output agent_read.json
clulatent agent-read export PACKAGE --format markdown --budget full --output agent_read.md
clulatent agent-read window PACKAGE --time 17s
clulatent agent-read window PACKAGE --start 17s --end 18s
clulatent agent-read write-index PACKAGE
```

Each evidence window contains a stable `ew_XXXXXX` ID, millisecond bounds and
timecodes, evidence density, nearby/overlapping event IDs, package-relative
keyframe paths when available, bundle/review coverage, missing evidence,
caveats, and a focused retrieval command. Rankings are deliberately named
`evidence-dense windows` and `inspection candidates`, never “important
scenes.”

## Package-contained artifacts

`agent-read write-index` atomically writes only these derived files:

```text
index/v1/agent_read_manifest.json
index/v1/agent_read_micro.md
index/v1/agent_read_model.json
index/v1/agent_read_model.md
index/v1/agent_read_windows.jsonl
```

The manifest records generation metadata, validation and track counts,
bundle/review IDs, artifact sizes, and SHA-256 hashes. A valid package
integrity lock blocks writing. No receipt or evidence lane is created.

## Relationship to existing surfaces

The model uses `PackageReader` for package access and `agent_context` for the
validated overview contract. The playback viewer remains the human evidence
surface; ask bundles remain question-specific handoff packets. Agent-read sits
between them: it orients an agent cheaply, ranks where evidence is dense, and
provides hooks for focused retrieval before an answer is attempted. Phase 3.22
package-index lock and atomic-write behavior is preserved; automatic
build-video generation is intentionally not added in this phase.

## Safety and limitations

This is not an answer to what happened and not semantic certification. It does
not call an LLM, network API, OCR, vision model, object detector, FFmpeg, or
Pillow. It does not process new media. Visual-change candidates remain numeric
change evidence, changed-region candidates remain numeric localization of
change, and agent reviews remain reviews of evidence. Agents must cite
timestamps and event IDs, use “candidate evidence” language, retrieve focused
windows, and request human review when available evidence is insufficient.
