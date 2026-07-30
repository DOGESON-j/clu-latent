"""Phase 3.20: the V1 "open and ask" demo kit.

Phase 3.19 made the V1 file loop real: a video becomes a `.clulatent`
package, and `agent_context.build_agent_context` opens that package
(through the Phase 3.18 read-only reader) into one stable JSON/Markdown
document. Phase 3.20 makes that document *usable* without adding a single
new evidence lane:

- `build_evidence_summary` renders a short, human-readable, evidence-only
  summary of a package — what evidence exists, what is missing, and what
  is safe to do next.
- `build_ask_prompt` renders a ready-to-paste prompt that instructs
  another agent (CLU, Codex, ...) to answer questions about the package
  strictly from evidence, citing IDs/timestamps and refusing to infer
  people, objects, actions, intent, identity, or scene meaning.

Boundaries (inherited from the agent-context layer this module builds on):

- Everything here goes through `agent_context.build_agent_context`, which
  opens the package via `package_reader.open_package`. This module never
  walks the folder tree, never assumes a filename, and never touches a
  track file directly.
- Strictly read-only: it never mutates the package, never writes a
  receipt, never touches lock state, and never runs FFmpeg/Pillow/an ML
  model/a network call. It adds no evidence and performs no semantic
  interpretation.
- The summary emits **candidate evidence only**: no object, person, face,
  action, scene, speech, or intent claim. Its authored prose is scanned
  by `assert_summary_no_forbidden_language` for banned semantic phrasing.
  The ask prompt deliberately *names* those forbidden concepts in order
  to prohibit them to the downstream agent, so the prompt is authored
  instruction text and is not subject to that scan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import agent_context as agent_context_mod
from .agent_context import (
    CORE_LANES,
    FORBIDDEN_CONTEXT_PHRASES,
    build_agent_context,
)

__all__ = [
    "OpenAskError",
    "SUMMARY_FORBIDDEN_PHRASES",
    "build_evidence_summary",
    "render_evidence_summary",
    "build_ask_prompt",
    "render_ask_prompt",
    "assert_summary_no_forbidden_language",
]

# The evidence-only summary must never emit a semantic claim. We reuse the
# agent-context layer's banned phrases (person appeared, object moved, the
# video shows, intent, identity, ...) and add the phase's own
# "confirmed scene". `dict.fromkeys` de-duplicates while preserving order.
SUMMARY_FORBIDDEN_PHRASES: tuple[str, ...] = tuple(
    dict.fromkeys(FORBIDDEN_CONTEXT_PHRASES + ("confirmed scene",))
)

# Authored section labels for the summary. These are the only strings the
# summary guard scans (plus the caveats/next-steps carried over from the
# agent context, which are already guarded there) — never opaque,
# user-controlled data like a package id, a source filename, or a track
# id. So a video literally named "identity.mp4" never trips the guard.
_SUMMARY_LABELS: tuple[str, ...] = (
    "CLULatent evidence summary",
    "Package facts",
    "package id",
    "duration",
    "source",
    "status",
    "clulatent version",
    "Validation and lock",
    "validation",
    "lock",
    "Tracks present",
    "present",
    "present but empty",
    "unavailable (never generated)",
    "record(s)",
    "unknown track",
    "Evidence (candidate only)",
    "visual-change candidates",
    "changed-region candidates",
    "no candidate evidence lanes present",
    "Major evidence windows",
    "timeline window contains",
    "evidence records",
    "no evidence windows to report",
    "Evidence bundle IDs",
    "reviews evidence bundle",
    "review status",
    "Agent review IDs",
    "none",
    "Unavailable / missing evidence",
    "all core lanes present",
    "Caveats",
    "Safe next steps",
)

_RULE = "=" * 60


class OpenAskError(ValueError):
    """Raised when a summary or ask prompt cannot be built."""


def assert_summary_no_forbidden_language(authored: list[str]) -> None:
    """Raise `OpenAskError` if any authored summary string is banned.

    Scans only the strings this module authors — section labels, lane
    status phrasings, and the caveats/next-steps carried from the agent
    context — never opaque data a user controls (a source filename, a
    package id, a track id). A future edit that smuggles a semantic claim
    into the authored prose fails loudly.
    """
    for text in authored:
        lowered = text.lower()
        for phrase in SUMMARY_FORBIDDEN_PHRASES:
            if phrase in lowered:
                raise OpenAskError(
                    "authored evidence-summary prose contains forbidden "
                    f"semantic phrase {phrase!r}: {text!r}"
                )


def _lane_status_line(lane: str, present: bool, count: int) -> str:
    """A non-semantic one-line status for a core evidence lane."""
    if not present:
        return f"  {lane}: unavailable (never generated)"
    if count == 0:
        return f"  {lane}: present but empty"
    return f"  {lane}: present ({count} record(s))"


def render_evidence_summary(context: dict[str, Any]) -> str:
    """Render an agent-context dict as an evidence-only text summary.

    Pure and non-semantic: it only re-presents facts already in
    `context`. The caveats and next-steps are carried over verbatim from
    the agent context (already authored and guarded there).
    """
    pkg = context["package"]
    source = pkg["source"]
    validation = context["validation"]
    event_counts: dict[str, int] = context.get("event_counts", {})
    evidence: dict[str, Any] = context.get("evidence", {})
    unavailable: list[str] = context.get("unavailable_evidence", [])

    lines: list[str] = []
    lines.append(_RULE)
    lines.append(f"CLULatent evidence summary - {pkg['package_id']}")
    lines.append(_RULE)

    # --- Package facts ---
    lines.append("Package facts:")
    lines.append(f"  package id:        {pkg['package_id']}")
    lines.append(f"  duration:          {pkg['duration_ms']} ms")
    sha = source.get("sha256") or ""
    sha_short = f"{sha[:12]}..." if sha else "(none)"
    lines.append(f"  source:            {source.get('filename')} ({sha_short})")
    lines.append(f"  status:            {pkg.get('status')}")
    lines.append(f"  clulatent version: {pkg.get('clulatent_version')}")

    # --- Validation and lock ---
    lines.append("Validation and lock:")
    lines.append(
        "  validation:        "
        + ("valid" if validation["valid"] else "INVALID")
        + f" ({validation['error_count']} error(s), "
        + f"{validation['warning_count']} warning(s))"
    )
    lock_status = context.get("lock", {}).get("status")
    lines.append(f"  lock:              {lock_status}")

    # --- Tracks present ---
    summary = context.get("track_summary", {})
    present_names = {t["name"] for t in context.get("tracks", [])}
    lines.append(
        f"Tracks present ({summary.get('track_count', 0)} track(s); "
        f"{summary.get('total_events', 0)} event record(s)):"
    )
    for lane in CORE_LANES:
        is_present = lane in present_names
        lines.append(_lane_status_line(lane, is_present, event_counts.get(lane, 0)))
    for name in summary.get("unknown_tracks", []):
        lines.append(f"  {name}: present (unknown track, {event_counts.get(name, 0)} record(s))")

    # --- Evidence (candidate only) ---
    lines.append("Evidence (candidate only):")
    any_candidate = False
    vc = evidence.get("visual_change_candidates")
    if vc is not None:
        any_candidate = True
        lines.append(
            f"  visual-change candidates: {vc['count']} "
            f"(strength: {vc.get('strength_counts', {})})"
        )
    cr = evidence.get("changed_region_candidates")
    if cr is not None:
        any_candidate = True
        lines.append(
            f"  changed-region candidates: {cr['count']} "
            f"(strength: {cr.get('strength_counts', {})})"
        )
    if not any_candidate:
        lines.append("  no candidate evidence lanes present")

    # --- Major evidence windows ---
    lines.append("Major evidence windows:")
    windows = _evidence_windows(context)
    if windows:
        lines.extend(windows)
    else:
        lines.append("  no evidence windows to report")

    # --- Evidence bundle IDs ---
    bundles = evidence.get("evidence_bundles", [])
    lines.append(f"Evidence bundle IDs ({len(bundles)}):")
    if bundles:
        for bundle in bundles:
            lines.append(
                f"  {bundle['id']} [{bundle['t_start_ms']}-{bundle['t_end_ms']} ms]"
            )
    else:
        lines.append("  none")

    # --- Agent review IDs ---
    reviews = evidence.get("agent_reviews", [])
    lines.append(f"Agent review IDs ({len(reviews)}):")
    if reviews:
        for review in reviews:
            lines.append(
                f"  {review['id']} (reviews evidence bundle "
                f"{review.get('evidence_bundle_id')}): "
                f"review status {review.get('review_status')}"
            )
    else:
        lines.append("  none")

    # --- Unavailable / missing evidence ---
    lines.append("Unavailable / missing evidence:")
    if unavailable:
        for lane in unavailable:
            lines.append(f"  {lane}")
    else:
        lines.append("  none - all core lanes present")

    # --- Caveats (carried verbatim from the agent context) ---
    lines.append("Caveats:")
    for caveat in context.get("caveats", []):
        lines.append(f"  - {caveat}")

    # --- Safe next steps (carried verbatim from the agent context) ---
    lines.append("Safe next steps:")
    for step in context.get("next_steps", []):
        lines.append(f"  - {step}")

    lines.append(_RULE)

    # Guard the authored prose (labels + carried caveats/next-steps),
    # never the interpolated opaque data.
    authored = list(_SUMMARY_LABELS)
    authored.extend(context.get("caveats", []))
    authored.extend(context.get("next_steps", []))
    assert_summary_no_forbidden_language([s for s in authored if isinstance(s, str)])

    return "\n".join(lines) + "\n"


def _evidence_windows(context: dict[str, Any]) -> list[str]:
    """Non-semantic "where the evidence is" lines.

    Prefers evidence bundles (each is an explicit time range with an
    evidence-count total); falls back to the whole-timeline coverage when
    no bundle exists. Never names *what* is in a window — only that a
    timeline window contains N evidence records.
    """
    evidence = context.get("evidence", {})
    bundles = evidence.get("evidence_bundles", [])
    if bundles:
        out: list[str] = []
        for bundle in bundles:
            counts = bundle.get("evidence_counts", {}) or {}
            total = sum(v for v in counts.values() if isinstance(v, int))
            out.append(
                f"  [{bundle['t_start_ms']}-{bundle['t_end_ms']} ms] "
                f"evidence bundle {bundle['id']}: "
                f"timeline window contains {total} evidence records"
            )
        return out

    coverage = context.get("time_coverage", {})
    total_events = context.get("track_summary", {}).get("total_events", 0)
    tracks_with_events = coverage.get("tracks_with_events", [])
    if total_events and tracks_with_events:
        return [
            f"  [{coverage.get('start_ms', 0)}-{coverage.get('end_ms', 0)} ms] "
            f"timeline window contains {total_events} evidence records "
            f"across tracks {sorted(tracks_with_events)}"
        ]
    return []


def build_evidence_summary(package_path: Path | str) -> str:
    """Open `package_path` and render its evidence-only summary.

    Read-only: delegates to `agent_context.build_agent_context`, which
    opens the package through the reader and mutates nothing.
    """
    try:
        context = build_agent_context(package_path)
    except agent_context_mod.AgentContextError as exc:
        raise OpenAskError(str(exc)) from exc
    return render_evidence_summary(context)


# ---------------------------------------------------------------------------
# Ask prompt kit
# ---------------------------------------------------------------------------

# The instruction body of the ask prompt. It deliberately names the
# forbidden concepts (people, objects, actions, intent, identity, scene
# meaning) so it can prohibit them to the downstream agent, and so is NOT
# scanned by the summary forbidden-language guard.
_ASK_PROMPT_INSTRUCTIONS = """\
You are given CLULatent context for a single media package. CLULatent is a
human-inspectable, machine-readable evidence format. It records candidate
evidence about a video -- numeric, non-semantic signals and the records
built from them -- never conclusions about what the video depicts.

Your task: answer the user's question about this package using ONLY the
evidence in the context provided below.

Grounding rules:
- Summarize only from evidence that is present in the context.
- Cite specific event IDs, timestamps in ms, track names, evidence bundle
  IDs, and agent review IDs for every statement you make.
- Report unknowns explicitly. If the context does not contain something,
  say it is unsupported by available evidence rather than guessing.
- Call out missing or unavailable evidence lanes when they are relevant.

Do NOT infer or assert any of the following -- the evidence cannot support
them and this format never encodes them:
- people, faces, or identity
- objects, or that any object moved or entered a scene
- actions or events, or that anything happened
- speech content, or that anyone said anything
- intent, purpose, or scene meaning

Visual-change and changed-region records are numeric pixel-difference
candidates, not detections. An evidence bundle only groups already-existing
records for a time range; it does not establish what happened. Agent review
status reports evidence support and gaps only; it confirms nothing.

Structure your answer in three clearly separated sections:
1. Package facts -- verbatim, non-interpretive facts from the context
   (package id, duration, validation, lock, tracks, event counts).
2. Candidate evidence -- the candidate records and time windows, each with
   its IDs and timestamps, described as candidates only.
3. Unsupported claims -- anything the user asked about that the evidence
   cannot support; mark it "requires human review" or "unsupported by
   available evidence".

If the context below is not enough to answer safely, ask for the raw
.clulatent package or its full agent-context export (clulatent
agent-context export PACKAGE --output ...) before answering.\
"""


def render_ask_prompt(context: dict[str, Any]) -> str:
    """Render a ready-to-paste ask prompt for a downstream agent.

    Embeds a compact, non-semantic facts block so the prompt is usable on
    its own, plus a placeholder where the full agent-context export can be
    pasted. Introduces no new information beyond `context`.
    """
    pkg = context["package"]
    evidence = context.get("evidence", {})
    bundles = evidence.get("evidence_bundles", [])
    reviews = evidence.get("agent_reviews", [])
    summary = context.get("track_summary", {})

    lines: list[str] = []
    lines.append("CLULATENT ASK PROMPT")
    lines.append("=" * 20)
    lines.append("")
    lines.append(_ASK_PROMPT_INSTRUCTIONS)
    lines.append("")
    lines.append(f"--- BEGIN CLULATENT CONTEXT (package: {pkg['package_id']}) ---")
    lines.append(f"package id: {pkg['package_id']}")
    lines.append(f"duration_ms: {pkg['duration_ms']}")
    lines.append(
        "validation: "
        + ("valid" if context["validation"]["valid"] else "INVALID")
    )
    lines.append(f"lock: {context.get('lock', {}).get('status')}")
    lines.append(
        f"tracks: {summary.get('track_count', 0)}; "
        f"total event records: {summary.get('total_events', 0)}"
    )
    lines.append(
        "evidence bundle ids: "
        + (", ".join(b["id"] for b in bundles) if bundles else "none")
    )
    lines.append(
        "agent review ids: "
        + (", ".join(r["id"] for r in reviews) if reviews else "none")
    )
    unavailable = context.get("unavailable_evidence", [])
    lines.append(
        "unavailable evidence lanes: "
        + (", ".join(unavailable) if unavailable else "none")
    )
    lines.append("")
    lines.append(
        "Paste the full agent-context JSON or Markdown (clulatent "
        "agent-context export ...)"
    )
    lines.append("below this line before sending the prompt:")
    lines.append("")
    lines.append("<<< paste clulatent agent-context here >>>")
    lines.append("")
    lines.append("--- END CLULATENT CONTEXT ---")

    return "\n".join(lines) + "\n"


def build_ask_prompt(package_path: Path | str) -> str:
    """Open `package_path` and render a ready-to-paste ask prompt.

    Read-only: delegates to `agent_context.build_agent_context`, which
    opens the package through the reader and mutates nothing. Does not
    call any external agent -- it only generates prompt text.
    """
    try:
        context = build_agent_context(package_path)
    except agent_context_mod.AgentContextError as exc:
        raise OpenAskError(str(exc)) from exc
    return render_ask_prompt(context)
