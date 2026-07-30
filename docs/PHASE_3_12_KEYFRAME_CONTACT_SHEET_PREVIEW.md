# Phase 3.12 — Keyframe Contact Sheet Preview

## Summary

Phase 3.12 gives CLULatent a **read-only visual evidence surface**: a
static, local-first HTML **contact sheet** rendered from the keyframes
that ingest already stored in a `.clulatent` package.

Phase 3.11 (the Real Clip Evidence Gap Debugging Harness) proved that,
given only a package, CLULatent cannot yet honestly describe what happens
visually in a clip. Phase 3.12 does not close that gap — it **exposes the
visual evidence that already exists** so a human, or a future
LLM-adapter, can inspect the frame sequence directly without relying on a
transcript or on semantic inference.

## Core rule

> **Show visual evidence. Do not interpret visual evidence.**

This is a **visual evidence browser, not a visual understanding system.**

## What it does

Command:

```
clulatent keyframes contact-sheet PACKAGE --output contact-sheet.html [--force]
```

It reads the package's `manifest.json` and its `keyframes` track and
writes a single self-contained HTML file containing:

- the package id
- the source filename (if recorded)
- the clip duration
- the keyframe count
- a validation/trust caveat (reported as **evidence only**)
- a grid of the stored keyframes
- under each frame: its timestamp, its keyframe event id, and the
  relative package path of the image
- the caveat: **"Keyframes are evidence, not interpretation."**
- the caveat: **"This preview does not describe what happens in the clip."**

The `<img>` links are **relative** links from the output file to the
keyframe images inside the package. Nothing is inlined as base64.

## What it deliberately does NOT do

- No visual AI, no frame captioning.
- No scene-meaning, object-identity, intent, or emotion inference.
- No OCR, no motion analysis.
- No FFmpeg / no new frame extraction — it only renders frames that
  already exist.
- No package mutation: it never writes a manifest, track, receipt,
  index, or lock file, and touches nothing except `--output`.
- No ML dependency, no Pillow/OpenCV, no new semantic events, no analysis
  lanes.
- No publish, push, or tag.

## Safety properties

The generator reuses `report.py`'s HTML safety helpers:

- Every package-originated string is stripped of control characters and
  HTML-escaped before it reaches the document.
- The output has **no JavaScript**, no external stylesheet/font/image
  link, and no CDN or network reference; CSS is a small inline `<style>`
  block.
- Output is **bounded** (`DEFAULT_MAX_KEYFRAMES`) so a pathological
  package cannot produce an unbounded document.
- Untrusted `payload.path` values are resolved through
  `resolve_in_package`; a path that escapes the package or is otherwise
  unsafe is reported as missing evidence, never followed.

## Degraded-input handling

- **Missing keyframe image** → shown as *missing evidence*, not a crash.
- **Empty keyframes track / no keyframes** → a safe empty state.
- **Invalid package (but readable manifest)** → renders, with a
  validation/trust caveat noting it does not validate.
- **Fundamentally unreadable package** (missing/invalid `manifest.json`)
  → a clean `KeyframePreviewError` / exit-1 CLI error, never a traceback.

## Files

- `src/clu_latent/keyframe_preview.py` — the contact sheet generator.
- `src/clu_latent/cli.py` — the `keyframes contact-sheet` command.
- `tests/test_keyframe_preview.py` — tests for rendering, safety,
  degraded inputs, no-mutation, and the trust caveats.

## Trust position

Adapters produce evidence, not truth; generated does not mean canonical.
This phase gives CLULatent a visual evidence surface. It does **not** make
CLULatent understand visuals.
