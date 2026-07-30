"""Phase 2.1: read-only reviewed-state resolver.

Computes, at read time, the *effective* review state of every source
event in a package by folding `tracks/review_events.jsonl` (Phase 2.0)
over the package's other canonical tracks. This module never writes
anything: not a new track, not a mutation of an existing record, not a
"reviewed_truth" file. It only reads already-canonical data and returns
plain Python objects.

Core principle, unchanged from Phase 1.9/2.0: review events are
**additive evidence**. A source event's own record, in its own track, is
never touched by anything here. `supersedes_event_ids` chains are
followed to figure out which review judgments are still "active", but
the superseded review records themselves are never deleted or rewritten
— `resolve_review_states` only *reads* `review_events.jsonl` and reports
what it currently implies.

Conflicts are surfaced, not hidden or auto-resolved: `has_conflict` on
`ResolvedReviewState` is a flag for the caller to act on, not something
this module tries to adjudicate. See `docs/PHASE_2_REVIEWED_STATE_RESOLVER.md`
for the full design rationale.

This module deliberately does not duplicate `review.validate_review_track`.
It assumes `validate_package` is the source of truth for *shape*
correctness, and is defensive on top of that only so a malformed or
hand-edited package can never crash resolution — malformed review
records are skipped (with a warning), never raised.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from .constants import REVIEW_EVENTS_TRACK_NAME
from .event import EventEnvelope
from .manifest import Manifest
from .review import ASSERTABLE_REVIEW_STATES, JUDGMENT_EVENT_TYPES, REVIEW_EVENT_TYPES
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

_CORRECTING_TYPES = frozenset({"review_correction", "review_override"})
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class ResolvedReviewState:
    """The current, read-time-computed review state of one source event.

    `event_id` and `source_event_id` are always equal — both are kept so
    callers can use whichever name reads more naturally at the call
    site. Nothing in this dataclass is ever written back into a package;
    it exists only in memory for the duration of a resolve call.
    """

    event_id: str
    review_state: str
    source_event_id: str
    latest_review_event_id: str | None = None
    review_event_ids: list[str] = field(default_factory=list)
    corrected_payload: dict[str, Any] | None = None
    reviewer_id: str | None = None
    reviewed_at: str | None = None
    reason: str | None = None
    certainty: float | None = None
    is_reviewed: bool = False
    is_approved: bool = False
    is_rejected: bool = False
    is_corrected: bool = False
    is_superseded: bool = False
    has_conflict: bool = False


def _extract_id_list(value: Any) -> list[str]:
    """Defensively pull a list of string ids out of a payload field.

    Never raises: a non-list value yields `[]`, and non-string elements
    within a list are silently dropped. `validate_review_track` is
    responsible for flagging this as a shape error at validation time —
    this resolver only needs to never crash on it.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _parse_reviewed_at(value: Any) -> datetime | None:
    """Best-effort ISO-8601 parse; returns None (never raises) on failure."""
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _asserted_state_for_event(event: EventEnvelope, warnings: list[str]) -> str | None:
    """The single review_state this one judgment event asserts, if any.

    Returns None (and appends a warning) for a judgment event whose
    payload is too malformed to trust, and for every non-judgment type
    (`human_note`, `review_session_summary`, and any unrecognized
    `type`) — per Phase 2.1 rule 4, those never change state.
    """
    event_type = event.type
    if event_type == "review_approval":
        return "approved"
    if event_type == "review_rejection":
        return "rejected"
    if event_type == "review_correction":
        return "corrected"
    if event_type == "review_override":
        state = event.payload.get("review_state")
        if isinstance(state, str) and state in ("corrected", "rejected"):
            return state
        warnings.append(
            f"review_events[{event.id}]: review_override has a missing/invalid "
            "review_state, ignored by the resolver"
        )
        return None
    if event_type == "review_status":
        state = event.payload.get("review_state")
        if isinstance(state, str) and state in ASSERTABLE_REVIEW_STATES:
            return state
        warnings.append(
            f"review_events[{event.id}]: review_status has a missing/invalid "
            "review_state, ignored by the resolver"
        )
        return None
    return None


def _resolve_one(
    source_event_id: str,
    group: list[tuple[int, EventEnvelope, str, datetime | None]],
) -> ResolvedReviewState:
    if not group:
        return ResolvedReviewState(
            event_id=source_event_id,
            source_event_id=source_event_id,
            review_state="unreviewed",
        )

    ordered = sorted(group, key=lambda item: (item[3] or _EPOCH, item[0]))
    review_event_ids = [event.id for _, event, _, _ in ordered]

    superseded_ids: set[str] = set()
    for _, event, _, _ in ordered:
        superseded_ids.update(_extract_id_list(event.payload.get("supersedes_event_ids")))

    unsuperseded = [item for item in ordered if item[1].id not in superseded_ids]
    if not unsuperseded:
        # Defensive: e.g. a supersession cycle among this source event's
        # own judgments — never let resolution end up with nothing to
        # report, fall back to the full ordered history instead.
        unsuperseded = ordered

    distinct_states = {item[2] for item in unsuperseded}
    unsuperseded_correction_count = sum(
        1 for item in unsuperseded if item[1].type in _CORRECTING_TYPES
    )
    has_conflict = len(distinct_states) > 1 or unsuperseded_correction_count > 1

    _, latest_event, latest_state, _ = ordered[-1]

    corrected_payload: dict[str, Any] | None = None
    if latest_event.type in _CORRECTING_TYPES:
        candidate = latest_event.payload.get("corrected_payload")
        if isinstance(candidate, dict):
            corrected_payload = candidate

    reviewer_id = latest_event.payload.get("reviewer_id")
    if not isinstance(reviewer_id, str):
        reviewer_id = None
    reviewed_at = latest_event.payload.get("reviewed_at")
    if not isinstance(reviewed_at, str):
        reviewed_at = None
    reason = latest_event.payload.get("reason")
    if not isinstance(reason, str):
        reason = None

    return ResolvedReviewState(
        event_id=source_event_id,
        source_event_id=source_event_id,
        review_state=latest_state,
        latest_review_event_id=latest_event.id,
        review_event_ids=review_event_ids,
        corrected_payload=corrected_payload,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        reason=reason,
        certainty=latest_event.confidence,
        is_reviewed=latest_state != "unreviewed",
        is_approved=latest_state == "approved",
        is_rejected=latest_state == "rejected",
        is_corrected=latest_state == "corrected",
        is_superseded=bool(superseded_ids),
        has_conflict=has_conflict,
    )


def resolve_review_states(
    source_event_ids: Iterable[str],
    review_events: list[EventEnvelope],
) -> tuple[dict[str, ResolvedReviewState], list[str]]:
    """Resolve the current review state of every id in `source_event_ids`.

    Deterministic and defensive over an already-parsed list of review
    `EventEnvelope` records — never touches disk. Ids in
    `source_event_ids` that are never referenced by any review event
    resolve to `review_state="unreviewed"`. A review event referencing
    an id *not* in `source_event_ids` is ignored (with a warning), never
    treated as an error.

    Ordering within a group of judgments applying to the same source
    event: `reviewed_at` (parsed as ISO-8601) when present, falling back
    to the review event's position in `review_events` (its track order)
    when `reviewed_at` is missing or unparseable — see the module
    docstring and `docs/PHASE_2_REVIEWED_STATE_RESOLVER.md` for why this
    is "small and boring" rather than a full causal/vector-clock model.
    """
    warnings: list[str] = []
    ordered_ids = list(dict.fromkeys(source_event_ids))
    known_ids = set(ordered_ids)

    applicable: dict[str, list[tuple[int, EventEnvelope, str, datetime | None]]] = {}

    for idx, event in enumerate(review_events):
        if not isinstance(event, EventEnvelope):
            continue
        if event.type not in REVIEW_EVENT_TYPES or event.type not in JUDGMENT_EVENT_TYPES:
            # Unrecognized types, human_note, and review_session_summary
            # never change any source event's review state (rule 4).
            continue

        state = _asserted_state_for_event(event, warnings)
        if state is None:
            continue

        reviewed_dt = _parse_reviewed_at(event.payload.get("reviewed_at"))
        for source_id in _extract_id_list(event.payload.get("source_event_ids")):
            if source_id not in known_ids:
                warnings.append(
                    f"review_events[{event.id}]: source_event_ids references "
                    f"{source_id!r}, which is not a known source event id in this "
                    "package — ignored"
                )
                continue
            applicable.setdefault(source_id, []).append((idx, event, state, reviewed_dt))

    states = {
        source_id: _resolve_one(source_id, applicable.get(source_id, []))
        for source_id in ordered_ids
    }
    return states, warnings


def resolve_review_state_for_event(
    event_id: str, review_events: list[EventEnvelope]
) -> tuple[ResolvedReviewState, list[str]]:
    """Convenience wrapper around `resolve_review_states` for a single id."""
    states, warnings = resolve_review_states([event_id], review_events)
    return states[event_id], warnings


def load_review_events(
    package_path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[EventEnvelope], list[str]]:
    """Load `tracks/review_events.jsonl` from a package, if present.

    Returns `(events, warnings)`. Returns `([], [])` if the package has
    no track named `"review_events"` in its manifest — packages without
    review events are fully supported. This is a tolerant reader (like
    `reindex.py`, not `validate.py`): a malformed line or a record that
    fails the shared event envelope schema is skipped and reported as a
    warning, never raised, so a malformed review track can never crash
    resolution.
    """
    package_path = Path(package_path)
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        return [], [f"manifest.json not found in package: {package_path}"]

    try:
        manifest = Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError, json.JSONDecodeError) as exc:
        return [], [f"manifest.json could not be read: {exc}"]

    track = next((t for t in manifest.tracks if t.name == REVIEW_EVENTS_TRACK_NAME), None)
    if track is None:
        return [], []

    try:
        track_path = resolve_in_package(
            package_path, track.file, field_name=f"tracks[{track.name}].file"
        )
    except PathSecurityError as exc:
        return [], [str(exc)]
    if not track_path.exists():
        return [], [f"track file listed in manifest is missing: {track.file}"]

    events: list[EventEnvelope] = []
    warnings: list[str] = []
    if track_path.stat().st_size > 0:
        try:
            for record in iter_jsonl_bounded(track_path, limits=limits):
                if record.error is not None:
                    warnings.append(f"{track.file}:{record.lineno}: {record.error}, line skipped")
                    continue
                try:
                    events.append(EventEnvelope.model_validate(record.data))
                except ValidationError as exc:
                    warnings.append(
                        f"{track.file}:{record.lineno}: does not match the shared event "
                        f"envelope, line skipped ({exc})"
                    )
        except JsonlLimitError as exc:
            warnings.append(f"{track.file}: exceeds size limits: {exc}")

    return events, warnings


def resolve_package_review_states(
    package_path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[dict[str, ResolvedReviewState], list[str]]:
    """Resolve reviewed state for every source event in a package.

    Reads every canonical track other than `review_events` to build the
    universe of source event ids, loads `review_events.jsonl` (if
    present), and resolves. Read-only: nothing on disk is modified.
    """
    package_path = Path(package_path)
    warnings: list[str] = []
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        return {}, [f"manifest.json not found in package: {package_path}"]

    try:
        manifest = Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError, json.JSONDecodeError) as exc:
        return {}, [f"manifest.json could not be read: {exc}"]

    source_event_ids: list[str] = []
    for track in manifest.tracks:
        if track.name == REVIEW_EVENTS_TRACK_NAME:
            continue
        try:
            track_path = resolve_in_package(
                package_path, track.file, field_name=f"tracks[{track.name}].file"
            )
        except PathSecurityError as exc:
            warnings.append(str(exc))
            continue
        if not track_path.exists():
            warnings.append(f"track file listed in manifest is missing: {track.file}")
            continue
        if track_path.stat().st_size == 0:
            continue
        try:
            for record in iter_jsonl_bounded(track_path, limits=limits):
                if record.error is not None:
                    warnings.append(f"{track.file}:{record.lineno}: {record.error}, line skipped")
                    continue
                event_id = record.data.get("id") if isinstance(record.data, dict) else None
                if isinstance(event_id, str):
                    source_event_ids.append(event_id)
        except JsonlLimitError as exc:
            warnings.append(f"{track.file}: exceeds size limits: {exc}")

    review_events, review_warnings = load_review_events(package_path, limits=limits)
    warnings.extend(review_warnings)

    states, resolve_warnings = resolve_review_states(source_event_ids, review_events)
    warnings.extend(resolve_warnings)
    return states, warnings


def summarize_review_states(states: dict[str, ResolvedReviewState]) -> dict[str, int]:
    """Roll `states` up into the small set of counts a CLI/report needs.

    `superseded` and `conflicts` are counted from the `is_superseded` /
    `has_conflict` flags, not from `review_state` — `review_state` never
    holds the literal value `"superseded"` (see the module docstring:
    that is a per-review-event notion, exposed here as a flag on the
    owning source event's resolved state, not as its final state).
    """
    summary = {
        "total": len(states),
        "unreviewed": 0,
        "needs_review": 0,
        "uncertain": 0,
        "reviewed": 0,
        "approved": 0,
        "rejected": 0,
        "corrected": 0,
        "superseded": 0,
        "conflicts": 0,
    }
    for state in states.values():
        if state.review_state in summary:
            summary[state.review_state] += 1
        if state.is_superseded:
            summary["superseded"] += 1
        if state.has_conflict:
            summary["conflicts"] += 1
    return summary
