"""Phase 3.21: the V1 "ask bundle" / agent handoff kit.

Phase 3.19 made a `.clulatent` package *buildable* from a video. Phase
3.20 made it *explainable* (an evidence-only summary and a safe ask
prompt). Phase 3.21 makes it *askable*:

> "I have a `.clulatent` package. I want to ask a question about the
> video, and CLULatent should produce a safe, evidence-grounded ask
> bundle that Codex/CLU/another agent can use without spelunking through
> package internals."

`build_ask_bundle(package_path, question)` renders a ready-to-paste agent
handoff bundle: the user's question, the package's facts, what candidate
evidence exists (and what is missing), a relevant evidence window when the
question names a timestamp, and the rules an agent must follow to answer
strictly from evidence.

Boundaries (inherited from the layers this module builds on):

- All package facts come from `agent_context.build_agent_context`, which
  opens the package via `package_reader.open_package`. Timestamp windows
  are resolved with `PackageReader.query_time`. This module never walks
  the folder tree, never assumes a filename, and never opens a track file
  directly.
- Strictly read-only: it never mutates the package, never writes a
  receipt, never touches lock state, and never runs FFmpeg/Pillow/an ML
  model/a network/an LLM call. It adds no evidence and performs no
  semantic interpretation. It never *answers* the question -- it only
  produces the safe packet that lets an agent answer from evidence.
- The bundle's authored prose emits **candidate evidence only**: no
  object, person, face, action, scene, speech, intent, identity, or
  scene-meaning claim. Its authored labels/caveats are scanned by
  `assert_bundle_no_forbidden_language`. Two things are deliberately
  exempt from that scan, because neither is CLULatent asserting anything:
  (1) the user's question, quoted verbatim as user input, and (2) the
  safe-answering / forbidden-claim instruction blocks, which *name* the
  forbidden concepts precisely in order to prohibit them to the agent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import agent_context as agent_context_mod
from . import package_reader as package_reader_mod
from .agent_context import CORE_LANES, build_agent_context
from .v1_open_ask import SUMMARY_FORBIDDEN_PHRASES

__all__ = [
    "AskBundleError",
    "ASK_BUNDLE_SCHEMA_ID",
    "ASK_BUNDLE_SCHEMA_VERSION",
    "ASK_FORBIDDEN_PHRASES",
    "DEFAULT_WINDOW_PAD_MS",
    "INTENT_PACKAGE_OVERVIEW",
    "INTENT_TIMELINE_OVERVIEW",
    "INTENT_EVIDENCE_NEAR_TIME",
    "INTENT_UNSUPPORTED",
    "INTENT_GENERAL",
    "SAFE_ANSWERING_RULES",
    "FORBIDDEN_CLAIM_RULES",
    "SUGGESTED_ANSWER_FORMAT",
    "classify_question",
    "parse_time_query",
    "build_ask_bundle",
    "render_ask_bundle",
    "assert_bundle_no_forbidden_language",
]

ASK_BUNDLE_SCHEMA_ID = "clulatent.ask_bundle.v0"
ASK_BUNDLE_SCHEMA_VERSION = "0.1.0"

# The ask bundle must never emit a semantic claim in its authored prose.
# We reuse the summary layer's banned phrases and add "scene meaning".
ASK_FORBIDDEN_PHRASES: tuple[str, ...] = tuple(
    dict.fromkeys(SUMMARY_FORBIDDEN_PHRASES + ("scene meaning",))
)

# When a question names a single instant, we surround it by this padding
# (each side, in ms) to form the "relevant evidence window".
DEFAULT_WINDOW_PAD_MS = 2000

# --- broad, non-semantic intent labels -------------------------------------
INTENT_PACKAGE_OVERVIEW = "package_overview"
INTENT_TIMELINE_OVERVIEW = "timeline_overview"
INTENT_EVIDENCE_NEAR_TIME = "evidence_near_time"
INTENT_UNSUPPORTED = "unsupported_or_unknown"
INTENT_GENERAL = "general_question"

# Evidence-safe framing for each intent. These strings deliberately name
# the forbidden concepts in order to prohibit them, so they are authored
# *instruction* text and are exempt from the forbidden-language scan.
_INTENT_FRAMING: dict[str, str] = {
    INTENT_PACKAGE_OVERVIEW: (
        "Summarize the package's available evidence records, counts, and "
        "caveats. Do not infer scene meaning."
    ),
    INTENT_TIMELINE_OVERVIEW: (
        "Describe the distribution of evidence records across the timeline "
        "using timestamps and track names only. Do not infer what occurred."
    ),
    INTENT_EVIDENCE_NEAR_TIME: (
        "List the candidate evidence records whose time range overlaps the "
        "requested window, each with its id, track, and timestamps only. "
        "Do not infer what occurred."
    ),
    INTENT_UNSUPPORTED: (
        "This question asks for information the package cannot support "
        "(people, objects, actions, intent, identity, or scene meaning). "
        "Summarize available evidence records and caveats, and mark the "
        "semantic parts as unsupported by this package / requires human "
        "review. Do not infer scene meaning."
    ),
    INTENT_GENERAL: (
        "Answer only from available evidence records and caveats. If the "
        "question requires semantic interpretation, mark it unsupported by "
        "this package."
    ),
}

# Keyword sets used by the deliberately small classifier.
_UNSUPPORTED_KEYWORDS: tuple[str, ...] = (
    "what happened",
    "what happens",
    "what is happening",
    "what's happening",
    "what occurred",
    "who is",
    "who's",
    "who was",
    "identify",
    "recognize",
    "what does it mean",
    "what it means",
    "meaning",
    "why did",
    "why does",
    "intent",
    "purpose",
    "what object",
    "what is shown",
    "what is depicted",
)
_OVERVIEW_KEYWORDS: tuple[str, ...] = (
    "overview",
    "summary",
    "summarize",
    "what is this",
    "what's this",
    "tell me about",
    "describe the package",
    "describe this",
    "about the video",
    "about the package",
)
_TIMELINE_KEYWORDS: tuple[str, ...] = (
    "timeline",
    "sequence",
    "order of",
    "throughout",
    "over time",
    "when does",
    "when do",
)

# Authored safe-answering / forbidden-claim / answer-format instruction
# blocks. All three deliberately name forbidden concepts in order to
# prohibit them, so they are exempt from the forbidden-language scan.
SAFE_ANSWERING_RULES: tuple[str, ...] = (
    "Answer ONLY from the evidence in this bundle. Treat the package as the "
    "sole source of truth.",
    "Cite specific event ids, timestamps in ms, track names, evidence "
    "bundle ids, and agent review ids for every statement you make.",
    "Describe visual-change and changed-region records as numeric "
    "pixel-difference candidates, not detections.",
    "Treat an evidence bundle as a grouping of existing records for a time "
    "range; it does not establish what happened.",
    "Treat an agent review as a report of evidence support and gaps only; "
    "it confirms nothing.",
    "If the bundle does not contain something, say it is unsupported by "
    "this package or unknown from current evidence -- never guess.",
    "Everything here is candidate evidence that requires human review "
    "before it is treated as canonical.",
)

FORBIDDEN_CLAIM_RULES: tuple[str, ...] = (
    "Do NOT infer or assert people, faces, or identity.",
    "Do NOT infer or assert objects, or that any object moved or entered a "
    "scene.",
    "Do NOT infer or assert actions or events, or that anything happened.",
    "Do NOT infer or assert speech content, or that anyone said anything.",
    "Do NOT infer or assert intent, purpose, or scene meaning.",
    "Do NOT claim the video shows anything beyond a direct package or "
    "evidence fact.",
)

SUGGESTED_ANSWER_FORMAT: tuple[str, ...] = (
    "1. Package facts -- verbatim, non-interpretive facts (package id, "
    "duration, validation, lock, tracks, event counts).",
    "2. Candidate evidence -- the candidate records and time windows "
    "relevant to the question, each with its ids and timestamps, described "
    "as candidates only.",
    "3. Unsupported / unknown -- anything the question asked for that the "
    "evidence cannot support; mark it \"unsupported by this package\" or "
    "\"requires human review\".",
)

# Section headers and package-fact labels. These are the only authored
# strings the bundle guard scans (plus the caveats carried over from the
# agent context, already guarded there) -- never opaque, user-controlled
# data (the question, a package id, a source filename, a track id, or an
# event type), so a video named "identity.mp4" or a question containing
# "what did the person say" never trips the guard.
_AUTHORED_LABELS: tuple[str, ...] = (
    "CLULatent Ask Bundle",
    "User Question",
    "Question Framing",
    "Package Facts",
    "package id",
    "duration",
    "validation",
    "lock",
    "clulatent version",
    "source",
    "Track Summary",
    "Evidence Available",
    "candidate evidence",
    "visual-change candidate",
    "changed-region candidate",
    "no candidate evidence lanes present",
    "Relevant Evidence Window",
    "no timestamp detected in the question",
    "no evidence records overlap the requested window",
    "timeline window contains",
    "evidence records",
    "Evidence Bundles",
    "Agent Reviews",
    "reviews evidence bundle",
    "review status",
    "none",
    "Unavailable / Missing Evidence",
    "all core lanes present",
    "Caveats",
    "Safe Answering Instructions",
    "Forbidden Claims",
    "Suggested Answer Format",
    "Source Context References",
)


class AskBundleError(ValueError):
    """Raised when an ask bundle cannot be built."""


def assert_bundle_no_forbidden_language(authored: list[str]) -> None:
    """Raise `AskBundleError` if any authored bundle string is banned.

    Scans only the strings this module authors -- section labels and the
    caveats carried from the agent context -- never opaque data (the user
    question, a source filename, a package id, a track id, an event type)
    and never the safe-answering / forbidden-claim instruction blocks
    (which name the forbidden concepts on purpose in order to prohibit
    them).
    """
    for text in authored:
        lowered = text.lower()
        for phrase in ASK_FORBIDDEN_PHRASES:
            if phrase in lowered:
                raise AskBundleError(
                    "authored ask-bundle prose contains forbidden semantic "
                    f"phrase {phrase!r}: {text!r}"
                )


# ---------------------------------------------------------------------------
# Question handling: broad intent + tiny timestamp parser (no NLP)
# ---------------------------------------------------------------------------

# A single time token: mm:ss / hh:mm:ss (colon form), or a number with an
# explicit `ms` / `s` unit. `ms` is listed before `s` so "4000ms" is not
# mis-read as "4000" seconds. Bare integers are intentionally NOT matched
# ("4 objects" must not look like a timestamp).
_TIME_TOKEN = r"(?:\d{1,2}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?\s*ms|\d+(?:\.\d+)?\s*s)"
_RANGE_RE = re.compile(rf"({_TIME_TOKEN})\s*-\s*({_TIME_TOKEN})", re.IGNORECASE)
_SINGLE_RE = re.compile(rf"({_TIME_TOKEN})", re.IGNORECASE)


def _parse_time_token(token: str) -> int | None:
    """Parse a single time token to milliseconds, or `None` if malformed."""
    tok = token.strip().lower().replace(" ", "")
    try:
        if ":" in tok:
            parts = [int(p) for p in tok.split(":")]
            if len(parts) == 2:
                minutes, seconds = parts
                return (minutes * 60 + seconds) * 1000
            if len(parts) == 3:
                hours, minutes, seconds = parts
                return ((hours * 3600) + (minutes * 60) + seconds) * 1000
            return None
        if tok.endswith("ms"):
            return int(float(tok[:-2]))
        if tok.endswith("s"):
            return int(float(tok[:-1]) * 1000)
    except ValueError:
        return None
    return None


def parse_time_query(
    question: str,
    *,
    duration_ms: int | None = None,
    pad_ms: int = DEFAULT_WINDOW_PAD_MS,
) -> tuple[int, int] | None:
    """Extract a `[start_ms, end_ms]` window from a question, or `None`.

    Recognizes an explicit range (`0:04-0:10`) first, then a single
    timestamp (`00:04`, `4s`, `4000ms`), which is padded by `pad_ms` on
    each side. The window is clamped to `[0, duration_ms]` when a duration
    is provided. Deliberately small: no bare integers, no natural-language
    time phrases.
    """
    range_match = _RANGE_RE.search(question)
    if range_match:
        start = _parse_time_token(range_match.group(1))
        end = _parse_time_token(range_match.group(2))
        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            return _clamp_window(start, end, duration_ms)

    single_match = _SINGLE_RE.search(question)
    if single_match:
        instant = _parse_time_token(single_match.group(1))
        if instant is not None:
            return _clamp_window(instant - pad_ms, instant + pad_ms, duration_ms)

    return None


def _clamp_window(
    start_ms: int, end_ms: int, duration_ms: int | None
) -> tuple[int, int]:
    start = max(0, start_ms)
    end = max(start, end_ms)
    if duration_ms is not None:
        end = min(end, duration_ms)
        start = min(start, end)
    return (start, end)


def classify_question(question: str, *, has_time_query: bool | None = None) -> str:
    """Classify a question into one broad, non-semantic intent label.

    This never answers or semantically interprets the question -- it only
    picks a framing so the bundle can tell the agent how to stay
    evidence-safe. A timestamp wins first (`evidence_near_time`); then a
    semantic "what happened / who / meaning" question is flagged
    `unsupported_or_unknown`; then overview / timeline keywords; otherwise
    `general_question`.
    """
    if has_time_query is None:
        has_time_query = parse_time_query(question) is not None
    if has_time_query:
        return INTENT_EVIDENCE_NEAR_TIME

    lowered = question.lower()
    if any(kw in lowered for kw in _UNSUPPORTED_KEYWORDS):
        return INTENT_UNSUPPORTED
    if any(kw in lowered for kw in _OVERVIEW_KEYWORDS):
        return INTENT_PACKAGE_OVERVIEW
    if any(kw in lowered for kw in _TIMELINE_KEYWORDS):
        return INTENT_TIMELINE_OVERVIEW
    return INTENT_GENERAL


# ---------------------------------------------------------------------------
# Window resolution (read-only, via the reader)
# ---------------------------------------------------------------------------


def _query_window(package_path: Path | str, window: tuple[int, int]) -> list[dict[str, Any]]:
    """Return non-semantic descriptors of events overlapping `window`.

    Opens the package read-only through `package_reader.open_package` and
    uses `PackageReader.query_time`. Each descriptor carries only opaque
    record fields (id, track, type, timestamps) -- never any interpretation.
    """
    start_ms, end_ms = window
    try:
        reader = package_reader_mod.open_package(package_path)
    except package_reader_mod.PackageReaderError as exc:
        raise AskBundleError(str(exc)) from exc
    events = reader.query_time(start_ms, end_ms)
    return [
        {
            "id": ev.id,
            "track": ev.track_name,
            "type": ev.type,
            "t_start_ms": ev.t_start_ms,
            "t_end_ms": ev.t_end_ms,
        }
        for ev in events
    ]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _bundle_data(
    context: dict[str, Any],
    question: str,
    intent: str,
    window: tuple[int, int] | None,
    nearby: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the structured bundle payload (shared by markdown + JSON)."""
    pkg = context["package"]
    evidence = context.get("evidence", {})
    bundles = evidence.get("evidence_bundles", [])
    reviews = evidence.get("agent_reviews", [])
    return {
        "schema_id": ASK_BUNDLE_SCHEMA_ID,
        "schema_version": ASK_BUNDLE_SCHEMA_VERSION,
        "user_question": question,
        "intent": intent,
        "interpreted_framing": _INTENT_FRAMING[intent],
        "package": pkg,
        "validation": context.get("validation", {}),
        "lock": context.get("lock", {}),
        "track_summary": context.get("track_summary", {}),
        "tracks": context.get("tracks", []),
        "event_counts": context.get("event_counts", {}),
        "evidence": evidence,
        "evidence_bundle_ids": [b["id"] for b in bundles],
        "agent_review_ids": [r["id"] for r in reviews],
        "relevant_window": (
            {"start_ms": window[0], "end_ms": window[1], "events": nearby}
            if window is not None
            else None
        ),
        "unavailable_evidence": context.get("unavailable_evidence", []),
        "caveats": list(context.get("caveats", [])),
        "safe_answering_rules": list(SAFE_ANSWERING_RULES),
        "forbidden_claim_rules": list(FORBIDDEN_CLAIM_RULES),
        "suggested_answer_format": list(SUGGESTED_ANSWER_FORMAT),
        "source_context_references": _source_references(pkg),
    }


def _source_references(pkg: dict[str, Any]) -> list[str]:
    path = pkg.get("path", "PACKAGE")
    return [
        f"Package id: {pkg.get('package_id')}",
        f"Full agent context (JSON): clulatent agent-context export {path} --output ctx.json",
        f"Readable agent context (Markdown): clulatent agent-context export {path} --output ctx.md --format markdown",
        f"Evidence-only summary: clulatent agent-context summarize {path}",
    ]


def render_ask_bundle(
    context: dict[str, Any],
    question: str,
    intent: str,
    window: tuple[int, int] | None,
    nearby: list[dict[str, Any]],
) -> str:
    """Render the ask bundle as Markdown.

    Pure and non-semantic: it re-presents facts already in `context` plus
    the (opaque) records `nearby`, wrapped in authored safe-answering
    instructions. Guards the authored prose (labels + carried caveats),
    never the quoted question, the instruction blocks, or opaque data.
    """
    data = _bundle_data(context, question, intent, window, nearby)
    pkg = data["package"]
    source = pkg.get("source", {})
    validation = data["validation"]
    summary = data["track_summary"]
    event_counts = data["event_counts"]
    evidence = data["evidence"]

    lines: list[str] = []
    lines.append("# CLULatent Ask Bundle")
    lines.append("")
    lines.append(
        "_This is not an answer. It is a safe, evidence-grounded packet that "
        "lets an agent answer a question from package evidence without "
        "reading package internals._"
    )
    lines.append("")

    # --- User Question (verbatim, quoted; NOT guarded) ---
    lines.append("## User Question")
    lines.append("")
    lines.append("> " + question.replace("\n", "\n> "))
    lines.append("")

    # --- Question Framing (safe framing; NOT guarded) ---
    lines.append("## Question Framing")
    lines.append("")
    lines.append(f"- classification (broad, non-semantic): `{intent}`")
    lines.append(f"- evidence-safe framing: {data['interpreted_framing']}")
    lines.append("")

    # --- Package Facts ---
    sha = source.get("sha256") or ""
    sha_short = f"{sha[:12]}..." if sha else "(none)"
    lines.append("## Package Facts")
    lines.append("")
    lines.append(f"- package id: {pkg.get('package_id')}")
    lines.append(f"- duration: {pkg.get('duration_ms')} ms")
    lines.append(f"- source: {source.get('filename')} ({sha_short})")
    lines.append(f"- status: {pkg.get('status')}")
    lines.append(f"- clulatent version: {pkg.get('clulatent_version')}")
    lines.append(
        "- validation: "
        + ("valid" if validation.get("valid") else "INVALID")
        + f" ({validation.get('error_count', 0)} error(s), "
        + f"{validation.get('warning_count', 0)} warning(s))"
    )
    lines.append(f"- lock: {data['lock'].get('status')}")
    lines.append("")

    # --- Track Summary ---
    present_names = {t["name"] for t in data["tracks"]}
    lines.append("## Track Summary")
    lines.append("")
    lines.append(
        f"- {summary.get('track_count', 0)} track(s); "
        f"{summary.get('total_events', 0)} event record(s)"
    )
    for lane in CORE_LANES:
        if lane in present_names:
            count = event_counts.get(lane, 0)
            status = f"present ({count} record(s))" if count else "present but empty"
        else:
            status = "unavailable (never generated)"
        lines.append(f"  - {lane}: {status}")
    for name in summary.get("unknown_tracks", []):
        lines.append(f"  - {name}: present (unknown track, {event_counts.get(name, 0)} record(s))")
    lines.append("")

    # --- Evidence Available (candidate counts only) ---
    lines.append("## Evidence Available")
    lines.append("")
    any_candidate = False
    vc = evidence.get("visual_change_candidates")
    if vc is not None:
        any_candidate = True
        lines.append(
            f"- visual-change candidates: {vc['count']} "
            f"(strength: {vc.get('strength_counts', {})})"
        )
    cr = evidence.get("changed_region_candidates")
    if cr is not None:
        any_candidate = True
        lines.append(
            f"- changed-region candidates: {cr['count']} "
            f"(strength: {cr.get('strength_counts', {})})"
        )
    if not any_candidate:
        lines.append("- no candidate evidence lanes present")
    lines.append("")

    # --- Relevant Evidence Window ---
    lines.append("## Relevant Evidence Window")
    lines.append("")
    if window is None:
        lines.append("- no timestamp detected in the question")
    else:
        start_ms, end_ms = window
        lines.append(
            f"- requested window: [{start_ms}-{end_ms} ms] "
            f"(timeline window contains {len(nearby)} evidence records)"
        )
        if nearby:
            for ev in nearby:
                lines.append(
                    f"  - [{ev['t_start_ms']}-{ev['t_end_ms']} ms] "
                    f"{ev['track']} / {ev['id']} (type: {ev['type']})"
                )
        else:
            lines.append("  - no evidence records overlap the requested window")
    lines.append("")

    # --- Evidence Bundles ---
    lines.append("## Evidence Bundles")
    lines.append("")
    if data["evidence_bundle_ids"]:
        for bundle in evidence.get("evidence_bundles", []):
            lines.append(
                f"- {bundle['id']} [{bundle['t_start_ms']}-{bundle['t_end_ms']} ms]"
            )
    else:
        lines.append("- none")
    lines.append("")

    # --- Agent Reviews ---
    lines.append("## Agent Reviews")
    lines.append("")
    if data["agent_review_ids"]:
        for review in evidence.get("agent_reviews", []):
            lines.append(
                f"- {review['id']} (reviews evidence bundle "
                f"{review.get('evidence_bundle_id')}): "
                f"review status {review.get('review_status')}"
            )
    else:
        lines.append("- none")
    lines.append("")

    # --- Unavailable / Missing Evidence ---
    lines.append("## Unavailable / Missing Evidence")
    lines.append("")
    unavailable = data["unavailable_evidence"]
    if unavailable:
        for lane in unavailable:
            lines.append(f"- {lane}")
    else:
        lines.append("- none - all core lanes present")
    lines.append("")

    # --- Caveats (carried verbatim from the agent context) ---
    lines.append("## Caveats")
    lines.append("")
    for caveat in data["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")

    # --- Safe Answering Instructions (authored; NOT guarded) ---
    lines.append("## Safe Answering Instructions")
    lines.append("")
    for rule in SAFE_ANSWERING_RULES:
        lines.append(f"- {rule}")
    lines.append("")

    # --- Forbidden Claims (authored; NOT guarded) ---
    lines.append("## Forbidden Claims")
    lines.append("")
    for rule in FORBIDDEN_CLAIM_RULES:
        lines.append(f"- {rule}")
    lines.append("")

    # --- Suggested Answer Format (authored; NOT guarded) ---
    lines.append("## Suggested Answer Format")
    lines.append("")
    for item in SUGGESTED_ANSWER_FORMAT:
        lines.append(f"- {item}")
    lines.append("")

    # --- Source Context References ---
    lines.append("## Source Context References")
    lines.append("")
    for ref in data["source_context_references"]:
        lines.append(f"- {ref}")
    lines.append("")

    # Guard only the authored labels + carried caveats. The question, the
    # instruction blocks, and every interpolated opaque field are exempt.
    authored = list(_AUTHORED_LABELS)
    authored.extend(data["caveats"])
    assert_bundle_no_forbidden_language([s for s in authored if isinstance(s, str)])

    return "\n".join(lines).rstrip("\n") + "\n"


def build_ask_bundle(
    package_path: Path | str,
    question: str,
    *,
    output_format: str = "markdown",
    window_pad_ms: int = DEFAULT_WINDOW_PAD_MS,
) -> str:
    """Open `package_path` and render a ready-to-paste ask bundle.

    Read-only: package facts come from `agent_context.build_agent_context`
    (which opens the package through the reader), and a timestamp window,
    when present, is resolved with `PackageReader.query_time`. Mutates
    nothing, writes no receipt, calls no model, and never answers the
    question -- it only produces the safe handoff packet.
    """
    if output_format not in ("markdown", "json"):
        raise AskBundleError(
            f"unknown output_format {output_format!r}; expected 'markdown' or 'json'"
        )
    try:
        context = build_agent_context(package_path)
    except agent_context_mod.AgentContextError as exc:
        raise AskBundleError(str(exc)) from exc

    duration_ms = context["package"].get("duration_ms")
    window = parse_time_query(question, duration_ms=duration_ms, pad_ms=window_pad_ms)
    intent = classify_question(question, has_time_query=window is not None)
    nearby = _query_window(package_path, window) if window is not None else []

    if output_format == "json":
        # Still runs the guard by rendering markdown's authored prose check.
        data = _bundle_data(context, question, intent, window, nearby)
        assert_bundle_no_forbidden_language(
            [s for s in list(_AUTHORED_LABELS) + data["caveats"] if isinstance(s, str)]
        )
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return render_ask_bundle(context, question, intent, window, nearby)
