# Phase 3.25 — V1 Open Package UX v0

CLULatent now has one obvious front door for a `.clulatent` package.

> MP4 plays media. CLULatent plays media plus evidence.

Before this phase, opening a package meant already knowing about
`playback-viewer`, `agent-read`, and `package-index` as separate
subcommands. `clulatent open PACKAGE` composes the existing V1 read
surfaces into a single action:

```text
clulatent build-video demo.mp4 -o demo.clulatent --profile v1
clulatent open demo.clulatent
```

Then, for deeper inspection:

```text
clulatent agent-read summary demo.clulatent
clulatent agent-read window demo.clulatent --time 17s
clulatent ask demo.clulatent "What evidence exists around 17s?"
```

`open` is the front door to available evidence surfaces, not a semantic
answer engine.

## What `open` does

1. Generates the Phase 3.23 evidence playback viewer (`viewer.html`).
2. Builds the Phase 3.24 budgeted agent read model and renders it to a
   Markdown brief (`agent_read_<budget>.md`).
3. Writes a small, evidence-only `open_summary.md` with the same package
   facts printed to the terminal.
4. Reports whether the package's built-in Phase 3.22 package index
   (`index/v1/index_manifest.json`) and Phase 3.24 agent-read index
   (`index/v1/agent_read_manifest.json`) are present -- **without**
   regenerating either.
5. Unless `--no-browser` is given, tries to open the generated viewer in
   the local system browser via the standard-library `webbrowser` module.
6. Prints a compact status panel: package facts, artifact paths, index
   status, browser outcome, and safe next-step command suggestions (never
   executed automatically).

## Command

```text
clulatent open PACKAGE
clulatent open PACKAGE --output-dir DIR --budget summary --max-events 2000
clulatent open PACKAGE --no-browser
clulatent open PACKAGE --force
```

- `--output-dir DIR` -- external directory to write the open artifacts
  into. Defaults to a package-derived directory under the system temp dir
  (`$TMPDIR/clulatent-open-<package_id>-<hash>/`), deterministic per
  package path so a second `open` finds the same directory. Never inside
  the package.
- `--no-browser` -- still generates every artifact and prints the viewer
  path; just does not attempt to launch a browser.
- `--force` -- overwrite existing open artifacts in `--output-dir`.
  Without it, identical existing content is left alone and reported as
  reused; different existing content is refused with a clear error asking
  for `--force`. `--force` only ever affects these external artifacts --
  it never touches package safety or lock state.
- `--max-events N` (default 1000) -- forwarded to the playback viewer's
  timeline event cap.
- `--budget micro|summary|standard|full` (default `micro`) -- forwarded
  to the agent-read model; picks how much the generated Markdown brief
  and the terminal's inspection candidates cover.

## Output layout

```text
OUTPUT_DIR/
  viewer.html           # Phase 3.23 playback viewer
  agent_read_<budget>.md  # Phase 3.24 budgeted agent-read brief
  open_summary.md        # package + evidence facts, safety caveats
```

None of these are written into the package's own `index/v1/`. Opening is
not processing: no receipt is written for generating them, and no
package-internal artifact is refreshed.

## Playback viewer and missing media

`open` calls the same `v1_playback_viewer.build_playback_viewer` /
`write_playback_viewer` used by `clulatent playback-viewer` directly --
it does not reimplement viewer rendering. When the package's source media
is not present or not playable, the viewer still generates and degrades
honestly to an evidence-only view; `open`'s terminal panel reports
`media: evidence-only (no playable media)` instead of failing.

## Agent-read surface

`open` calls `v1_agent_read_model.build_agent_read_model` and
`render_agent_read_markdown` for the requested `--budget`. The terminal
panel itself stays compact (package facts and artifact paths); the full
budgeted read model lives in the generated `agent_read_<budget>.md`.

## Package index inspection

`open` checks both independent `index/v1/` artifact sets read-only:

- Phase 3.22 package index (`v1_package_index.v1_index_exists`)
- Phase 3.24 agent-read index (`agent_read_manifest.json` presence)

If either is missing, the terminal panel says so honestly and suggests
the explicit regeneration command (`package-index refresh` /
`agent-read write-index`) -- `open` never runs either automatically.

## Browser behavior

By default `open` calls Python's stdlib `webbrowser.open()` on the
generated `viewer.html`. A launch failure (no browser available, headless
environment, `webbrowser` raising) is reported, never fatal: if the
viewer was generated, `open` still exits 0 and prints the exact local
path to open by hand. `--no-browser` skips the attempt entirely.

## Guarantees

- **No package mutation.** `open` never writes into the package: no new
  track, receipt, index, lock, or manifest change. Verified in tests via
  a full package content-hash snapshot taken before and after `open`
  (`sha256` over every file, not just size/mtime), and manually against a
  real built package (see Verification below).
- **No receipts.** Opening is not a processing operation.
- **No index refresh.** Neither `index/v1/` artifact set is regenerated
  by `open`.
- **No lock-state change.**
- **Read-only reuse of existing V1 APIs.** `open` composes
  `v1_playback_viewer`, `v1_agent_read_model`, and `v1_package_index`; it
  does not reimplement package parsing.

## Safety limitations

`open` performs UX composition only. It authors no new evidence, runs no
visual/audio/language model, and adds no semantic interpretation. All
authored prose (the `open_summary.md` caveats) is scanned by the same
forbidden-language guard the playback viewer uses and never scans opaque
package data (filenames, package ids, paths) -- a package whose source
filename happens to contain a flagged phrase is never censored or
rejected.

## Non-goals (unchanged doctrine)

No LLM / OpenAI / Claude / network / local vision model / new ML
dependency; no OCR / object / face / speech / scene detection; no new
evidence lane; no `.clubin`; no macOS Finder/Quick Look/VS Code
integration; no web server, React/Next.js/npm/bundler, external font, or
CDN. `open` does not answer "what happened" -- it opens the surfaces that
answer "what evidence exists."

## Relationship to Phase 3.23 / 3.24

Phase 3.23 made a package *playable* (the viewer). Phase 3.24 made it
*agent-readable at a chosen budget*. Phase 3.25 makes both *discoverable
from one command* without prior knowledge of either subcommand, and adds
package-index awareness and optional browser hand-off on top.

## Known limitations

- The default output directory lives under the system temp dir and is not
  automatically cleaned up by `open`.
- `--output-dir` collision handling compares whole-file content; a
  partially-written or corrupted prior artifact is treated the same as a
  genuinely different one and requires `--force`.
- Phase 3.23's known cosmetic viewer issues (duplicated caveats,
  keyframe/label spacing) are unchanged; this phase did not touch viewer
  rendering.

## Files

- `src/clu_latent/v1_open_package.py` (new) -- `OpenPackageError`,
  `OpenPackagePlan`, `OpenPackageResult`, `build_open_package_plan`,
  `write_open_package_artifacts`, `open_package_surface`,
  `render_open_package_summary`.
- `src/clu_latent/cli.py` -- the top-level `open` command.
- `tests/test_v1_open_package.py` (new).
- `README.md` -- Phase 3.25 section.

## Verification

```bash
python -m pytest tests/test_v1_open_package.py -q
python -m pytest tests/test_v1_agent_read_model.py tests/test_v1_playback_viewer.py \
                 tests/test_v1_package_index.py tests/test_v1_ask_bundle.py \
                 tests/test_v1_open_ask_demo.py tests/test_agent_context_export.py \
                 tests/test_v1_build_pipeline.py -q
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
