# Phase 3.12.1 — Keyframe Contact Sheet Link Fix

## Summary

A follow-up bug fix to Phase 3.12 (Keyframe Contact Sheet Preview). The
contact sheet rendered all metadata correctly, but the keyframe `<img>`
links were broken whenever the output HTML file was written **outside**
the `.clulatent` package directory.

## The bug

For a run like:

```
clulatent keyframes contact-sheet "$OUT/movie.clulatent" \
    --output "$OUT/keyframe-contact-sheet.html" --force
```

the HTML file lives at `$OUT/keyframe-contact-sheet.html` while the images
live at `$OUT/movie.clulatent/media/keyframes/000000.jpg`. The frozen
Phase 3.12 code computed each `<img src>` from the **symlink-resolved**
image path (`resolve_in_package` calls `Path.resolve`) but the
**un-resolved** output directory. Because only one side was resolved, the
two paths could sit in different symlink namespaces — most visibly on
macOS, where `/tmp` is a symlink to `/private/tmp`. The resulting relative
link did not point at the real file, so browsers showed broken images.

## The fix

In `src/clu_latent/keyframe_preview.py`, `_relative_href` now computes the
link from the keyframe image **as it sits inside the package** to the
output file's directory, keeping both sides in the same namespace:

- Actual image path: `package_path / package_relative_image_path`
  (a plain join — no symlink resolution).
- Output base: the output file's parent directory.
- Both are made absolute with `os.path.abspath` (lexical `..`
  normalisation, **no** symlink following), then
  `os.path.relpath(actual, base)` gives the browser-usable relative link.
- The result is converted to a POSIX path and URL-quoted (slashes
  preserved) so filenames containing spaces or other characters still
  produce a valid, safe href.

`resolve_in_package` is still used — but only for its security role
(containment / symlink rejection) and to check whether the image file
exists on disk. The link itself is computed from the un-resolved join.

The package-relative path (`media/keyframes/000000.jpg`) continues to be
displayed as evidence metadata under each frame, independently of the
`<img src>`.

Behaviour that is unchanged:

- A **missing** keyframe file still renders a *missing-evidence* state
  rather than a broken `<img>`.
- Still read-only: no package mutation, no receipts, no index rebuild, no
  FFmpeg, no new frame extraction, no dependencies, no network, no
  JavaScript.
- Still shows visual evidence without interpreting it — keyframes are
  evidence, not interpretation, and the preview does not describe what
  happens in the clip.

## Verification

```
OUT="/tmp/clulatent-movie-audio-digest-test"
clulatent keyframes contact-sheet "$OUT/movie.clulatent" \
    --output "$OUT/keyframe-contact-sheet.html" --force
open "$OUT/keyframe-contact-sheet.html"
```

Every `<img src>` now resolves — e.g.
`movie.clulatent/media/keyframes/000000.jpg` — from the output file's
directory to the real image, and the trust caveats remain intact.

## Files

- `src/clu_latent/keyframe_preview.py` — `_relative_href` rewritten.
- `tests/test_keyframe_preview.py` — regression tests: links point through
  the package dir, every `<img src>` resolves from the output dir, nested
  output dirs resolve, package-relative metadata still shown, missing file
  emits no broken image, and no package/receipt mutation.
