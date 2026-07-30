# Phase 3.26 — V1 Package Index Verify + Staleness Check v0

## What this answers

`clulatent package-index verify PACKAGE` answers one question: **is the
package's V1 self-description present, internally consistent, and fresh
relative to the evidence package it describes?**

It is verification, not repair. It never regenerates an index, never
writes an artifact, and never touches the manifest, tracks, receipts, or
lock. It only opens the package (read-only) and the two independent index
manifests already on disk, and reports what it finds.

## Command

```
clulatent package-index verify PACKAGE
```

Read-only, no options beyond the package path. Prints a compact status
report to the terminal.

## What it checks, for each of the two independent V1 indexes

CLULatent carries two independent built-in indexes under `index/v1/` that
do not cross-reference each other:

- the **package index** (Phase 3.22): `agent_context.json`,
  `agent_context.md`, `ask_prompt.md`, `index_manifest.json`
- the **agent-read index** (Phase 3.24): `agent_read_micro.md`,
  `agent_read_model.json`, `agent_read_model.md`,
  `agent_read_windows.jsonl`, `agent_read_manifest.json`

For each, `verify` checks:

- **presence** — does the manifest and each declared artifact file exist?
- **internal consistency** — does each artifact's on-disk content still
  match the sha256 hash recorded in its own manifest at generation time?
  does the manifest's recorded `package_id` match the package's current
  id?
- **reference resolution** — do the manifest's recorded evidence bundle ids
  and agent review ids still resolve to real events in the package's
  tracks?
- **freshness** — do the package's current track counts and evidence/review
  ids still match what the manifest recorded when it was generated?

## Status labels

| Label | Meaning |
|---|---|
| `FRESH` | Present, internally consistent, and every freshness check that could be performed matched. |
| `STALE` | Present and internally consistent, but the package's current tracks/evidence/reviews have diverged from what the index recorded. |
| `MISSING` | The index manifest (or a specific artifact it declares) does not exist on disk. |
| `INVALID` | The index exists but is internally broken — a content hash mismatch, a package id mismatch, or a reference that no longer resolves. |
| `UNKNOWN` | The index exists and passed the checks that could be performed, but the current package state could not be recomputed to check freshness (reported honestly rather than assumed fresh). |
| `PASS` | Used only for individual artifact status entries that match their recorded hash. |

`verify` never invents a freshness guarantee the current index format
cannot prove — where provenance is insufficient, it reports `UNKNOWN`
rather than `FRESH`.

## Read-only, not semantic certification

`verify` makes no claim about what the package's evidence means. It
reports structural and referential facts about the index artifacts
themselves — hashes, ids, counts, resolvability — never an interpretation
of video content. It performs no evidence generation, no FFmpeg/Pillow
analysis, and no model or network call.

## Remediation

When a component is `MISSING`, `STALE`, or `INVALID`, `verify` prints the
exact command that would fix *that* component, and only that component:

- for the package index: `clulatent package-index refresh PACKAGE`
- for the agent-read index: `clulatent agent-read write-index PACKAGE`

It never runs either command itself.

## Relationship to other commands

- `package-index refresh` — writes/regenerates the Phase 3.22 package
  index. `verify` tells you whether you need to run it; it never runs it.
- `agent-read write-index` — writes the Phase 3.24 agent-read index.
  `verify` tells you whether you need to run it; it never runs it.
- `package-index inspect` — prints the *contents* of an existing package
  index without judging whether it is still accurate. `verify` judges
  accuracy; `inspect` does not.

## Known limitations

- Freshness detection compares track counts and referenced evidence/review
  ids, not full track content — a track whose event count is unchanged but
  whose event content changed underneath it would not be detected as
  stale. This is a deliberate V0 bound: it uses only the provenance the
  current index format actually stores, and reports `UNKNOWN` rather than
  fabricate stronger guarantees.
- `verify` is intentionally silent about, and unaffected by, a package's
  integrity lock: unlike `refresh`/`write-index`, it can and does report
  status against a locked package, since it performs no write.

## Verification

```bash
python -m pytest tests/test_v1_package_index_verify.py -q
python -m pytest tests/test_v1_package_index.py tests/test_v1_agent_read_model.py \
  tests/test_v1_open_package.py tests/test_v1_playback_viewer.py -q
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
