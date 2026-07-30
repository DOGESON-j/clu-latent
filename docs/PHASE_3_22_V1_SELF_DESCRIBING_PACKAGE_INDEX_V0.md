# Phase 3.22 — V1 Self-Describing Package Index v0

## What this phase is for

- Phase 3.19 made a `.clulatent` package **buildable** from a video and
  **exportable** into one agent-context document.
- Phase 3.20 made it **explainable** (an evidence-only summary and a safe
  ask prompt).
- Phase 3.21 made it **askable** (a safe, evidence-grounded ask bundle).
- Phase 3.22 makes it **self-describing**:

> "When CLU, Codex, or another agent opens a V1-built `.clulatent`
> package, the package itself should already contain the ready-to-use V1
> agent context, a Markdown summary, a safe prompt, and an index manifest.
> The agent should know where to start without requiring a separate
> sidecar export."

Every V1 build now writes an agent-readable index **inside** the package:

```
PACKAGE.clulatent/
  index/
    v1/
      agent_context.json    # Phase 3.19 agent context (the machine contract)
      agent_context.md      # the same context as a readable brief
      ask_prompt.md         # Phase 3.20 safe ask prompt
      index_manifest.json   # descriptor: hashes, validation, counts, caveats
```

## What each file is for

- **`agent_context.json`** — the stable, versioned Phase 3.19 agent context
  (`clulatent.agent_context.v0`). This is the machine contract: package
  facts, tracks, event counts, time coverage, candidate-evidence summaries,
  evidence-bundle / agent-review records, caveats, and safe next steps.
- **`agent_context.md`** — the same document rendered as a human-readable
  brief. No new information beyond the JSON.
- **`ask_prompt.md`** — the Phase 3.20 ready-to-paste prompt that constrains
  a downstream agent to answer strictly from the package's evidence, cite
  IDs/timestamps, report unknowns, and never infer
  people/objects/actions/intent/identity/scene meaning.
- **`index_manifest.json`** — a small descriptor of the built-in index:

  ```json
  {
    "schema_id": "clulatent.v1_package_index.v0",
    "schema_version": "0.1.0",
    "package_id": "…",
    "generated_at": "…ISO8601…",
    "generated_by": {"tool": "clulatent", "version": "0.1.0"},
    "source_agent_context_schema": {"schema_id": "…", "schema_version": "…"},
    "source_validation": {"valid": true, "error_count": 0, "warning_count": 0},
    "package": {"duration_ms": …, "status": "…", "clulatent_version": "…",
                "track_counts": {"keyframes": …, …}},
    "evidence": {"evidence_bundle_ids": [...], "agent_review_ids": [...]},
    "artifacts": [
      {"name": "agent_context.json", "path": "index/v1/agent_context.json",
       "media_type": "application/json", "size_bytes": …, "sha256": "…"},
      …,
      {"name": "index_manifest.json", "path": "index/v1/index_manifest.json",
       "media_type": "application/json", "size_bytes": null,
       "sha256": null, "self": true}
    ],
    "caveats": ["…"],
    "note": "This is a package index artifact, not a lock and not a certificate. …"
  }
  ```

  The manifest lists each content artifact with its package-relative path
  and the SHA-256 of its exact bytes. Its own entry is self-referential
  (a file cannot record its own hash), so `sha256` is `null` and
  `"self": true`.

## How Codex / CLU consume it

An agent opening the package reads `index/v1/agent_context.json` for the
machine contract (or `agent_context.md` for a quick human view), and pastes
`ask_prompt.md` ahead of a question to keep itself grounded. It can read
`index_manifest.json` to learn the package id, when the index was
generated, which tracks and evidence IDs existed at that time, and the
artifact hashes — so it can detect that the built-in index is **stale**
relative to the package's current tracks (recompute a file's hash and
compare). No package-internal folder walking is required.

## How to refresh

The built-in index is a snapshot of the package's evidence at generation
time. After the package's tracks change (a new lane, a new evidence bundle,
a new review), regenerate it:

```bash
clulatent package-index refresh PACKAGE.clulatent
```

`refresh` re-renders the agent context from the package's **current** state
and overwrites the four `index/v1/` artifacts. It generates **no** new
evidence lane (no visual-change / changed-region / evidence-bundle /
agent-review write) and writes **no** receipt. It refuses to write against a
package that carries a valid integrity lock.

Inspect the built-in index read-only:

```bash
clulatent package-index inspect PACKAGE.clulatent
```

`inspect` reports whether `index/v1/` exists and, if so, the manifest's
schema, package id, generation time and tool, the validation status when
generated, each artifact's path and content hash, and the caveats. It never
mutates the package and never writes a receipt.

## Safety caveats

- The index re-packages **candidate evidence only**. It performs no
  semantic interpretation and makes no object/person/face/action/scene/
  speech/intent claim.
- The index manifest is **not a lock and not a certificate**. It records
  artifact hashes at generation time only; it does not certify what the
  package's evidence means. (For integrity guarantees over the package's
  canonical files, use `clulatent lock` / `clulatent verify-lock`.)
- Authored prose (the manifest caveats and note) is scanned by a
  forbidden-language guard. Opaque, user-controlled data — a source
  filename, a package id, a track name — is never scanned, so a video
  literally named `the_video_shows_intent.mp4` never trips the guard.

## Non-goals (hard boundaries)

- **No new evidence lane** and **no semantic interpretation.**
- **No LLM / OpenAI / Claude / network / local vision model / new ML
  dependency**, and no FFmpeg/Pillow invocation.
- **No claim about people, objects, actions, intent, identity, or scene
  meaning.**
- No macOS file association / Finder / VS Code extension / MCP server /
  `.clubin` binary.
- The only mutation is writing the four `index/v1/` artifacts. No receipt
  is written and no evidence track is generated.
- No unrelated refactors.

## Files

- `src/clu_latent/v1_package_index.py` (new) — `build_v1_package_index`
  (read-only, in-memory), `write_v1_package_index` (mutating writer),
  `refresh_v1_package_index` (regenerate), `load_v1_index_manifest`,
  `v1_index_exists`, the forbidden-language guard, and `V1PackageIndexError`.
- `src/clu_latent/v1_build.py` — writes the built-in index by default as the
  final V1 build step (threaded via `write_index=True`; new
  `V1BuildResult.index_written` / `index_artifacts` fields).
- `src/clu_latent/cli.py` — `build-video --no-agent-index`; the
  `package-index` group with `refresh` and `inspect`.
- `tests/test_v1_package_index.py` (new).
- `README.md` — Phase 3.22 bullet.

## Example workflow (copy-paste)

```bash
MOVIE="/path/to/synthetic-or-rights-cleared-demo.mp4"
OUT="/tmp/clulatent-322-index-demo"
mkdir -p "$OUT"

# Build a V1 package; the built-in index is written by default.
clulatent build-video "$MOVIE" -o "$OUT/movie.clulatent" --profile v1 --force

# The package is now self-describing.
find "$OUT/movie.clulatent/index/v1" -maxdepth 1 -type f -print
clulatent package-index inspect "$OUT/movie.clulatent"

# After tracks change, regenerate the built-in index.
clulatent package-index refresh "$OUT/movie.clulatent"
```

## Verification

```bash
python -m pytest tests/test_v1_package_index.py -q
python -m pytest tests/test_v1_build_pipeline.py tests/test_v1_ask_bundle.py \
                 tests/test_v1_open_ask_demo.py tests/test_agent_context_export.py -q
python -m pytest tests/test_package_reader.py -q
python -m compileall src/clu_latent
git diff --check
python -m pip check
```
