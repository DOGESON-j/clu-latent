# CLULatent V1 Build Report

Date: 2026-07-30

## Verified behavior

- The V1 profile contract, offline conformance runner, deterministic portable
  archive, bounded extraction, package reader, validator, index verification,
  local viewer, and agent-read surfaces pass their focused tests.
- The release-critical Python suite passes on macOS with Python 3.14.
- The wheel and source distribution build successfully and pass `twine check`.
- Clean, non-editable installs from both distributions run `--version`,
  `--help`, `doctor`, `profile inspect v1`, all 16 conformance fixtures, and
  the offline demo without a source-tree `PYTHONPATH`.
- The release demo is generated from synthetic FFmpeg sources, validates as
  V1-compatible, and carries fresh package and agent-read indexes.

## Current limitations

- FFmpeg and ffprobe are external system dependencies for media build
  operations.
- Pillow is an optional extra for visual evidence lanes.
- Compatibility is structural and does not certify semantic truth.
- The viewer is local and static; CLULatent does not provide cloud
  collaboration or a Studio UI.
