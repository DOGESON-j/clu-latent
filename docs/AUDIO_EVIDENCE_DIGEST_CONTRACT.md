# Audio Evidence Digest Contract

This is the living reference for CLULatent's audio evidence digest
layer, first designed in Phase 3.3
(`docs/PHASE_3_3_AUDIO_EVIDENCE_DIGEST_CONTRACT.md`). It builds on
`docs/TRUST_MODEL.md`, `docs/AUDIO_EVIDENCE_LANES.md` (the Phase 3.2
lane catalog), and the shared `EventEnvelope` (`id`, `type`,
`t_start_ms`, `t_end_ms`, `producer`, `confidence`, `payload`).

This document is design/reference only. No digest generator,
retrieval tool, or model is implemented here.

## Core rule

**Store deep. Show shallow. Retrieve detail only when needed.**

- Full-resolution audio evidence (Level 0 source audio, Level 1 dense
  metrics) is always stored in the package, never discarded.
- A default consumer — especially an LLM — is shown a small, bounded,
  ranked digest (Level 4 summaries, Level 5 context packets), not the
  raw material.
- Anything omitted from a digest is still in the package and
  retrievable by time range or evidence id; a digest never claims to
  be the whole of what CLULatent recorded.

## The multi-resolution audio evidence pyramid

| Level | Name | Record shape | Purpose |
|---|---|---|---|
| 0 | Source audio | media file (package-relative path) | Ground material; never re-encoded or mutated by digest tooling |
| 1 | Dense metrics | `audio_feature_series` | Regularly-sampled numeric arrays referenced by path, not inlined |
| 2 | Events | Phase 3.2 lane events (`audio_energy_events`, etc.) | Discrete, timestamped, bounded evidence |
| 3 | Segments | `audio_digest_segment` | Merged spans grouping related Level 2 events |
| 4 | Summaries | `payload.summary` on a segment or packet | Short, qualified, evidence-linked text |
| 5 | LLM context packets | `audio_llm_context_packet` | Bounded, ranked, retrieval-pointer-bearing digest for an agent |

Every level traces back to the level below it. A Level 5 packet must
be able to point (via `linked_event_ids` / `linked_series_refs`) all
the way down to the Level 1/0 evidence that supports it — a digest is
a *view* over stored evidence, never a replacement for it.

---

## `audio_feature_series` (Level 1)

**Purpose:** describe a dense, regularly-sampled numeric array (e.g. a
loudness curve, a spectral-centroid curve) **without** inlining the
array itself into the event payload.

**Rule: reference dense arrays by package-internal path, never as a
raw dump.** The array lives in its own package-relative file (e.g.
`media/audio_features/loudness_rms.jsonl` or a binary format); the
`audio_feature_series` event is a small, bounded pointer/descriptor,
just like every other CLULatent source reference is a path, not an
inline blob.

**Payload sketch:**

```json
{
  "series_kind": "loudness_rms | spectral_centroid | energy | pitch_hz | other",
  "sample_rate_hz": 50.0,
  "window_ms": 20,
  "value_unit": "adapter_defined",
  "series_ref": "media/audio_features/loudness_rms.jsonl",
  "sample_count": 9000,
  "notes": "optional"
}
```

**Rules:**

- `series_ref` must resolve inside the package (same
  `security.paths.resolve_in_package` containment/symlink-safety check
  every other package-relative path reference uses).
- The descriptor event itself stays small and bounded; only the
  referenced file may be large.
- `value_unit` must be documented — never implied as a calibrated,
  universal unit unless the producer says so.
- A `audio_feature_series` event is Level 1 evidence: dense, not yet
  interpreted. It never carries a qualified/hedged label — that
  belongs to Level 2+ events and above, which are built *from* the
  series, not the series record itself.

---

## `audio_digest_segment` (Level 3)

**Purpose:** group a time range's worth of related Level 2 lane
events (and, optionally, Level 1 series windows) into one reviewable,
summarizable unit.

**Payload sketch:**

```json
{
  "segment_label": "rising-tension window | calm passage | ... (short, hedged)",
  "summary": "Loudness and rhythmic density both increase across this window; linked evidence suggests a tension-like buildup candidate.",
  "linked_event_ids": ["energy_000012", "rhythm_000006", "comp_000004"],
  "linked_series_refs": [
    {"series_ref": "media/audio_features/loudness_rms.jsonl", "t_start_ms": 12000, "t_end_ms": 18500}
  ],
  "salience": 0.81
}
```

**Rules:**

- `summary` must use only the allowed, hedged vocabulary (see below)
  and must not introduce a claim not traceable to `linked_event_ids`.
- `linked_event_ids` should reference real Level 2 event ids wherever
  possible — a segment with no linked evidence is not a valid digest
  segment ("no pure vibes," restated from Phase 3.2).
- `linked_series_refs` point at a Level 1 series **and a bounded time
  window within it** — never the whole series unbounded.
- `salience` (see "Salience and ranking" below) is required so a
  Level 5 packet can rank and select among many segments.

---

## `audio_llm_context_packet` (Level 5)

**Purpose:** a bounded, ranked digest safe to hand directly to an LLM
— the only audio-evidence record shape this contract considers
default-safe for an LLM consumer.

**Payload sketch:**

```json
{
  "time_range": {"t_start_ms": 0, "t_end_ms": 120000},
  "top_segments": [
    {
      "segment_id": "seg_000003",
      "label": "tension-like buildup candidate",
      "summary": "Linked evidence suggests a tension-like buildup candidate: loudness and rhythmic density increase together.",
      "salience": 0.81,
      "linked_event_ids": ["energy_000012", "rhythm_000006", "comp_000004"]
    }
  ],
  "omitted_segment_count": 14,
  "retrieval": {
    "by_time_range": "retrieve tracks/audio_digest_segment.jsonl records overlapping a given t_start_ms/t_end_ms",
    "by_evidence_id": "retrieve any linked_event_ids or linked_series_refs entry by id/path"
  },
  "caveat": "This packet describes measurable acoustic evidence and qualified candidate effects only. It is evidence, not truth, and does not establish intent, meaning, or a listener's emotional response. Omitted, lower-salience evidence still exists in the package and can be retrieved by time range or evidence id."
}
```

**Rules:**

- **Bounded token/context budget.** A packet has a fixed maximum
  number of `top_segments` and a fixed maximum character length per
  `summary` (mirroring Phase 3.0's `DEFAULT_MAX_TIMELINE_EVENTS` /
  `DEFAULT_MAX_PAYLOAD_CHARS` bounding pattern) — a pathological
  package must not be able to produce an unbounded packet.
- **`caveat` is required, non-optional, and non-empty.** It must
  restate evidence-not-truth and note that omitted evidence is
  retrievable, not discarded.
- **No raw dumps.** A context packet never embeds a Level 1 series
  array or an unbounded list of Level 2 events — only ranked,
  bounded, already-summarized Level 3 segments plus retrieval
  pointers.
- **`omitted_segment_count`** must be present whenever segments were
  ranked out of the packet, so a consumer knows the packet is a view,
  not the whole record.
- A context packet's `summary`/`label` fields obey the same allowed/
  forbidden vocabulary as every other level — being bounded and
  LLM-facing is not license to be less conservative.

---

## Salience and ranking

`audio_digest_segment.payload.salience` is a bounded `[0.0, 1.0]`
score (same bound as `confidence` elsewhere) used to rank segments for
inclusion in an `audio_llm_context_packet`'s `top_segments`. Salience
reflects the *strength and density of linked evidence* — e.g. how many
Level 2 events support the segment, how large the measured change is —
never a claim about narrative importance, emotional weight, or
intent. Ranking is:

1. Deterministic and reproducible from the linked evidence (same
   input evidence must produce the same salience).
2. Never the sole basis for omitting a segment from the *package* —
   only from a given *packet*. Segments below the packet's cutoff
   remain in `tracks/audio_digest_segment.jsonl` and are retrievable.
3. Documented by the producer (a future digest builder must record
   its salience method in its receipt, mirroring the existing
   `AnalysisAdapterReceipt` contract).

## Bounded token/context packets

An `audio_llm_context_packet` is deliberately small:

- A fixed maximum count of `top_segments` per packet.
- A fixed maximum character length per `summary` field.
- A fixed maximum character length for the `caveat` field (still long
  enough to be meaningful, but not unbounded).
- No embedded Level 0/1 raw material under any circumstance.

Exact numeric bounds are an implementation detail for whichever future
phase builds the digest generator (the same way Phase 3.0 defines
`DEFAULT_MAX_TIMELINE_EVENTS = 500` etc. only once a real generator
exists) — this contract only fixes the *shape* and the *rule* that
bounds must exist.

## Retrieval by time range and evidence id

Two read-only retrieval axes, for a future tool to implement (not
implemented this phase):

1. **By time range.** Given `t_start_ms`/`t_end_ms`, return every
   Level 1–3 record whose span overlaps the requested range.
2. **By evidence id.** Given one or more ids (event id, segment id, or
   series ref), return exactly those records, plus (optionally) the
   series window(s) they reference.

Both retrieval paths are strictly read-only, never mutate the package,
and reuse the same bounded-reader pattern already used by
`src/clu_latent/report.py` (`security.jsonl.iter_jsonl_bounded`,
`security.paths.resolve_in_package`) rather than inventing a new I/O
path.

## Allowed language

- "candidate effect"
- "linked evidence suggests"
- "compression/limiting evidence"
- "rhythmic density increase"
- "tension-like buildup candidate"

This extends, and must stay consistent with, the Phase 3.2 vocabulary
in `docs/AUDIO_EVIDENCE_LANES.md` — the digest layer is not a place to
relax hedging just because the output is smaller.

## Forbidden claims

None of the following may appear as an assertion at any digest level,
including inside a `summary` or a `caveat`:

- "proves intent"
- "manipulation"
- "makes viewer afraid"
- "CLULatent understands audio"
- "semantic audio truth"
- "definitely exact instrument/source"

## Caveats and evidence-not-truth warnings

Every `audio_llm_context_packet` carries a mandatory `caveat` field.
At minimum it must:

- State that the packet's contents are **evidence, not truth**.
- State that the packet does **not** establish intent, meaning, or a
  listener's emotional response.
- State that omitted or lower-salience evidence still exists in the
  package and is retrievable, not discarded.

`audio_digest_segment` records inherit the same obligation implicitly
through their hedged `summary` vocabulary, but the explicit `caveat`
field is required only at the Level 5 packet — the level actually
handed to an LLM by default.

## See also

- [`docs/TRUST_MODEL.md`](TRUST_MODEL.md) — the core evidence-not-truth
  principle this document restates for the audio digest layer.
- [`docs/AUDIO_EVIDENCE_LANES.md`](AUDIO_EVIDENCE_LANES.md) — the
  Phase 3.2 lane catalog (Level 2 of this pyramid).
- [`docs/PHASE_3_0_STATIC_PACKAGE_REPORT_VIEWER.md`](PHASE_3_0_STATIC_PACKAGE_REPORT_VIEWER.md)
  — the bounded, read-only, safety pattern this contract's future
  retrieval tool should reuse.
- [`docs/PHASE_3_3_AUDIO_EVIDENCE_DIGEST_CONTRACT.md`](PHASE_3_3_AUDIO_EVIDENCE_DIGEST_CONTRACT.md)
  — the phase that introduced this contract.
