# Phase 2.18 — Real Adapter Demo Script

Base: Phase 2.17 Real Adapter CLI Polish is frozen
(tag `phase-2.17-real-adapter-cli-polish-freeze`).

No new module. No new adapter. No new CLI command. No new adapter
architecture. This phase adds a single, small, reproducible demo
script -- `scripts/demo_real_adapter_workflow.py` -- that runs the
polished first-real-adapter workflow (Phase 2.10-2.17) end-to-end so a
user or developer can see the whole path work without guessing
commands.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

## What the script does

`demo_real_adapter_workflow.py` runs five steps, each calling an
existing, unmodified library function -- it duplicates no adapter,
import, dry-run, or validation logic:

1. **Create a tiny demo package.** Generates a tiny, deterministic,
   local-only synthetic video (a one-second red clip concatenated with
   a one-second blue clip, via ffmpeg's `lavfi` source -- no network,
   no embedded/downloaded media file) unless `--media` points at a
   real file, then calls `ingest.ingest_video` (Phase 1) to build a
   `.clulatent` package from it.
2. **Generate a real adapter result.** Calls
   `analysis_ffmpeg_visual_change_adapter.build_visual_change_adapter_result`
   (Phase 2.15) against the media, and writes the resulting JSON
   document to a file in the output directory.
3. **Dry-run it.** Calls `analysis_adapter_dry_run.dry_run_adapter_result`
   (Phase 2.11) against the generated result and the package --
   writes nothing.
4. **Import it.** Calls `analysis_adapters.write_adapter_result`
   (Phase 2.10/2.12's writer bridge) to commit the result into the
   package's `tracks/visual_change_events.jsonl`, its `manifest.json`
   `TrackDescriptor`, and `receipts/analyze.jsonl`.
5. **Validate the package.** Calls `validate.validate_package`
   (Phase 1/2.9).
6. **Print a safe summary.** Adapter name, per-lane and total event
   counts, the analyze receipt path, PASS/FAIL for each step, and an
   explicit evidence-not-truth reminder -- via the same
   `security.console.safe_console_text` helper `cli.py` already uses
   for every dynamic/path-derived value.

## Exact command to run it

```sh
python scripts/demo_real_adapter_workflow.py
```

With no arguments, it creates `$TMPDIR/clulatent_demo/` (the system
temp directory) and runs the full workflow inside it, using a tiny
synthetic ffmpeg-generated video. Optional flags:

```sh
python scripts/demo_real_adapter_workflow.py \
    --output-dir /tmp/my_demo \
    --media my_clip.mp4 \
    --threshold 5 \
    --print-json
```

- `--output-dir DIR` — where to put everything (default: a
  `clulatent_demo` directory under the system temp directory).
  Created if missing; reused if it already exists and is empty/does
  not yet contain a demo package.
- `--media PATH` — use a real media file instead of the generated
  synthetic one.
- `--threshold N` — ffmpeg `scdet` threshold, 0-100 (default: 10,
  ffmpeg's own default).
- `--print-json` — also print the full generated adapter-result JSON
  document at the end (off by default, so normal runs stay short).

## Expected high-level output

```
Step 1/5: preparing demo media and package in /tmp/clulatent_demo
  media: /tmp/clulatent_demo/demo_media.mp4
  package: /tmp/clulatent_demo/demo.clulatent
Step 2/5: generating real ffmpeg visual-change adapter result
  adapter_name: clulatent-ffmpeg-visual-change-adapter
  adapter result: /tmp/clulatent_demo/visual_change_result.json
Step 3/5: dry-running the adapter result (writes nothing)
  PASS -- dry run reports this result is eligible to import
Step 4/5: importing the adapter result into the package
          Lanes written
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Lane                 ┃ Events ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ visual_change_events │      1 │
└──────────────────────┴────────┘
  total events written: 1
  analyze receipt: /tmp/clulatent_demo/demo.clulatent/receipts/analyze.jsonl
Step 5/5: validating the package
  PASS -- package is valid

Summary
  package: /tmp/clulatent_demo/demo.clulatent
  adapter result: /tmp/clulatent_demo/visual_change_result.json
  dry-run: PASS
  import: PASS (1 event(s) written)
  validate: PASS
Reminder: this is candidate evidence, not confirmed truth -- importing
made it canonical (validated, bounded, receipted, reviewable,
lockable), never confirmed true.
```

Exact counts vary with the source media and `--threshold`. The script
exits `0` on success and `1` on any real failure (missing ffmpeg, a
media file that does not exist, an unwritable output directory, a
failed dry-run/import/validate step) -- always with a clean `Error:
...` message on stderr, never a raw traceback.

## What files it creates

All inside `--output-dir` (nothing outside it is ever created,
modified, or deleted):

- `demo_media.mp4` — the tiny synthetic video (skipped if `--media`
  was given).
- `demo.clulatent/` — the demo package (`ingest_video`'s normal
  output: `manifest.json`, `sources/`, `tracks/`, `index/`,
  `receipts/`).
- `visual_change_result.json` — the generated Phase 2.10 `AdapterResult`
  JSON document, before import.
- After step 4: `demo.clulatent/tracks/visual_change_events.jsonl` and
  an appended line in `demo.clulatent/receipts/analyze.jsonl` -- both
  written only by the pre-existing Phase 2.7 writer, reached through
  the unmodified Phase 2.10/2.12 writer bridge.

Re-running the script against the same `--output-dir` fails cleanly
(the package already exists) rather than silently overwriting it --
the script has no `--force`/overwrite flag by design, so it is never
destructive by default.

## How this relates to Phase 2.16 / 2.17

- Phase 2.16 proved the same generate -> dry-run -> import -> validate
  path works end-to-end via a demo **doc** and **tests** driving the
  CLI directly (`clulatent analysis ...` commands through `CliRunner`).
- Phase 2.17 polished that same CLI command's help text, `--output`
  success summary, and one error-handling edge case.
- Phase 2.18 adds a **script**, not a doc or a CLI command: a single
  file a user can run directly (`python scripts/demo_real_adapter_
  workflow.py`) that shows the same workflow without needing to first
  build/locate a package or media file themselves. It calls the same
  Phase 2.10-2.12 library functions Phase 2.16's tests already call
  directly (not the Phase 2.17 CLI wrapper) -- avoiding any dependency
  on `typer`/`CliRunner` inside a standalone script.

## Evidence, not truth

Every event the script's generated adapter result contains remains a
"visual-change candidate" / "ffmpeg scene-score candidate" -- never a
confirmed cut or semantic scene boundary (Phase 2.15's language,
unchanged). Importing it makes it canonical -- validated, bounded,
receipted, reviewable, lockable -- never confirmed true. The script's
own summary states this explicitly as its final line, and
`--print-json`'s output still carries the same hedged
`payload.evidence_label`/`payload.description` and
`metadata.warnings` fields the underlying adapter always produces.

## Why this is not ML

No model, weights, or learned inference is introduced or touched by
this phase. The script's only "detection" step is the same,
unmodified ffmpeg `scdet` call Phase 2.15 already made; the only new
code is orchestration (calling five existing functions in order) and
console output.

## Why this is not adapter discovery / plugin runtime

The script imports exactly one adapter function
(`build_visual_change_adapter_result`) by name, statically, at the top
of the file -- no registry, no dynamic import, no plugin lookup, and
no new adapter is added. It is a fixed, single-purpose demonstration
of one already-existing adapter's full path, not a general adapter
runner.

## Why this is not Studio UI

The script is a single, stateless, one-shot terminal invocation that
prints text (via the same Rich console/`safe_console_text` pattern
`cli.py` uses) and exits -- no graphical interface, no persistent
server, no interactive review surface.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
The demo package the script produces is an ordinary `.clulatent`
folder, identical in shape to any other package this codebase
produces; the generated adapter-result JSON's shape is unchanged by
this phase.

## Safety properties (enforced by tests)

- Every file the script creates lives inside the chosen
  `--output-dir` -- nothing is ever written outside it, no repo file
  or committed fixture is modified, and no unrelated package is
  touched.
- The synthetic demo media is generated entirely locally via the same
  hardened `security.subprocess.resolve_tool`/`run_tool` pattern every
  other ffmpeg call in this codebase already uses (no `shell=True`,
  bounded capture, hard timeout) -- no network access, no large media.
- The script's console output carries no unsafe terminal control
  sequences (checked the same way `cli.py`'s output already is, via
  `safe_console_text` for any dynamic/path-derived value) and never
  dumps the full generated JSON unless `--print-json` is explicitly
  given.
- An `--output-dir` that is a file (not a directory), an unwritable/
  invalid output path, or a re-run against an `--output-dir` that
  already contains a demo package all fail cleanly with exit code `1`
  and a clean `Error: ...` message on stderr -- never a traceback.
- A missing `--media` file fails the same way.

## Non-goals (explicit)

- No new CLI command, module, adapter, validation rule, writer, or
  lock logic.
- No change to `analysis_ffmpeg_visual_change_adapter.py`,
  `analysis_adapter_dry_run.py`, `analysis_adapters.py`, `ingest.py`,
  or `validate.py` -- every one of the script's calls uses these
  modules exactly as they already existed.
- No `--force`/overwrite flag; the script is non-destructive by
  default.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.18 usage |
|---|---|---|
| 1 | `ingest_video`, `validate_package` | Unchanged; steps 1 and 5 |
| 2.7 | `append_analysis_events` (writer) | Unchanged; reached transitively through step 4 |
| 2.10 | `analysis_adapters.py` contracts, `write_adapter_result` | Unchanged; step 4 |
| 2.11 | `analysis_adapter_dry_run.py` | Unchanged; step 3 |
| 2.12 | `import-adapter-result` CLI command | Not called directly (script calls the library function it wraps); precedent for the `Lanes written` table style this script's summary matches |
| 2.15 | `analysis_ffmpeg_visual_change_adapter.py` | Unchanged; step 2 |
| 2.16 | Real adapter demo doc/tests | Structural precedent; this script demonstrates the same path as a runnable file instead of a doc |
| 2.17 | Polished CLI path | Precedent for the console-output style (adapter name, lanes table, evidence reminder) this script's summary mirrors |

## Files changed

- `scripts/demo_real_adapter_workflow.py` (new)
- `tests/test_real_adapter_demo_script.py` (new)
- `docs/PHASE_2_18_REAL_ADAPTER_DEMO_SCRIPT.md` (new, this file)
- `README.md` (roadmap bullet)

## Summary

Phase 2.18 adds one small, reproducible, non-destructive script that
runs the existing real-adapter workflow end-to-end and prints a safe,
human-readable summary -- without adding any new adapter, module, CLI
command, validation rule, or intelligence. It does not make CLULatent
understand media any better than Phase 2.15 already did; it only makes
the existing, honest evidence-generation-through-canonicalization path
easier to see run, from a single command, entirely inside a directory
the caller chooses.
