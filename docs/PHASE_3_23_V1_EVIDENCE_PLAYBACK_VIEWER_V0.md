# Phase 3.23 — V1 Evidence Playback Viewer v0

## What this phase is for

- Phase 3.19 made a `.clulatent` package **buildable** and **exportable**
  into one agent-context document.
- Phase 3.20 made it **explainable**, Phase 3.21 made it **askable**, and
  Phase 3.22 made it **self-describing** (a built-in `index/v1/`).
- Phase 3.23 makes it **playable**:

> "I built a `.clulatent` package from a video. Now I want to open a local
> viewer and play the video while seeing the evidence tracks: keyframes,
> visual-change candidates, changed-region candidates, evidence bundles,
> agent reviews, validation status, caveats, and the V1 index links."

The guiding line:

> **MP4 plays media. CLULatent plays media plus evidence.**

`clulatent playback-viewer` renders a single, static, local-first HTML
file that plays the package's source media (when a playable file is
present) alongside a timeline of the package's timestamped evidence.

```
viewer.html          # one self-contained HTML file (inline CSS + JS + JSON)
```

The viewer references the package's own media/keyframe files only via
safe, path-contained **relative links** computed from the output file's
location. It copies no media and transcodes nothing.

## What the viewer shows

- **Package header** — package id, source filename, status, duration,
  dimensions, has-audio, created-at, clulatent version, validation status,
  lock status, track count, and whether a built-in V1 index is present.
- **Media playback** — a `<video controls>` when a playable source file is
  present; otherwise a clear "media is not directly playable from this
  viewer" message. The timeline works either way.
- **Evidence timeline** — a horizontal track scaled by the package
  duration, with one clickable marker per event across every track
  (keyframes, audio/speech events, visual-change and changed-region
  candidates, evidence bundles, agent reviews, plus any other track the
  package carries). Clicking a marker seeks the media and opens the event
  in the details panel.
- **Track toggles** — a checkbox per track to show/hide its markers.
- **Event details panel** — the clicked event's id, track, type, timestamp
  range, confidence, a small bounded set of scalar payload fields, and a
  collapsible raw-JSON view of that one event.
- **Keyframe strip** — a bounded strip of keyframe thumbnails (safe
  relative links); clicking a thumbnail seeks the media.
- **V1 index links** — when `index/v1/` exists, links to
  `agent_context.json`, `agent_context.md`, `ask_prompt.md`, and
  `index_manifest.json` as package-relative artifacts.
- **Safety / caveats panel** — always visible; states that the viewer
  plays candidate evidence, not scene meaning.

## How it works

The generator reads the package **only through the Phase 3.18
`PackageReader`** (tracks, events, lock status, validation) and reuses the
Phase 3.22 `v1_package_index` helpers for the index links. It builds one
bounded JSON payload and embeds it in a `<script type="application/json">`
element; a small inline script renders the timeline, toggles, details
panel, and keyframe strip client-side. There is no folder-walking, no
external script/stylesheet/font/image link, no CDN, and no network access.

Media and keyframe links are computed with the same lexical
`os.path.abspath` / `os.path.relpath` technique used by the Phase 3.12
contact sheet, so links stay valid across symlinked prefixes (e.g. macOS
`/tmp` → `/private/tmp`). Every package-relative path is first resolved
through `resolve_in_package` (containment- and symlink-checked); an
unsafe path yields no link (and a notice) rather than an unsafe href.

## Usage

```bash
clulatent playback-viewer PACKAGE.clulatent --output viewer.html
clulatent playback-viewer PACKAGE.clulatent -o viewer.html --max-events 1000 --force
```

- `--output/-o` (required) — where to write the HTML. The package is never
  written to.
- `--max-events` — cap on the number of timeline events embedded (default
  1000). Truncation is always flagged in the page.
- `--force` — overwrite an existing `--output`.

Open `viewer.html` directly in a browser. If the output lives next to the
package (or anywhere a relative path to the package can be formed), the
media and keyframe links resolve and play; otherwise the viewer still
renders the full evidence timeline.

## Safety caveats

- The viewer re-presents **candidate evidence only**. It performs no
  semantic interpretation and makes no object/person/face/action/scene/
  speech/intent claim.
- Visual-change and changed-region markers are numeric pixel-difference
  candidates, not recognition. Evidence bundles collect existing records
  for a time range; agent reviews report evidence support and gaps only.
- Authored prose (the caveats and section notes) is scanned by a
  forbidden-language guard. Opaque, user-controlled data — a source
  filename, a package id, a track name, an adapter payload field — is
  never scanned, so a video literally named
  `the_video_shows_intent.mp4` never trips the guard and is never
  censored.

## Non-goals (hard boundaries)

- **No new evidence lane** and **no semantic interpretation.**
- **No LLM / OpenAI / Claude / network / local vision model / new ML
  dependency**, and no FFmpeg/Pillow invocation.
- **No claim about people, objects, actions, intent, identity, or scene
  meaning.**
- No web server, no React/Next.js/npm/bundler, no external font/CDN/remote
  asset.
- No macOS file association / Finder / VS Code extension / MCP server /
  `.clubin` binary.
- The only file written is the caller-chosen `--output`. No package
  mutation, no receipt, no index refresh, no evidence generation.
- No unrelated refactors.

## Files

- `src/clu_latent/v1_playback_viewer.py` (new) —
  `build_playback_viewer_payload` (read-only, in-memory),
  `render_playback_viewer_html`, `build_playback_viewer`,
  `write_playback_viewer` (writes only `--output`), the authored-language
  guard, and `PlaybackViewerError`.
- `src/clu_latent/cli.py` — the `playback-viewer` top-level command.
- `tests/test_v1_playback_viewer.py` (new).
- `README.md` — Phase 3.23 bullet.

## Verification

```bash
python -m pytest tests/test_v1_playback_viewer.py -q
python -m pytest tests/test_v1_package_index.py tests/test_v1_ask_bundle.py \
                 tests/test_v1_open_ask_demo.py tests/test_agent_context_export.py \
                 tests/test_v1_build_pipeline.py -q
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
