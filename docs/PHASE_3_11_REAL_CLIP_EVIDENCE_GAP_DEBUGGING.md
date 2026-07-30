# Phase 3.11 — Real Clip Evidence Gap Debugging Harness

Base: Phase 3.10 Audio Digest Demo Workflow is frozen
(tag `phase-3.10-audio-digest-demo-workflow-freeze`).

No new module. No new adapter. No new CLI command. No new validation
rule, retrieval function, writer, or dependency. This phase adds a
single, small, **read-only** diagnostic script --
`scripts/debug_real_clip_evidence_gap.py` -- that answers one honest
question about a `.clulatent` package:

> **Given only this package, can CLULatent determine what happens in
> the clip?**

For every package this codebase can produce today, the correct answer
is almost always **NO — insufficient evidence**. This harness exists to
make that gap explicit and auditable, so a downstream caller never
quietly assumes a package that merely *validates* also *explains* its
clip.

## Why this phase exists

The audio digest stack (Phase 3.4–3.10) can now write, validate,
receipt, retrieve, and demo audio digest evidence end-to-end. But every
one of those records is still either absent or **synthetic/demo**
evidence — there is no real audio adapter, and there is no visual
interpretation lane at all. It would be easy for a caller to look at a
green "package is valid" and assume CLULatent *understands* the clip.
It does not. This harness is a truth-teller: it reports what evidence a
package actually holds, names every gap, flags synthetic/demo evidence
as not-real-analysis, and refuses to invent a story.

## What the script reports (only what is present, never inferred)

- **Package validity** (via the existing `validate.validate_package` —
  unchanged).
- **Duration and resolution** (from `manifest.source`).
- **Keyframe count** (from `manifest.media.keyframes`) — reported as
  *present*, never as "visual events".
- **Audio presence** (from `manifest.source.has_audio` / an
  `audio_events` track) — reported as *present*, never as audio
  meaning.
- **Track inventory** (the manifest's declared tracks).
- **Audio digest presence**, and whether those records are
  **synthetic/demo** evidence rather than real audio analysis.
- Whether **speech / semantic / real-audio-feature / visual
  interpretation** lanes are present or empty.

## What the script must never do

- Infer a visual story, scene meaning, intent, or emotion.
- Interpret real audio from synthetic digest records.
- Claim to know "what happens in the clip" unless an actual package
  evidence lane supports it.
- Run an LLM, a model, an audio adapter, or ffmpeg.
- Use a transcript to infer clip meaning.
- Mutate the package — no receipt, no track edit, no manifest edit, no
  index rebuild.

It is a diagnostic tool, not a feature generator, and it is strictly
read-only.

## The three honest answers

The script computes three booleans from the package's declared,
non-empty evidence lanes only:

- `can_describe_visual_events` — true only if a visual interpretation
  lane (`scene_events`, `object_proposal_events`,
  `object_tracking_events`, `ocr_events`, `motion_events`, or
  `semantic_events`) is present with at least one record. Keyframes and
  audio presence never count.
- `can_describe_audio_meaning` — true only if a *real* audio feature
  lane (`audio_energy_events`, `audio_transient_events`,
  `audio_texture_events`, `audio_signature_events`, `rhythm_events`,
  `stereo_events`, or `music_events`) is present with at least one
  record. Basic audio presence and synthetic/demo audio digest records
  deliberately do **not** count.
- `can_answer_what_happens` — true only if at least one of the two
  above is true.

For current packages, all three are `false`.

## Exact commands to run it

```sh
python scripts/debug_real_clip_evidence_gap.py PACKAGE
python scripts/debug_real_clip_evidence_gap.py PACKAGE --question "what happens in this clip?"
python scripts/debug_real_clip_evidence_gap.py PACKAGE --json
```

- `PACKAGE` — path to a `.clulatent` package to audit (read-only).
- `--question "..."` — an optional free-text question. The audit
  answers only whether the package holds enough evidence to answer it —
  it never generates a story.
- `--json` — emit the audit as a single JSON object instead of
  human-readable text.

## Expected human output style

```
CLULatent Real Clip Evidence Gap Audit

Package: /tmp/demo/pkg.clulatent
Package id: 9415ad3a-...
Validation: PASS
Duration: 2000 ms
Resolution: 320x240
Keyframes: present (count known: 2)
Audio: present
Speech evidence: empty
Semantic evidence: empty
Audio digest: no audio digest track
Track inventory: keyframes, audio_events, speech_events, semantic_events

Can describe visual events: NO
  Reason: keyframes may exist, but no visual interpretation lane
  (scene, object, OCR, motion, frame caption, or semantic evidence) is
  present, so visual interpretation is unavailable.
Can describe audio meaning: NO
  Reason: only basic audio presence/non-silence evidence exists, or the
  audio digest is synthetic/demo evidence; no real audio feature
  analysis lane is present.
Can answer "what happens in this clip?": NO
  Reason: insufficient evidence in this package to determine what happens.

Recommended next work:
  1. Contact sheet / keyframe preview report surface
  2. Real audio energy/transient adapter
  3. Scene-change lane
  4. OCR lane
  5. Motion/object candidate lane

All findings above are evidence, not truth. ...
```

The `--json` mode emits the same information as an object with
`validation_status`, `can_describe_visual_events`,
`can_describe_audio_meaning`, `can_answer_what_happens`,
`evidence_inventory`, `gaps`, `risks`, and `recommended_next_work`.

## Exit codes

- `0` whenever the audit produces a report — even if the package
  validates as invalid, or its audio digest track is corrupt. Those are
  exactly the gaps this tool is meant to report, so it reports them
  rather than aborting.
- `1` only when the target package cannot be audited at all (a
  missing/non-directory path, or an unreadable `manifest.json`) — always
  with a clean `Error: ...` message on stderr, never a traceback.

## Safety behavior (enforced by tests)

- **Read-only.** The script never writes a track file, a receipt, a
  manifest, or the index — verified byte-for-byte before/after a run.
- **Corrupt evidence is never partially trusted.** A corrupt, malformed-
  JSONL, duplicate-id, path-traversal-`data_path`, or dense-array audio
  digest track is surfaced (via the Phase 3.8 loader's Phase 3.4
  re-validation) as *present but invalid or unreadable*, never as
  trustworthy evidence.
- **Synthetic contamination guard.** Any audio digest records present
  are flagged as synthetic/demo evidence (no real audio adapter exists),
  and the output never claims real audio analysis, tension, intent, or
  understanding.
- **Missing receipt / manifest mismatch** are reported as trust
  warnings, not silently accepted.
- **Bounded output.** The audit prints a compact summary; it never dumps
  raw JSONL payloads or dense arrays.
- **Forbidden language.** The output avoids every forbidden
  certainty/intent phrase (`proves intent`, `manipulates`, `semantic
  truth`, `understands the clip`, `definitely shows/means`, ...) and
  preserves evidence-not-truth language.

## What this phase intentionally does not implement

- No visual AI model, no audio adapter, no OCR/scene/motion/object lane
  — it only *reports their absence*.
- No FFmpeg/librosa/Essentia/Demucs invocation, no ML dependency
  (`pyproject.toml` is unchanged).
- No LLM call, no transcript-based inference of clip meaning.
- No report generation into tracked repo paths, no package mutation, no
  receipt, no index rebuild.
- No network access. No publish, no push, no tag.

## Relation to prior phases

| Phase | Provides | Phase 3.11 usage |
|---|---|---|
| 1 | `ingest_video`, `validate_package`, manifest schema | Unchanged; read to build the inventory and validation status |
| 3.4 | `audio_digest.py` record validators | Unchanged; reached transitively when reading the digest track |
| 3.5 | `audio_digest_writer.py` (track name, receipt path) | Unchanged; used to locate the digest track/receipt |
| 3.8 | `audio_digest_retrieval.load_audio_digest_events` | Unchanged; the read-only, re-validating loader this audit trusts |
| 3.10 | Synthetic audio digest demo records | Structural precedent for the synthetic records the tests audit |

## Files changed

- `scripts/debug_real_clip_evidence_gap.py` (new)
- `tests/test_real_clip_evidence_gap_debugging.py` (new)
- `docs/PHASE_3_11_REAL_CLIP_EVIDENCE_GAP_DEBUGGING.md` (new, this file)
- `README.md` (roadmap bullet)

## Summary

Phase 3.11 adds one small, read-only diagnostic that audits a
`.clulatent` package and reports, honestly, that CLULatent cannot yet
determine what happens in a clip from the package alone — naming every
evidence gap, flagging synthetic/demo audio digest evidence as not-real-
analysis, and recommending the concrete next lanes of work. It adds no
new adapter, model, validation rule, or dependency, mutates nothing, and
makes no claim to understand audio or video. Its entire job is to tell
the truth about the gap.
