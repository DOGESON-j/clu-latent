"""Phase 3.16: read-only retrieval primitives for the changed-region evidence lane.

Mirrors `visual_change_retrieval.py`'s read-only conventions exactly:
every function here only reads already-computed `changed_region_candidate`
records back from `tracks/changed_region_candidates.jsonl` (written by
`changed_region_writer.analyze_changed_regions`) -- nothing here computes
a new metric, runs Pillow, or mutates a package in any way.

Core rule (unchanged from `changed_region.py`):

    Changed-region evidence, not semantic interpretation.

What this module is NOT:

  - No image decoding, no Pillow dependency, no pixel comparison.
  - No package mutation. Nothing here writes a track, a manifest, a
    receipt, an index, or a lock file.
  - No network access.
  - No dense-data dump: retrieval is bounded and returns only the
    small, already-stored records, never raw image bytes, masks, or
    pixel maps.

An *absent* changed-region track is not an error -- it yields an empty
result, mirroring `visual_change_retrieval.load_visual_change_events`'s
convention for an absent `visual_change_candidates` track.
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

CHANGED_REGION_TRACK_NAME = "changed_region_candidates"

# Bound on the number of changed-region records returned by any single
# retrieval so a pathological package cannot produce an unbounded
# result, mirroring `visual_change_retrieval.DEFAULT_MAX_VISUAL_CHANGE_EVENTS`.
DEFAULT_MAX_CHANGED_REGION_EVENTS = 5000


class ChangedRegionRetrievalError(ValueError):
    """Raised when changed-region evidence cannot be safely read from a package.

    Covers: a missing/non-directory package, an unreadable manifest, a
    manifest-declared changed-region track file missing from disk, a
    track file that exceeds size limits or contains a malformed/invalid
    record, and a record whose stored image path is unsafe (escapes the
    package or fails symlink containment). Never raised for an *absent*
    changed-region track -- that is not an error, see
    `load_changed_region_events`.
    """


@dataclass
class ChangedRegionRetrievalResult:
    """A bounded set of changed-region records plus any non-fatal warnings."""

    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_package_root(package_root: Path | str) -> Path:
    package_root = Path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise ChangedRegionRetrievalError(
            f"Package not found or not a directory: {package_root}"
        )
    if not (package_root / "manifest.json").exists():
        raise ChangedRegionRetrievalError(f"manifest.json not found in package: {package_root}")
    return package_root


def _changed_region_image_paths(event: dict[str, Any]) -> list[str]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return []
    paths = []
    for key in ("source_image_path", "target_image_path"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    return paths


def load_changed_region_events(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Load every record in the `changed_region_candidates` track as ordered plain dicts.

    Returns an empty list if the package declares no changed-region
    track -- absence is not an error. Raises `ChangedRegionRetrievalError`
    if the package itself doesn't exist, the manifest can't be read, a
    track *is* declared but its file is missing/oversized/malformed, or
    a record carries an unsafe image path. Records are returned sorted
    by `t_start_ms` (then by id) so time-ordered retrieval is
    deterministic. Never mutates the package.
    """
    package_root = _resolve_package_root(package_root)

    try:
        manifest = Manifest.from_json_file(package_root / "manifest.json", limits=limits)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise ChangedRegionRetrievalError(f"manifest.json could not be read: {exc}") from exc

    descriptor = next(
        (t for t in manifest.tracks if t.name == CHANGED_REGION_TRACK_NAME), None
    )
    if descriptor is None:
        return []

    try:
        track_path = resolve_in_package(
            package_root, descriptor.file, field_name="tracks[changed_region_candidates].file"
        )
    except PathSecurityError as exc:
        raise ChangedRegionRetrievalError(str(exc)) from exc

    if not track_path.exists():
        raise ChangedRegionRetrievalError(
            f"changed-region track file listed in manifest is missing: {descriptor.file}"
        )

    try:
        envelopes = tracks_mod.read_track_file(track_path, limits=limits)
    except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
        raise ChangedRegionRetrievalError(
            f"changed-region track could not be read cleanly: {exc}"
        ) from exc

    events = [envelope.model_dump(mode="json") for envelope in envelopes]

    # Reject any record whose stored image path is unsafe *before*
    # handing the evidence back to a caller, mirroring
    # `visual_change_retrieval.load_visual_change_events`.
    for event in events:
        for image_path in _changed_region_image_paths(event):
            try:
                resolve_in_package(
                    package_root, image_path, field_name="changed_region.payload.image_path"
                )
            except PathSecurityError as exc:
                raise ChangedRegionRetrievalError(
                    f"changed-region event {event.get('id')!r} has an unsafe image path: {exc}"
                ) from exc

    events.sort(key=lambda e: (e.get("t_start_ms", 0), str(e.get("id", ""))))
    return events


def get_changed_region_event_by_id(
    package_root: Path | str,
    event_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the one changed-region record with `id == event_id`, or `None`.

    Never raises for a missing id -- only for the same package/track-
    level failures `load_changed_region_events` already raises for.
    """
    for event in load_changed_region_events(package_root, limits=limits):
        if event.get("id") == event_id:
            return event
    return None


def _overlaps(event: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    return t_start <= end_ms and t_end >= start_ms


def query_changed_regions_by_time_range(
    package_root: Path | str,
    start_ms: int,
    end_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every changed-region record whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no record overlaps the range, or the track is absent)
    is not an error. An inverted range (`end_ms < start_ms`) is a
    caller mistake and raises `ChangedRegionRetrievalError`.
    """
    if end_ms < start_ms:
        raise ChangedRegionRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
    events = load_changed_region_events(package_root, limits=limits)
    return [event for event in events if _overlaps(event, start_ms, end_ms)]


def query_changed_regions_by_visual_change_id(
    package_root: Path | str,
    visual_change_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every changed-region record linked to `visual_change_id`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no record links to this visual change candidate id,
    or the track is absent) is not an error -- this mirrors every other
    retrieval function here, and lets a caller distinguish "no changed
    region was ever computed for this visual change" from a hard
    package-level failure.
    """
    events = load_changed_region_events(package_root, limits=limits)
    return [
        event
        for event in events
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("visual_change_id") == visual_change_id
    ]


def summarize_changed_regions(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Produce a small, shallow index of a package's changed-region evidence.

    Reports only counts and already-stored fields -- event count,
    first/last event id and timestamp, a count of records per
    `strength` bucket (low/medium/high), and a count of records per
    `change_scope` bucket (localized/distributed/global/unknown). Never
    generates prose, never infers meaning. A package with no
    changed-region track produces a zeroed-out, still-valid summary
    (exit path, not an error). Bounded: at most
    `DEFAULT_MAX_CHANGED_REGION_EVENTS` records are considered.
    """
    events = load_changed_region_events(package_root, limits=limits)[
        :DEFAULT_MAX_CHANGED_REGION_EVENTS
    ]

    strength_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    change_scope_counts: dict[str, int] = {
        "localized": 0,
        "distributed": 0,
        "global": 0,
        "unknown": 0,
    }
    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        strength = payload.get("strength")
        if strength in strength_counts:
            strength_counts[strength] += 1
        change_scope = payload.get("change_scope")
        if change_scope in change_scope_counts:
            change_scope_counts[change_scope] += 1

    first_event = events[0] if events else None
    last_event = events[-1] if events else None

    return {
        "event_count": len(events),
        "first_event_id": first_event.get("id") if first_event else None,
        "first_timestamp_ms": first_event.get("t_start_ms") if first_event else None,
        "last_event_id": last_event.get("id") if last_event else None,
        "last_timestamp_ms": last_event.get("t_end_ms") if last_event else None,
        "strength_counts": strength_counts,
        "change_scope_counts": change_scope_counts,
    }
