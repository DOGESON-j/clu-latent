"""Phase 3.17: read-only retrieval primitives for the agent review lane.

Mirrors `evidence_bundle_retrieval.py`'s read-only conventions: every
function here only reads already-computed `agent_review_event` records
back from `tracks/agent_review_events.jsonl` (written by
`agent_review_writer.run_agent_review`) -- nothing here computes a new
review, calls a model, or mutates a package in any way.

Core rule (unchanged from `agent_review.py`):

    Agent review is review of evidence, not invention of truth.

What this module is NOT:

  - No package mutation. Nothing here writes a track, a manifest, a
    receipt, an index, or a lock file.
  - No network access, no model call.
  - No dense-data dump: retrieval returns only the small, already-
    stored review records.

An *absent* agent review track is not an error -- it yields an empty
result, mirroring every other Phase 3.x retrieval module's convention
for an absent optional track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tracks as tracks_mod
from .manifest import Manifest
from .security.jsonl import JsonlLimitError
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

AGENT_REVIEW_TRACK_NAME = "agent_review_events"

# Bound on the number of agent review records returned by any single
# retrieval so a pathological package cannot produce an unbounded
# result, mirroring `evidence_bundle_retrieval.DEFAULT_MAX_EVIDENCE_BUNDLE_EVENTS`.
DEFAULT_MAX_AGENT_REVIEW_EVENTS = 5000


class AgentReviewRetrievalError(ValueError):
    """Raised when agent review evidence cannot be safely read from a package.

    Covers: a missing/non-directory package, an unreadable manifest, a
    manifest-declared agent review track file missing from disk, and a
    track file that exceeds size limits or contains a malformed/invalid
    record. Never raised for an *absent* agent review track -- that is
    not an error, see `load_agent_review_events`.
    """


@dataclass
class AgentReviewRetrievalResult:
    """A bounded set of agent review records plus any non-fatal warnings."""

    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_package_root(package_root: Path | str) -> Path:
    package_root = Path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise AgentReviewRetrievalError(f"Package not found or not a directory: {package_root}")
    if not (package_root / "manifest.json").exists():
        raise AgentReviewRetrievalError(f"manifest.json not found in package: {package_root}")
    return package_root


def load_agent_review_events(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Load every record in the `agent_review_events` track as ordered plain dicts.

    Returns an empty list if the package declares no agent review track
    -- absence is not an error. Raises `AgentReviewRetrievalError` if
    the package itself doesn't exist, the manifest can't be read, or a
    track *is* declared but its file is missing/oversized/malformed.
    Records are returned sorted by `t_start_ms` (then by id) so time-
    ordered retrieval is deterministic. Never mutates the package.
    """
    package_root = _resolve_package_root(package_root)

    try:
        manifest = Manifest.from_json_file(package_root / "manifest.json", limits=limits)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise AgentReviewRetrievalError(f"manifest.json could not be read: {exc}") from exc

    descriptor = next((t for t in manifest.tracks if t.name == AGENT_REVIEW_TRACK_NAME), None)
    if descriptor is None:
        return []

    try:
        track_path = resolve_in_package(
            package_root, descriptor.file, field_name="tracks[agent_review_events].file"
        )
    except PathSecurityError as exc:
        raise AgentReviewRetrievalError(str(exc)) from exc

    if not track_path.exists():
        raise AgentReviewRetrievalError(
            f"agent review track file listed in manifest is missing: {descriptor.file}"
        )

    try:
        envelopes = tracks_mod.read_track_file(track_path, limits=limits)
    except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
        raise AgentReviewRetrievalError(f"agent review track could not be read cleanly: {exc}") from exc

    events = [envelope.model_dump(mode="json") for envelope in envelopes]
    events.sort(key=lambda e: (e.get("t_start_ms", 0), str(e.get("id", ""))))
    return events


def get_agent_review_by_id(
    package_root: Path | str,
    review_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the one agent review record with `id == review_id`, or `None`.

    Never raises for a missing id -- only for the same package/track-
    level failures `load_agent_review_events` already raises for.
    """
    for event in load_agent_review_events(package_root, limits=limits):
        if event.get("id") == review_id:
            return event
    return None


def query_agent_reviews_by_bundle(
    package_root: Path | str,
    bundle_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every agent review whose `payload.evidence_bundle_id == bundle_id`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no review links to this bundle, or the track is
    absent) is not an error.
    """
    events = load_agent_review_events(package_root, limits=limits)
    matches = []
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, dict) and payload.get("evidence_bundle_id") == bundle_id:
            matches.append(event)
    return matches


def _overlaps(event: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    return t_start <= end_ms and t_end >= start_ms


def query_agent_reviews_by_time_range(
    package_root: Path | str,
    start_ms: int,
    end_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every agent review whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no review overlaps the range, or the track is
    absent) is not an error. An inverted range (`end_ms < start_ms`) is
    a caller mistake and raises `AgentReviewRetrievalError`.
    """
    if end_ms < start_ms:
        raise AgentReviewRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
    events = load_agent_review_events(package_root, limits=limits)
    return [event for event in events if _overlaps(event, start_ms, end_ms)]


def summarize_agent_reviews(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Produce a small, shallow index of a package's agent review evidence.

    Reports only counts and already-stored fields -- review count, a
    per-`review_status` count, escalation count (reviews with
    `recommended_next_step == "human_review"`), unsupported-claim count
    (total across all reviews), and missing-evidence count (total
    category mentions across all reviews). Never generates prose, never
    infers meaning. A package with no agent review track produces a
    zeroed-out, still-valid summary (exit path, not an error). Bounded:
    at most `DEFAULT_MAX_AGENT_REVIEW_EVENTS` records are considered.
    """
    events = load_agent_review_events(package_root, limits=limits)[:DEFAULT_MAX_AGENT_REVIEW_EVENTS]

    status_counts: dict[str, int] = {}
    escalation_count = 0
    unsupported_claim_count = 0
    missing_evidence_count = 0

    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        status = payload.get("review_status")
        if isinstance(status, str):
            status_counts[status] = status_counts.get(status, 0) + 1
        if payload.get("recommended_next_step") == "human_review":
            escalation_count += 1
        unsupported_claims = payload.get("unsupported_claims")
        if isinstance(unsupported_claims, list):
            unsupported_claim_count += len(unsupported_claims)
        evidence_missing = payload.get("evidence_missing")
        if isinstance(evidence_missing, list):
            missing_evidence_count += len(evidence_missing)

    return {
        "review_count": len(events),
        "status_counts": status_counts,
        "escalation_count": escalation_count,
        "unsupported_claim_count": unsupported_claim_count,
        "missing_evidence_count": missing_evidence_count,
    }
