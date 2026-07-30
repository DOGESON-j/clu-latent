# Phase 3.9 — Audio Digest Retrieval CLI

Status: **read-only CLI front door on the Phase 3.8 audio digest
retrieval primitives only — still no audio adapter, model, or runtime
wiring**. Base: Phase 3.8 Audio Digest Retrieval Primitives is frozen
(tag `phase-3.8-audio-digest-retrieval-primitives-freeze`). Builds
directly on `src/clu_latent/audio_digest_retrieval.py` (Phase 3.8),
which itself builds on `src/clu_latent/audio_digest.py` (Phase 3.4),
`src/clu_latent/audio_digest_writer.py` (Phase 3.5), the existing
`clulatent audio-digest ...` CLI group (Phase 3.6), and the
package-level validation wired into `validate.py` (Phase 3.7).

## Core rule

> **Store deep. Show shallow. Retrieve detail only when needed.**

Phase 3.8 made already-validated audio digest evidence retrievable by
id, time range, record type, linked evidence id, or
salience/recommended-for-context flag, as plain Python functions.
Phase 3.9 is the first place a human (or a script) can reach that same
evidence from the command line, without writing Python — by extending
the existing `clulatent audio-digest ...` Typer group with seven new,
strictly read-only subcommands.

## What this phase implements

Seven new commands added to the existing `audio_digest_app` group in
`src/clu_latent/cli.py`:

```
clulatent audio-digest get <package> <event-id>
clulatent audio-digest query-time <package> --start-ms <int> --end-ms <int>
clulatent audio-digest query-type <package> <record-type>
clulatent audio-digest query-linked <package> <evidence-id>
clulatent audio-digest query-salience <package> --min-salience <float>
clulatent audio-digest llm-context <package> [--start-ms <int>] [--end-ms <int>] [--max-events <int>]
clulatent audio-digest retrieval-summary <package>
```

Each command is a thin wrapper around exactly one
`audio_digest_retrieval.py` (Phase 3.8) function:

- `get` → `get_audio_digest_event_by_id`
- `query-time` → `query_audio_digest_by_time_range`
- `query-type` → `query_audio_digest_by_type` (after a CLI-level check
  that the requested type is one of `audio_digest.AUDIO_DIGEST_RECORD_TYPES`)
- `query-linked` → `query_audio_digest_by_linked_evidence_id`
- `query-salience` → `query_audio_digest_by_salience` (after a
  CLI-level check that `--min-salience` is within `0.0..1.0`)
- `llm-context` → `select_audio_digest_llm_context`
- `retrieval-summary` → `load_audio_digest_events` +
  `summarize_audio_digest_retrieval_result`

No new retrieval logic is added anywhere in this phase — every command
calls straight through to the Phase 3.8 primitive and formats its
result for a terminal.

## Records are only ever printed already validated

Every command reaches the audio digest track exclusively through
`audio_digest_retrieval.py`, which re-validates the whole track with
`audio_digest.validate_audio_digest_track` before handing back
anything (Phase 3.8's guarantee, unchanged here). A CLI command can
therefore never print a record with an unsupported type, a raw dense
array, forbidden truth/intent/manipulation language, or an unsafe
`data_path` — even if such a record was hand-edited onto disk outside
the Phase 3.5 writer. An invalid on-disk track makes every command
that touches it fail cleanly (`AudioDigestRetrievalError`, exit code
1) rather than print partial or unsafe data.

## Evidence-not-truth caveats are preserved, not fabricated

`llm-context` and `get` print each record's own `payload` verbatim
(through `safe_console_text`). Phase 3.4 validation already requires
`audio_llm_context_packet` records to carry evidence-not-truth
language in `payload.caveats` before they can ever be written or
retrieved — so printing the record's own payload is sufficient to
preserve that caveat. This phase does not add, rewrite, or infer any
new disclaimer text of its own.

## Safety behavior

- Read-only: no command writes a track file, a manifest, a receipt, or
  touches `index/search.sqlite`.
- Every genuine failure (missing package, missing/oversized/malformed
  track file, a track that fails Phase 3.4 validation) exits 1 via the
  existing `_fail()` helper, with the message routed through
  `safe_console_text` (ANSI/control-character stripping + Rich markup
  escaping) exactly like every other CLI command in this project.
- An empty-but-valid result — no audio digest track declared, or a
  query that matches nothing — is never treated as an error: it prints
  a plain `[yellow]...[/yellow]` message and exits 0, matching the
  existing convention already used elsewhere in `cli.py` (e.g. the
  `query`/list-events commands).
- `llm-context` never prints `audio_feature_series` records or any raw
  dense array; it is always bounded by `--max-events`.
- `query-type` rejects an unsupported record type, and `query-salience`
  rejects a `--min-salience` outside `0.0..1.0`, before ever touching
  the package — deliberate CLI-layer checks, since the Phase 3.8
  primitives themselves simply return no matches for those inputs
  rather than raising.

## What this phase intentionally does not implement

- No audio adapter. No FFmpeg/ffprobe, librosa, Essentia, aubio, Basic
  Pitch, Demucs, YAMNet, PANNs, or OpenL3 invocation of any kind.
- No dense audio extraction, no stem separation, no ML dependency
  added anywhere (`pyproject.toml` is unchanged).
- No auto-generated summaries or new retrieval logic — every command
  is a direct pass-through to an existing Phase 3.8 function.
- No claim that CLULatent understands audio.
- No Studio UI, no CLUBIN.
- No publish, no push, no tag.
- No package mutation, no receipt, no index rebuild.

## Relation to Phase 3.4, Phase 3.5, Phase 3.6, Phase 3.7, and Phase 3.8

Phase 3.4 defined the non-raising `(errors, warnings)` record
validators. Phase 3.5 defined the lock-aware writer that calls those
validators before committing a batch. Phase 3.6 put a CLI front door
on both (`types`, `validate-file`, `append`, `append-file`). Phase 3.7
made `validate_package` re-run that same validation at the package
level. Phase 3.8 made already-validated audio digest evidence
retrievable by id, time range, type, linked evidence id, and salience,
as plain Python functions, reusing that same Phase 3.4 validation as
its safety gate. Phase 3.9 exposes those exact Phase 3.8 functions
through the CLI — no new validation rule, no new retrieval rule, is
added anywhere in this phase.

## Run

```
python -m pytest
```

Expected: all tests pass, including the new
`tests/test_cli_audio_digest_retrieval.py` suite, with no regression
in existing report/demo/adapter/validation/locking/review/analysis-lane/
audio-digest/audio-digest-writer/audio-digest-CLI/validate-audio-digest/
audio-digest-retrieval tests.
