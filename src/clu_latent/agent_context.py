"""Phase 3.19: the agent-context export — one document another agent can open.

Phase 3.18 gave `.clulatent` a read-only reader so a caller can inspect a
package without knowing its folder layout. This module builds on that
reader to produce a single, stable, plain-JSON document that another
agent — CLULatent, Codex, anything — can read to understand **what
evidence a package contains**, **what it is not allowed to conclude from
that evidence**, and **what it can safely do next**.

Boundaries:

- It opens the package through `package_reader.open_package` and reads
  everything from the reader. It never walks the folder tree itself and
  never assumes a filename.
- It is strictly read-only: it never mutates the package, never writes a
  receipt, never touches lock state, and never runs FFmpeg/Pillow/an ML
  model/a network call.
- It emits **candidate evidence only**. It performs no semantic
  interpretation: it makes no object, person, face, text, action, scene,
  speech, or intent claim, and it never states what the video depicts or
  what any evidence means. The most load-bearing field it produces is
  `caveats`, which says exactly that to the downstream agent.

The output envelope is versioned (`schema_id = "clulatent.agent_context.v0"`,
`schema_version = "0.1.0"`) so a future revision is a new schema, not a
silent shape change. The JSON is the contract; the Markdown render
(`render_markdown`) is a convenience view of the same document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import package_reader as package_reader_mod
from .agent_review_retrieval import AGENT_REVIEW_TRACK_NAME
from .changed_region_retrieval import CHANGED_REGION_TRACK_NAME
from .constants import TOOL_NAME, TOOL_VERSION
from .evidence_bundle_retrieval import EVIDENCE_BUNDLE_TRACK_NAME
from .security.limits import DEFAULT_LIMITS, Limits
from .visual_change_retrieval import VISUAL_CHANGE_TRACK_NAME

__all__ = [
    "AgentContextError",
    "AGENT_CONTEXT_SCHEMA_ID",
    "AGENT_CONTEXT_SCHEMA_VERSION",
    "CONTEXT_CAVEATS",
    "FORBIDDEN_CONTEXT_PHRASES",
    "build_agent_context",
    "render_markdown",
    "context_to_json",
    "assert_no_forbidden_language",
]

AGENT_CONTEXT_SCHEMA_ID = "clulatent.agent_context.v0"
AGENT_CONTEXT_SCHEMA_VERSION = "0.1.0"

# The bound on how many events the compact timeline carries. A downstream
# agent uses the timeline to decide *where* to look, not as a complete
# record — the full events stay in the package and are read via the
# reader. Truncation is always flagged so nothing looks complete when it
# is not.
MAX_TIMELINE_EVENTS = 500

# The core canonical lanes a V1 build can produce. Used only to report
# which of them are *absent* from a given package (`unavailable_evidence`)
# so an agent knows what evidence was never generated versus generated-
# and-empty. Derived from the same track-name constants the writers use.
CORE_LANES: tuple[str, ...] = (
    "keyframes",
    "audio_events",
    "speech_events",
    VISUAL_CHANGE_TRACK_NAME,
    CHANGED_REGION_TRACK_NAME,
    EVIDENCE_BUNDLE_TRACK_NAME,
    AGENT_REVIEW_TRACK_NAME,
)

# The load-bearing section. Authored entirely here (never derived from
# untrusted package data) and deliberately worded to avoid every banned
# semantic phrase — the forbidden-language guard runs over exactly these
# strings plus the other authored prose.
CONTEXT_CAVEATS: tuple[str, ...] = (
    "This document describes candidate evidence only; it carries no semantic interpretation.",
    "No object, person, face, action, scene, speech, or purpose claim is made or implied.",
    "Visual-change and changed-region entries are numeric pixel-difference candidates, not detections.",
    "Evidence bundles collect existing package records for a time range; they do not establish what happened.",
    "Agent review status reports evidence support and gaps only; it does not confirm any event.",
    "Everything here needs human review before it is treated as canonical.",
)

# Phrases a downstream agent must never see this module emit. The guard
# (`assert_no_forbidden_language`) scans only the strings this module
# *authors* — caveats, next-steps, and section notes — not opaque data a
# user controls (a source filename, a package id), so a video literally
# named "truth.mp4" never trips it.
FORBIDDEN_CONTEXT_PHRASES: tuple[str, ...] = (
    "person appeared",
    "object moved",
    "car entered",
    "face changed",
    "someone said",
    "the video shows",
    "this means",
    "confirmed event",
    "intent",
    "identity",
    "truth",
)


class AgentContextError(ValueError):
    """Raised when an agent-context document cannot be built or fails its guard."""


def _validation_section(reader: package_reader_mod.PackageReader) -> dict[str, Any]:
    report = reader.validate()
    return {
        "valid": bool(report.valid),
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
    }


def _tracks_and_counts(
    reader: package_reader_mod.PackageReader,
) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    tracks: list[dict[str, Any]] = []
    event_counts: dict[str, int] = {}
    unknown: list[str] = []
    for handle in reader.list_tracks():
        tracks.append(
            {"name": handle.name, "record_count": handle.record_count, "known": handle.known}
        )
        event_counts[handle.name] = handle.record_count
        if not handle.known:
            unknown.append(handle.name)
    return tracks, event_counts, unknown


def _timeline_and_coverage(
    reader: package_reader_mod.PackageReader,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any], int]:
    """Build a compact, bounded, chronological timeline and time coverage.

    Reads every declared track via the reader (no filename knowledge),
    flattens events to `{t_start_ms, t_end_ms, track, id, type}`, sorts
    deterministically, and caps the list at `MAX_TIMELINE_EVENTS`.
    """
    entries: list[dict[str, Any]] = []
    tracks_with_events: list[str] = []
    total_events = 0
    for name in reader.track_names():
        events = reader.load_track(name)
        if events:
            tracks_with_events.append(name)
        total_events += len(events)
        for event in events:
            entries.append(
                {
                    "t_start_ms": event.t_start_ms,
                    "t_end_ms": event.t_end_ms,
                    "track": event.track_name,
                    "id": event.id,
                    "type": event.type,
                }
            )
    entries.sort(key=lambda e: (e["t_start_ms"], e["track"], e["id"]))

    truncated = len(entries) > MAX_TIMELINE_EVENTS
    timeline = entries[:MAX_TIMELINE_EVENTS]

    coverage = {
        "start_ms": 0,
        "end_ms": reader.duration_ms,
        "duration_ms": reader.duration_ms,
        "tracks_with_events": sorted(tracks_with_events),
    }
    return timeline, truncated, coverage, total_events


def _evidence_section(reader: package_reader_mod.PackageReader) -> dict[str, Any]:
    """Non-semantic summaries of the evidence-carrying lanes, if present."""
    evidence: dict[str, Any] = {}

    def _strength_counts(track_name: str) -> dict[str, Any] | None:
        if not reader.has_track(track_name):
            return None
        counts: dict[str, int] = {}
        events = reader.load_track(track_name)
        for event in events:
            strength = event.payload.get("strength")
            if isinstance(strength, str):
                counts[strength] = counts.get(strength, 0) + 1
        return {"count": len(events), "strength_counts": counts}

    vc = _strength_counts(VISUAL_CHANGE_TRACK_NAME)
    if vc is not None:
        evidence["visual_change_candidates"] = vc
    cr = _strength_counts(CHANGED_REGION_TRACK_NAME)
    if cr is not None:
        evidence["changed_region_candidates"] = cr

    if reader.has_track(EVIDENCE_BUNDLE_TRACK_NAME):
        bundles: list[dict[str, Any]] = []
        for event in reader.load_track(EVIDENCE_BUNDLE_TRACK_NAME):
            payload = event.payload
            bundles.append(
                {
                    "id": event.id,
                    "t_start_ms": event.t_start_ms,
                    "t_end_ms": event.t_end_ms,
                    "evidence_counts": payload.get("evidence_counts", {}),
                    "coverage": payload.get("coverage", {}),
                    "missing_evidence": payload.get("missing_evidence", []),
                }
            )
        evidence["evidence_bundles"] = bundles

    if reader.has_track(AGENT_REVIEW_TRACK_NAME):
        reviews: list[dict[str, Any]] = []
        for event in reader.load_track(AGENT_REVIEW_TRACK_NAME):
            payload = event.payload
            reviews.append(
                {
                    "id": event.id,
                    "evidence_bundle_id": payload.get("evidence_bundle_id"),
                    "review_status": payload.get("review_status"),
                }
            )
        evidence["agent_reviews"] = reviews

    return evidence


def _next_steps(
    reader: package_reader_mod.PackageReader,
    unavailable: list[str],
    validation: dict[str, Any],
) -> list[str]:
    """Safe, non-semantic suggested actions. Authored prose (guarded)."""
    steps: list[str] = []
    if not validation["valid"]:
        steps.append("Package does not validate; inspect validator errors before using it.")
    if VISUAL_CHANGE_TRACK_NAME in unavailable:
        steps.append("Run visual-change analysis to add pixel-difference candidates.")
    if CHANGED_REGION_TRACK_NAME in unavailable and VISUAL_CHANGE_TRACK_NAME not in unavailable:
        steps.append("Run changed-region analysis to localize visual-change candidates.")
    if EVIDENCE_BUNDLE_TRACK_NAME in unavailable:
        steps.append("Build an evidence bundle to collect existing evidence for a time range.")
    if reader.has_track(EVIDENCE_BUNDLE_TRACK_NAME):
        steps.append("Have a human review each evidence bundle before treating it as canonical.")
    steps.append("Use event_counts and time_coverage to decide where a human should look.")
    return steps


def build_agent_context(
    package_path: Path | str,
    *,
    strict: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
) -> dict[str, Any]:
    """Open `package_path` through the reader and build its agent context.

    Returns a plain-JSON dict (dicts/lists/strings/numbers/booleans/None
    only). Read-only: nothing here mutates the package or writes a
    receipt. Raises `AgentContextError` if the package cannot be opened
    or the authored prose fails the forbidden-language guard.
    """
    try:
        reader = package_reader_mod.open_package(package_path, strict=strict, limits=limits)
    except package_reader_mod.PackageReaderError as exc:
        raise AgentContextError(f"could not open package: {exc}") from exc

    manifest = reader.manifest
    validation = _validation_section(reader)
    tracks, event_counts, unknown_tracks = _tracks_and_counts(reader)
    timeline, timeline_truncated, time_coverage, total_events = _timeline_and_coverage(reader)
    present = set(reader.track_names())
    unavailable = [lane for lane in CORE_LANES if lane not in present]

    context: dict[str, Any] = {
        "schema_id": AGENT_CONTEXT_SCHEMA_ID,
        "schema_version": AGENT_CONTEXT_SCHEMA_VERSION,
        "generated_by": f"{tool_name} {tool_version}",
        "package": {
            "package_id": reader.package_id,
            "path": str(reader.path),
            "status": reader.status,
            "created_at": reader.created_at,
            "clulatent_version": manifest.clulatent_version,
            "duration_ms": reader.duration_ms,
            "has_audio": reader.has_audio,
            "source": {
                "filename": manifest.source.filename,
                "sha256": manifest.source.sha256,
                "duration_ms": manifest.source.duration_ms,
            },
        },
        "validation": validation,
        "lock": reader.lock_status(),
        "tracks": tracks,
        "track_summary": {
            "track_count": len(tracks),
            "known_track_count": len(tracks) - len(unknown_tracks),
            "unknown_tracks": unknown_tracks,
            "total_events": total_events,
        },
        "event_counts": event_counts,
        "time_coverage": time_coverage,
        "receipts": [handle.file for handle in reader.receipts() if handle.exists],
        "evidence": _evidence_section(reader),
        "timeline": timeline,
        "timeline_truncated": timeline_truncated,
        "unavailable_evidence": unavailable,
        "caveats": list(CONTEXT_CAVEATS),
        "next_steps": _next_steps(reader, unavailable, validation),
    }

    assert_no_forbidden_language(context)
    return context


def _iter_authored_strings(context: dict[str, Any]) -> list[str]:
    """The strings this module authors — the only text the guard scans."""
    authored: list[str] = list(context.get("caveats", []))
    authored.extend(context.get("next_steps", []))
    authored.append(context.get("generated_by", ""))
    return [s for s in authored if isinstance(s, str)]


def assert_no_forbidden_language(context: dict[str, Any]) -> None:
    """Raise `AgentContextError` if authored prose contains a banned phrase.

    Scans only the strings this module authors (caveats, next-steps,
    generated_by) — never opaque, user-controlled data like a source
    filename or package id — so legitimate input never trips the guard,
    but a future edit that smuggles a semantic claim into the authored
    prose fails loudly.
    """
    for text in _iter_authored_strings(context):
        lowered = text.lower()
        for phrase in FORBIDDEN_CONTEXT_PHRASES:
            if phrase in lowered:
                raise AgentContextError(
                    f"authored agent-context prose contains forbidden semantic phrase {phrase!r}: {text!r}"
                )


def render_markdown(context: dict[str, Any]) -> str:
    """Render an agent-context dict as a readable Markdown brief.

    A convenience view of the same document; the JSON remains the
    contract. No new information is introduced here.
    """
    pkg = context["package"]
    source = pkg["source"]
    validation = context["validation"]
    lines: list[str] = []
    lines.append(f"# Agent context — {pkg['package_id']}")
    lines.append("")
    lines.append(f"- schema: `{context['schema_id']}` (v{context['schema_version']})")
    lines.append(f"- generated by: {context['generated_by']}")
    lines.append(f"- source: `{source['filename']}` ({source['sha256'][:12]}...)")
    lines.append(f"- duration_ms: {pkg['duration_ms']}  |  has_audio: {pkg['has_audio']}")
    lines.append(
        f"- validation: {'valid' if validation['valid'] else 'INVALID'} "
        f"({validation['error_count']} error(s), {validation['warning_count']} warning(s))"
    )
    lines.append(f"- lock: {context['lock'].get('status')}")
    lines.append("")

    lines.append("## Tracks")
    lines.append("")
    lines.append("| track | records | known |")
    lines.append("|---|---:|---|")
    for track in context["tracks"]:
        lines.append(f"| {track['name']} | {track['record_count']} | {track['known']} |")
    lines.append("")

    evidence = context["evidence"]
    if evidence:
        lines.append("## Evidence (candidate only)")
        lines.append("")
        for lane in ("visual_change_candidates", "changed_region_candidates"):
            if lane in evidence:
                info = evidence[lane]
                lines.append(f"- {lane}: {info['count']} (strength: {info['strength_counts']})")
        for bundle in evidence.get("evidence_bundles", []):
            lines.append(
                f"- evidence bundle `{bundle['id']}` "
                f"[{bundle['t_start_ms']}–{bundle['t_end_ms']}ms]: counts {bundle['evidence_counts']}"
            )
        for review in evidence.get("agent_reviews", []):
            lines.append(
                f"- agent review `{review['id']}` of `{review['evidence_bundle_id']}`: "
                f"status `{review['review_status']}`"
            )
        lines.append("")

    if context["unavailable_evidence"]:
        lines.append("## Unavailable evidence")
        lines.append("")
        for lane in context["unavailable_evidence"]:
            lines.append(f"- {lane}")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    for caveat in context["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")

    lines.append("## Suggested next steps")
    lines.append("")
    for step in context["next_steps"]:
        lines.append(f"- {step}")
    lines.append("")

    return "\n".join(lines)


def context_to_json(context: dict[str, Any]) -> str:
    """Serialize an agent-context dict to stable, indented JSON text."""
    return json.dumps(context, indent=2, sort_keys=False) + "\n"
