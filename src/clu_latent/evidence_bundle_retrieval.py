"""Phase 3.17: read-only retrieval primitives for the evidence bundle lane.

Mirrors `changed_region_retrieval.py`'s read-only conventions: every
function here only reads already-computed `evidence_bundle` records
back from `tracks/evidence_bundles.jsonl` (written by
`evidence_bundle_writer.build_evidence_bundle`) -- nothing here gathers
new evidence, runs a retrieval module of its own, or mutates a package
in any way.

Core rule (unchanged from `evidence_bundle.py`):

    Evidence bundle, not semantic interpretation.

What this module is NOT:

  - No package mutation. Nothing here writes a track, a manifest, a
    receipt, an index, or a lock file.
  - No network access.
  - No dense-data dump: retrieval returns only the small, already-
    stored bundle records, never raw image bytes or other evidence
    payloads beyond what a bundle already summarizes.

An *absent* evidence bundle track is not an error -- it yields an
empty result, mirroring every other Phase 3.x retrieval module's
convention for an absent optional track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tracks as tracks_mod
from .evidence_bundle import COVERAGE_KEYS, EVIDENCE_CATEGORIES
from .manifest import Manifest
from .security.jsonl import JsonlLimitError
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

EVIDENCE_BUNDLE_TRACK_NAME = "evidence_bundles"

# Bound on the number of evidence bundle records returned by any single
# retrieval so a pathological package cannot produce an unbounded
# result, mirroring `changed_region_retrieval.DEFAULT_MAX_CHANGED_REGION_EVENTS`.
DEFAULT_MAX_EVIDENCE_BUNDLE_EVENTS = 5000


class EvidenceBundleRetrievalError(ValueError):
    """Raised when evidence bundle evidence cannot be safely read from a package.

    Covers: a missing/non-directory package, an unreadable manifest, a
    manifest-declared evidence bundle track file missing from disk, a
    track file that exceeds size limits or contains a malformed/invalid
    record, and a record whose referenced keyframe image path is
    unsafe. Never raised for an *absent* evidence bundle track -- that
    is not an error, see `load_evidence_bundle_events`.
    """


@dataclass
class EvidenceBundleRetrievalResult:
    """A bounded set of evidence bundle records plus any non-fatal warnings."""

    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_package_root(package_root: Path | str) -> Path:
    package_root = Path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise EvidenceBundleRetrievalError(f"Package not found or not a directory: {package_root}")
    if not (package_root / "manifest.json").exists():
        raise EvidenceBundleRetrievalError(f"manifest.json not found in package: {package_root}")
    return package_root


def _bundle_image_paths(event: dict[str, Any]) -> list[str]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return []
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, dict):
        return []
    keyframes = evidence_refs.get("keyframes")
    if not isinstance(keyframes, list):
        return []
    paths: list[str] = []
    for ref in keyframes:
        if isinstance(ref, dict) and isinstance(ref.get("image_path"), str) and ref["image_path"]:
            paths.append(ref["image_path"])
    return paths


def load_evidence_bundle_events(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Load every record in the `evidence_bundles` track as ordered plain dicts.

    Returns an empty list if the package declares no evidence bundle
    track -- absence is not an error. Raises `EvidenceBundleRetrievalError`
    if the package itself doesn't exist, the manifest can't be read, a
    track *is* declared but its file is missing/oversized/malformed, or
    a record references an unsafe keyframe image path. Records are
    returned sorted by `t_start_ms` (then by id) so time-ordered
    retrieval is deterministic. Never mutates the package.
    """
    package_root = _resolve_package_root(package_root)

    try:
        manifest = Manifest.from_json_file(package_root / "manifest.json", limits=limits)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise EvidenceBundleRetrievalError(f"manifest.json could not be read: {exc}") from exc

    descriptor = next((t for t in manifest.tracks if t.name == EVIDENCE_BUNDLE_TRACK_NAME), None)
    if descriptor is None:
        return []

    try:
        track_path = resolve_in_package(
            package_root, descriptor.file, field_name="tracks[evidence_bundles].file"
        )
    except PathSecurityError as exc:
        raise EvidenceBundleRetrievalError(str(exc)) from exc

    if not track_path.exists():
        raise EvidenceBundleRetrievalError(
            f"evidence bundle track file listed in manifest is missing: {descriptor.file}"
        )

    try:
        envelopes = tracks_mod.read_track_file(track_path, limits=limits)
    except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
        raise EvidenceBundleRetrievalError(f"evidence bundle track could not be read cleanly: {exc}") from exc

    events = [envelope.model_dump(mode="json") for envelope in envelopes]

    # Reject any record whose referenced keyframe image path is unsafe
    # *before* handing the evidence back to a caller, mirroring
    # `changed_region_retrieval.load_changed_region_events`.
    for event in events:
        for image_path in _bundle_image_paths(event):
            try:
                resolve_in_package(
                    package_root, image_path, field_name="evidence_bundle.evidence_refs.keyframes[].image_path"
                )
            except PathSecurityError as exc:
                raise EvidenceBundleRetrievalError(
                    f"evidence bundle {event.get('id')!r} references an unsafe keyframe image path: {exc}"
                ) from exc

    events.sort(key=lambda e: (e.get("t_start_ms", 0), str(e.get("id", ""))))
    return events


def get_evidence_bundle_by_id(
    package_root: Path | str,
    bundle_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the one evidence bundle record with `id == bundle_id`, or `None`.

    Never raises for a missing id -- only for the same package/track-
    level failures `load_evidence_bundle_events` already raises for.
    """
    for event in load_evidence_bundle_events(package_root, limits=limits):
        if event.get("id") == bundle_id:
            return event
    return None


def _overlaps(event: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    return t_start <= end_ms and t_end >= start_ms


def query_evidence_bundles_by_time_range(
    package_root: Path | str,
    start_ms: int,
    end_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every evidence bundle whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no bundle overlaps the range, or the track is
    absent) is not an error. An inverted range (`end_ms < start_ms`) is
    a caller mistake and raises `EvidenceBundleRetrievalError`.
    """
    if end_ms < start_ms:
        raise EvidenceBundleRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
    events = load_evidence_bundle_events(package_root, limits=limits)
    return [event for event in events if _overlaps(event, start_ms, end_ms)]


def summarize_evidence_bundles(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Produce a small, shallow index of a package's evidence bundle evidence.

    Reports only counts and already-stored fields -- bundle count,
    first/last bundle id and timestamp, a per-`coverage.*` true-count,
    and a per-evidence-category count of how many bundles list that
    category in `missing_evidence`. Never generates prose, never infers
    meaning. A package with no evidence bundle track produces a
    zeroed-out, still-valid summary (exit path, not an error). Bounded:
    at most `DEFAULT_MAX_EVIDENCE_BUNDLE_EVENTS` records are considered.
    """
    events = load_evidence_bundle_events(package_root, limits=limits)[:DEFAULT_MAX_EVIDENCE_BUNDLE_EVENTS]

    coverage_counts: dict[str, int] = {key: 0 for key in sorted(COVERAGE_KEYS)}
    missing_evidence_counts: dict[str, int] = {category: 0 for category in EVIDENCE_CATEGORIES}

    for event in events:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            for key, value in coverage.items():
                if key in coverage_counts and value is True:
                    coverage_counts[key] += 1
        missing_evidence = payload.get("missing_evidence")
        if isinstance(missing_evidence, list):
            for category in missing_evidence:
                if category in missing_evidence_counts:
                    missing_evidence_counts[category] += 1

    first_event = events[0] if events else None
    last_event = events[-1] if events else None

    return {
        "bundle_count": len(events),
        "first_bundle_id": first_event.get("id") if first_event else None,
        "first_timestamp_ms": first_event.get("t_start_ms") if first_event else None,
        "last_bundle_id": last_event.get("id") if last_event else None,
        "last_timestamp_ms": last_event.get("t_end_ms") if last_event else None,
        "coverage_counts": coverage_counts,
        "missing_evidence_counts": missing_evidence_counts,
    }
