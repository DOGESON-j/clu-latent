# Phase 3.10 — Audio Digest Demo Workflow

Base: Phase 3.9 Audio Digest Retrieval CLI is frozen
(tag `phase-3.9-audio-digest-retrieval-cli-freeze`).

No new module. No new adapter. No new CLI command. No new validation
rule, retrieval function, or writer logic. This phase adds a single,
small, reproducible demo script --
`scripts/demo_audio_digest_workflow.py` -- that proves the audio
digest stack (Phase 3.4-3.9) works end-to-end using **synthetic**
evidence, so a user or developer can see the whole path work without
writing Python or guessing commands.

## Core rule (unchanged)

> **Store deep. Show shallow. Retrieve detail only when needed.**

Dense, per-frame audio detail (feature series) is written to disk and
referenced by a package-relative `data_path`, never inlined. Only
bounded, salient, evidence-labelled summaries are ever shown by
default. Full detail is retrieved only when a caller explicitly asks
for it by id, time range, type, link, or salience.

## What the script does

`demo_audio_digest_workflow.py` runs eight steps, each calling an
existing, unmodified library function -- it duplicates no validation,
writer, or retrieval logic:

1. **Create a tiny demo package.** Generates a tiny, deterministic,
   local-only synthetic video via ffmpeg's `lavfi` source (no network,
   no downloaded media), then calls `ingest.ingest_video` (Phase 1) to
   build a `.clulatent` package from it. ffmpeg is used only to
   produce the base video -- it is never used to derive any audio
   digest record; there is still no real audio adapter anywhere in
   this phase.
2. **Write synthetic audio digest records.** Hand-authors one
   `audio_feature_series`, one `audio_digest_segment`, and one
   `audio_llm_context_packet` record as plain Python dicts and writes
   them to a temporary `synthetic_audio_digest_events.jsonl` file. This
   is **synthetic** evidence -- it is not derived from any real audio
   signal, and it is not real audio analysis.
3. **Validate the records.** Calls
   `audio_digest.validate_audio_digest_track` (Phase 3.4) against the
   synthetic records before anything is written to the package.
4. **Append the records.** Calls
   `audio_digest_writer.append_audio_digest_events` (Phase 3.5), which
   re-validates and then commits the batch to
   `tracks/audio_digest_events.jsonl`, updates the manifest track
   descriptor, and writes a receipt.
5. **Validate the package.** Calls `validate.validate_package`
   (Phase 1/3.7), which re-runs the same Phase 3.4 validators at the
   package level.
6. **Retrieve records five ways.** Calls
   `audio_digest_retrieval.get_audio_digest_event_by_id`,
   `query_audio_digest_by_time_range`, `query_audio_digest_by_type`,
   `query_audio_digest_by_linked_evidence_id`, and
   `query_audio_digest_by_salience` (all Phase 3.8) and prints
   PASS/FAIL for each.
7. **Retrieve bounded LLM context.** Calls
   `select_audio_digest_llm_context` (Phase 3.8) and explicitly checks
   that no `audio_feature_series` record (no raw dense data) is ever
   selected into that bounded context.
8. **Confirm receipts and manifest.** Confirms the manifest's audio
   digest track descriptor and `receipts/audio_digest.jsonl` both
   exist, then calls `load_audio_digest_events` +
   `summarize_audio_digest_retrieval_result` (Phase 3.8) and prints the
   resulting counts.

## Exact command to run it

```sh
python scripts/demo_audio_digest_workflow.py
```

With no arguments, it creates `$TMPDIR/clulatent_audio_digest_demo/`
and runs the full workflow inside it. Optional flag:

```sh
python scripts/demo_audio_digest_workflow.py --output-dir /tmp/my_demo
```

- `--output-dir DIR` — where to put everything (default: a
  `clulatent_audio_digest_demo` directory under the system temp
  directory). Created if missing.

## Expected high-level output

```
Step 1/8: preparing demo media and package in /tmp/clulatent_audio_digest_demo
  media: /tmp/clulatent_audio_digest_demo/demo_media.mp4
  package: /tmp/clulatent_audio_digest_demo/demo.clulatent
Step 2/8: writing synthetic audio digest records (evidence, not truth)
  events file: /tmp/clulatent_audio_digest_demo/synthetic_audio_digest_events.jsonl
  record count: 3
Step 3/8: validating the synthetic records
  PASS -- records are valid
Step 4/8: appending records to the package
        Records written
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ ID                               ┃ Type                  ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ demo_series_loudness_rms         │ audio_feature_series  │
│ demo_segment_buildup_candidate   │ audio_digest_segment  │
│ demo_packet_context              │ audio_llm_context_pkt │
└───────────────────────────────────┴───────────────────────┘
  events written: 3
  receipt: /tmp/clulatent_audio_digest_demo/demo.clulatent/receipts/audio_digest.jsonl
Step 5/8: validating the package
  PASS -- package is valid
Step 6/8: retrieving records five ways
  get by id: PASS
  query-time: PASS (3 event(s))
  query-type: PASS (1 event(s))
  query-linked: PASS (1 event(s))
  query-salience: PASS (2 event(s))
Step 7/8: retrieving bounded LLM context
  PASS -- 1 record(s) selected, no raw audio_feature_series record included
Step 8/8: confirming receipts and manifest
  manifest track record_count: 3
  receipt present: True
  retrieval-summary event_count=3

Summary
  package: /tmp/clulatent_audio_digest_demo/demo.clulatent
  validate records: PASS
  append: PASS (3 event(s) written)
  validate package: PASS
  retrieval: PASS
  llm-context: PASS
Reminder: this is entirely synthetic evidence, not confirmed truth, and
not derived from any real audio signal -- CLULatent does not claim to
understand audio.
```

Exact counts are fixed (the synthetic record set is deterministic).
The script exits `0` on success and `1` on any real failure (missing
ffmpeg, an unwritable output directory, a failed validate/append/
retrieval step) -- always with a clean `Error: ...` message on stderr,
never a raw traceback.

## What files it creates

All inside `--output-dir` (nothing outside it is ever created,
modified, or deleted):

- `demo_media.mp4` — the tiny synthetic video.
- `demo.clulatent/` — the demo package (`ingest_video`'s normal
  output: `manifest.json`, `sources/`, `tracks/`, `index/`,
  `receipts/`).
- `synthetic_audio_digest_events.jsonl` — the synthetic records, before
  append.
- After step 4: `demo.clulatent/tracks/audio_digest_events.jsonl` and
  an appended line in `demo.clulatent/receipts/audio_digest.jsonl` --
  both written only by the pre-existing Phase 3.5 writer.

## Why this is not real audio analysis

No ffmpeg audio filter, `librosa`, `essentia`, `aubio`, `basic_pitch`,
`demucs`, `yamnet`, `panns`, or `openl3` call is made anywhere in this
script, and no ML dependency is added to `pyproject.toml`. ffmpeg is
invoked exactly once, only to synthesize a tiny base video (the same
pattern Phase 2.18 already established) -- never to derive any audio
digest record. Every audio digest record the script writes is a
hand-authored Python dict with conservative, evidence-labelled
language ("tension-like buildup candidate", "linked evidence
suggests"), not the output of any signal-processing or ML step. This
phase does not make CLULatent understand audio.

## Evidence, not truth

`audio_digest_segment.payload.label`/`summary` and
`audio_llm_context_packet.payload.summary`/`caveats` in the synthetic
records use the same hedged, evidence-not-truth language already
required by Phase 3.4 validation (`_check_evidence_not_truth_caveat`)
and already used throughout the `audio_digest` test fixtures. The
script fabricates no new disclaimer text of its own -- it only reuses
the caveat language the validator already requires before such a
record can ever be written.

## Safety properties (enforced by tests)

- Every file the script creates lives inside the chosen
  `--output-dir` -- no repo file or committed fixture is modified, and
  no unrelated package or index is touched.
- The script never generates a report, never touches
  `index/search.sqlite` outside its own package, and never requires
  `ffmpeg`/`librosa`/`essentia`/`demucs` audio analysis or any ML
  extra (`silero-vad`, `faster-whisper`) to be installed.
- Console output carries no unsafe terminal control sequences and
  avoids every forbidden phrase/certainty marker Phase 3.4 validation
  itself forbids (e.g. no "proves intent", "manipulation", "definitely
  exact instrument").
- An unwritable/invalid `--output-dir`, missing ffmpeg, or a failed
  validate/append/retrieval step all fail cleanly with exit code `1`
  and a clean `Error: ...` message on stderr -- never a traceback.

## Non-goals (explicit)

- No real audio adapter, no FFmpeg/librosa/Essentia/Demucs/aubio/
  Basic Pitch/Demucs/YAMNet/PANNs/OpenL3 *audio analysis* invocation
  of any kind (ffmpeg is still used only to generate the base demo
  video, exactly as Phase 2.18 already did).
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated summaries or new retrieval/validation/writer
  logic -- every step is a direct pass-through to an existing Phase
  3.4-3.9 function.
- No claim that CLULatent understands audio.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.

## Relationship to prior phases

| Phase | Provides | Phase 3.10 usage |
|---|---|---|
| 1 | `ingest_video`, `validate_package` | Unchanged; steps 1 and 5 |
| 3.4 | `audio_digest.py` record validators | Unchanged; steps 3, 4 (transitively), 5 |
| 3.5 | `audio_digest_writer.append_audio_digest_events` | Unchanged; step 4 |
| 3.6 | `clulatent audio-digest ...` CLI group | Not called directly; script calls the same library functions the CLI wraps |
| 3.7 | Package-level audio digest validation | Unchanged; step 5 |
| 3.8 | `audio_digest_retrieval.py` primitives | Unchanged; steps 6, 7, 8 |
| 3.9 | Retrieval CLI commands | Not called directly; script calls the same Phase 3.8 functions those commands wrap |
| 2.18 | Real adapter demo script structural precedent | Followed directly for script/doc/test structure and the ffmpeg-synthetic-media pattern |

## Files changed

- `scripts/demo_audio_digest_workflow.py` (new)
- `tests/test_audio_digest_demo_workflow.py` (new)
- `docs/PHASE_3_10_AUDIO_DIGEST_DEMO_WORKFLOW.md` (new, this file)
- `README.md` (roadmap bullet)

## Summary

Phase 3.10 adds one small, reproducible, non-destructive script that
runs the existing audio digest stack end-to-end -- validate, append,
validate package, retrieve five ways, retrieve bounded LLM context,
confirm receipts and manifest -- using entirely synthetic evidence. It
adds no new adapter, module, CLI command, validation rule, retrieval
function, or ML dependency. It does not analyze real audio, does not
generate dense metrics from media, and does not make CLULatent
understand audio; it only makes the existing, honest
evidence-through-canonicalization path for audio digest evidence
easier to see run, from a single command.
