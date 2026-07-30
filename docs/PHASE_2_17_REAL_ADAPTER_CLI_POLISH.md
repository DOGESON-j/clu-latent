# Phase 2.17 — Real Adapter CLI Polish

Base: Phase 2.16 Real Adapter Demo Package is frozen
(tag `phase-2.16-real-adapter-demo-package-freeze`).
No new module. No new adapter. No new CLI command. This phase polishes
the existing `clulatent analysis generate-ffmpeg-visual-change-adapter-
result` command (Phase 2.15) — clearer help text, a clearer
`--output`-mode success summary, and one small error-handling fix —
plus documentation and tests proving the polished workflow.

## Scope

This phase makes the first real adapter's CLI path easier and safer to
use, without changing what it validates, writes, or claims. It adds no
new adapter architecture, no new real adapter, no ML model dependency,
no semantic truth generation, no dynamic plugin loading, no adapter
discovery, no Studio UI, no CLUBIN, and no broad refactor — every
change in this phase is scoped to the single Phase 2.15 command and
its docs/tests.

## Core principle (unchanged)

Adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

## What changed from Phase 2.15/2.16

1. **`--output`-mode success summary is richer.** Previously it
   printed only a one-line confirmation plus a raw event count:

   ```
   Visual-change adapter result written -> visual_change.json
     visual_change_events candidates: 3
   ```

   It now prints the adapter name, a `Lanes generated` table (lane
   name + event count, ready to grow if a future adapter ever declares
   more than one lane), the total event count, and an explicit
   evidence-not-truth reminder pointing at the next two commands:

   ```
   Visual-change adapter result written -> visual_change.json
     adapter_name: clulatent-ffmpeg-visual-change-adapter
            Lanes generated
   ┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
   ┃ Lane                  ┃ Events ┃
   ┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
   │ visual_change_events  │      3 │
   └───────────────────────┴────────┘
     total events: 3
   Reminder: this is candidate evidence, not confirmed truth -- run
   `analysis dry-run-adapter`, then `analysis import-adapter-result`,
   to make it canonical.
   ```

   This mirrors the `Lanes written` table `analysis import-adapter-
   result` already prints (Phase 2.12), so the two commands now read
   consistently.

2. **Stdout (no `--output`) mode is deliberately untouched.** With no
   `--output`, the command still prints *only* the JSON document and
   nothing else — no summary, no reminder, no extra line — exactly as
   Phase 2.15 built it. This is intentional, not an oversight: stdout
   in this mode must stay safe to pipe directly into `analysis
   dry-run-adapter` or `jq`, and any extra text would corrupt that
   JSON stream. The richer summary above only ever appears in
   `--output` file mode, where stdout is not carrying the JSON.

3. **Fixed an unhandled-exception path for a bad `--output`.** If
   `--output` named an *existing directory* (not a file) and `--force`
   was given, the command used to let a raw `IsADirectoryError`
   traceback escape to the user. It now checks for this case explicitly
   and fails cleanly:

   ```
   Error: output path is a directory, not a file: /path/to/some/existing/dir
   ```

   exiting `1`, same as every other `--output` failure mode (already-
   exists-without-`--force`, parent directory missing).

4. **Docstring now names the full four-step workflow** (generate ->
   `analysis dry-run-adapter` -> `analysis import-adapter-result` ->
   `validate`) and restates the evidence-not-truth reminder directly
   in `--help` output, so `clulatent analysis
   generate-ffmpeg-visual-change-adapter-result --help` alone explains
   the whole path, not just this one step.

5. **No command rename, no alias.** The existing name
   `generate-ffmpeg-visual-change-adapter-result` already matches this
   codebase's sibling command naming convention
   (`generate-fixture-adapter-result`); adding an alias would only
   create two names for the same thing without making either clearer,
   so none was added.

## What did not change

- `analysis dry-run-adapter` and `analysis import-adapter-result`
  (Phase 2.11/2.12) — unmodified, still the only two commands that can
  make a generated result canonical.
- `build_visual_change_adapter_result` / `visual_change_adapter_result_to_dict`
  (Phase 2.15) — unmodified. This phase touches only the CLI layer
  (`cli.py`), never `analysis_ffmpeg_visual_change_adapter.py`.
- Validation, writer, receipt, and lock logic (Phase 2.6/2.7/2.9/2.10)
  — unmodified.
- The generated JSON's shape, field names, event language, and
  confidence derivation — byte-for-byte unchanged; only the
  human-readable `--output`-mode console summary changed.

## The polished workflow

Each command below is copy-pasteable. `MEDIA` is a real media file;
`PKG` is a real `.clulatent` package folder.

### 1. Generate

```sh
clulatent analysis generate-ffmpeg-visual-change-adapter-result my_clip.mp4 -o visual_change.json
```

Expected output (counts vary with the source media and `--threshold`):

```
Visual-change adapter result written -> visual_change.json
  adapter_name: clulatent-ffmpeg-visual-change-adapter
         Lanes generated
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ Lane                 ┃ Events ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ visual_change_events │      N │
└──────────────────────┴────────┘
  total events: N
Reminder: this is candidate evidence, not confirmed truth -- run
`analysis dry-run-adapter`, then `analysis import-adapter-result`, to
make it canonical.
```

Omit `-o` to print pure JSON to stdout instead (unchanged from Phase
2.15 — safe to pipe straight into the next command).

### 2. Dry-run

```sh
clulatent analysis dry-run-adapter visual_change.json --package "$PKG"
```

Unchanged (Phase 2.11) — writes nothing.

### 3. Import

```sh
clulatent analysis import-adapter-result "$PKG" visual_change.json
```

Unchanged (Phase 2.12) — writes `tracks/visual_change_events.jsonl`,
one `manifest.json` `TrackDescriptor`, and one `receipts/analyze.jsonl`
line.

### 4. Validate

```sh
clulatent validate "$PKG"
```

Unchanged (Phase 1/2.9).

## Expected outputs at a high level

- Step 1 (`--output` mode): a written JSON file plus a console summary
  naming the adapter, every lane and its event count, the total event
  count, and the evidence reminder.
- Step 1 (stdout mode): pure JSON on stdout, nothing else.
- Step 2: a dry-run report (lanes/counts/warnings/package status),
  nothing written.
- Step 3: a `Lanes written` table, total events written, and the
  receipt path.
- Step 4: `PASS — package is valid`.

## Evidence, not truth

Every event this command produces remains a "visual-change candidate"
or "ffmpeg scene-score candidate" — never a confirmed cut or semantic
scene boundary. The new `--output`-mode summary makes this explicit at
generation time, in addition to the hedged language already carried
inside the JSON's `payload.evidence_label`/`payload.description` and
`metadata.warnings` fields (Phase 2.15, unchanged). Generating a result
is not importing it, and importing it makes it canonical — validated,
bounded, receipted, reviewable, lockable — never confirmed true.

## How review_events still apply

Unchanged (Phase 2.1/2.14/2.16): once imported, each
`visual_change_events` record is ordinary canonical evidence. A
`review_approval`, `review_rejection`, or `review_correction` event can
target it by id exactly as it can target any other canonical event,
regardless of which command produced it or how its CLI output was
formatted.

## Why this is not ML

No model, weights, or learned inference is introduced or touched by
this phase. The only thing that changed is what text a human sees in
their terminal after running an unmodified ffmpeg `scdet` call.

## Why this is not adapter discovery / plugin runtime

This phase adds no registry, no dynamic import, no plugin lookup, and
no new adapter. It polishes the CLI wrapper around one already-existing,
statically-imported adapter function
(`build_visual_change_adapter_result`), called exactly the same way it
was called before this phase.

## Why this is not Studio UI

This phase adds terminal text (Rich console output) and tests — no
graphical interface, no persistent server, no interactive review
surface. Every command remains a single, stateless CLI invocation.

## Why this is not CLUBIN

No binary, packaging format, or distribution artifact is introduced.
The generated adapter-result JSON's shape is completely unchanged by
this phase.

## Safety properties (enforced by tests)

- Generation still mutates nothing in any package — no track, no
  receipt, no lock, regardless of `--output` mode.
- Generation still touches no `index/search.sqlite` (there is no
  package involved in generation at all).
- Stdout (no `--output`) mode still emits *only* valid, parseable JSON
  — no extra text, no unsafe terminal control sequences.
- `--output` mode's new summary text also carries no unsafe terminal
  control sequences (checked the same way import/dry-run output
  already is, via `safe_console_text` for any package/user-path-
  derived string).
- An `--output` path that already exists (without `--force`), whose
  parent directory does not exist, or that is itself a directory all
  fail cleanly with exit code `1` and no traceback.
- `--threshold`/`--max-events` out of range, missing media, and
  missing ffmpeg all still fail cleanly (Phase 2.15 behaviour,
  unchanged and re-tested here).
- Unsupported/malformed CLI options (e.g. a non-numeric `--threshold`)
  are rejected by the underlying CLI framework's own usage-error
  handling, not a Python traceback.

## Non-goals (explicit)

- No new CLI command, module, adapter, validation rule, writer, or
  lock logic.
- No change to `analysis_ffmpeg_visual_change_adapter.py`'s generation
  logic, event shape, or confidence derivation.
- No change to `analysis dry-run-adapter` or `analysis
  import-adapter-result`'s behaviour.
- No command rename or alias.
- No Studio UI or CLUBIN.

## Relationship to prior phases

| Phase | Provides | Phase 2.17 usage |
|---|---|---|
| 2.10 | `analysis_adapters.py` contracts | Unchanged; still reached indirectly, never duplicated |
| 2.11 | `analysis_adapter_dry_run.py` | Unchanged; step 2 of the polished workflow |
| 2.12 | `import-adapter-result` CLI command | Unchanged; step 3 of the polished workflow; its `Lanes written` table is the style this phase's new summary now matches |
| 2.13 | Fixture adapter + its `generate-fixture-adapter-result` sibling command | Naming-convention precedent this phase chose not to diverge from (no alias added) |
| 2.15 | `analysis_ffmpeg_visual_change_adapter.py` + its CLI command | The command this phase polishes; generation logic itself is unmodified |
| 2.16 | Real adapter demo doc/tests | Structural precedent this doc and test file mirror |

## Files changed

- `src/clu_latent/cli.py` (polished `generate-ffmpeg-visual-change-
  adapter-result`: docstring, `--output`-mode summary, directory-path
  error handling; no other command touched)
- `tests/test_real_adapter_cli_polish.py` (new)
- `docs/PHASE_2_17_REAL_ADAPTER_CLI_POLISH.md` (new, this file)
- `README.md` (roadmap bullet)

## Summary

Phase 2.17 makes the first real adapter's command line easier to read
and safer to use without touching what it detects, validates, or
writes: a clearer `--output`-mode summary (adapter name, lanes, event
counts, evidence reminder), a docstring that names the whole four-step
workflow, and a fix for one raw-traceback edge case
(`--output` naming an existing directory). Stdout JSON mode, the
adapter's detection logic, and every downstream command (`analysis
dry-run-adapter`, `analysis import-adapter-result`, `validate`) are
unchanged. It adds no new intelligence and does not make CLULatent
understand media — it only makes the existing, honest evidence easier
to generate correctly from the command line.
