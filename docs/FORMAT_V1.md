# CLULatent V1 format

Profile identifier: `clulatent.profile.v1`
Contract version: `1.0`

The editable package is a directory containing `manifest.json`, source/hash
facts, receipts, derived indexes, media, and declared JSONL tracks. Required
compatibility checks cover manifest/schema readability, stable package
identity, supported format identity, safe relative paths, reader and validator
compatibility, required structure, and a fresh V1 package index.

Declared tracks are conditional: when present, their paths must resolve safely,
their shared envelope identity must be supported, and their records must be
bounded and readable.

Optional capabilities include playable source media, keyframes, visual-change
and changed-region candidates, audio and speech events, evidence bundles,
agent reviews, and agent-read artifacts. Their absence does not invalidate the
baseline profile.

Requirement states are `PASS`, `FAIL`, `MISSING`, `UNKNOWN`, and
`NOT_APPLICABLE`. Compatibility states are `COMPATIBLE`, `INCOMPATIBLE`, and
`UNKNOWN`. Unknown evidence is never converted to pass.

CLULatent V1 compatibility certifies package/profile compatibility, not
semantic truth about media content.
