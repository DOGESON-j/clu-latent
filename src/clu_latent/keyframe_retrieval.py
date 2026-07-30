"""Phase 3.13: read-only retrieval primitives for stored keyframe evidence.

Ingest (Phase 1) records a `keyframes` track: one `keyframe` event per
stored frame, each carrying the frame's timestamp and the package-
relative path of its image. Phase 3.12 rendered that track as a static
HTML contact sheet. Phase 3.13 is the first place the same evidence
becomes *retrievable as data* -- by event id, by time range, by nearest
timestamp, or as a shallow retrieval summary -- without a caller
re-implementing JSONL parsing or path-safety checks.

Core rule (it governs everything below):

    Show visual evidence. Do not interpret visual evidence.

This is a **visual evidence retriever, not a visual understanding
system**. Nothing here runs visual AI, captions a frame, infers scene
meaning / object identity / intent / emotion, runs OCR or motion
analysis, or extracts new frames. It only reads keyframe records ingest
already recorded and hands them back verbatim.

What this module is NOT:

  - No FFmpeg, no frame extraction, no image decoding, no ML dependency.
  - No package mutation. Nothing here writes a track, a manifest, a
    receipt, an index, or a lock file.
  - No network access.
  - No dense-data dump: retrieval is bounded and returns only the small,
    already-stored keyframe records (id, time, and the package-relative
    image path), never raw image bytes.

Safety: every keyframe's stored image path is checked through
`resolve_in_package` (path containment / symlink rejection). A record
whose path escapes the package or is otherwise unsafe causes the whole
retrieval to refuse cleanly (`KeyframeRetrievalError`) rather than
returning unsafe data. An *absent* keyframes track is not an error --
it yields an empty result, mirroring the optional-track convention the
rest of the codebase already uses.
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

KEYFRAMES_TRACK_NAME = "keyframes"

# Bound on the number of keyframe records returned by any single
# retrieval so a pathological package (millions of keyframe records)
# cannot produce an unbounded result.
DEFAULT_MAX_KEYFRAMES = 5000


class KeyframeRetrievalError(ValueError):
    """Raised when keyframe evidence cannot be safely read from a package.

    Covers: a missing/non-directory package, an unreadable manifest, a
    manifest-declared keyframes track file missing from disk, a track
    file that exceeds size limits or contains a malformed/invalid
    record, and a record whose stored image path is unsafe (escapes the
    package or fails symlink containment). Never raised for an *absent*
    keyframes track -- that is not an error, see `load_keyframe_events`.
    """


@dataclass
class KeyframeRetrievalResult:
    """A bounded set of keyframe records plus any non-fatal warnings."""

    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_package_root(package_root: Path | str) -> Path:
    package_root = Path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise KeyframeRetrievalError(
            f"Package not found or not a directory: {package_root}"
        )
    if not (package_root / "manifest.json").exists():
        raise KeyframeRetrievalError(f"manifest.json not found in package: {package_root}")
    return package_root


def _keyframe_image_path(event: dict[str, Any]) -> str | None:
    """Return the package-relative image path recorded on a keyframe, if any."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    raw_path = payload.get("path")
    return raw_path if isinstance(raw_path, str) and raw_path else None


def load_keyframe_events(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Load every record in the `keyframes` track as ordered plain dicts.

    Returns an empty list if the package declares no keyframes track --
    absence is not an error. Raises `KeyframeRetrievalError` if the
    package itself doesn't exist, the manifest can't be read, a track
    *is* declared but its file is missing/oversized/malformed, or a
    record carries an unsafe image path. Records are returned sorted by
    `t_start_ms` (then by id) so time-ordered retrieval is deterministic.
    Never mutates the package.
    """
    package_root = _resolve_package_root(package_root)

    try:
        manifest = Manifest.from_json_file(package_root / "manifest.json", limits=limits)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise KeyframeRetrievalError(f"manifest.json could not be read: {exc}") from exc

    descriptor = next(
        (t for t in manifest.tracks if t.name == KEYFRAMES_TRACK_NAME), None
    )
    if descriptor is None:
        return []

    try:
        track_path = resolve_in_package(
            package_root, descriptor.file, field_name="tracks[keyframes].file"
        )
    except PathSecurityError as exc:
        raise KeyframeRetrievalError(str(exc)) from exc

    if not track_path.exists():
        raise KeyframeRetrievalError(
            f"keyframes track file listed in manifest is missing: {descriptor.file}"
        )

    try:
        envelopes = tracks_mod.read_track_file(track_path, limits=limits)
    except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
        raise KeyframeRetrievalError(
            f"keyframes track could not be read cleanly: {exc}"
        ) from exc

    events = [envelope.model_dump(mode="json") for envelope in envelopes]

    # Reject any record whose stored image path is unsafe *before* handing
    # the evidence back to a caller. A traversal/symlink-escaping path can
    # only reach here via a hand-edited track (the ingest writer never
    # produces one), so refusing the whole retrieval is the safe choice.
    for event in events:
        image_path = _keyframe_image_path(event)
        if image_path is None:
            continue
        try:
            resolve_in_package(package_root, image_path, field_name="keyframe.payload.path")
        except PathSecurityError as exc:
            raise KeyframeRetrievalError(
                f"keyframe {event.get('id')!r} has an unsafe image path: {exc}"
            ) from exc

    events.sort(key=lambda e: (e.get("t_start_ms", 0), str(e.get("id", ""))))
    return events


def get_keyframe_by_id(
    package_root: Path | str,
    event_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the one keyframe record with `id == event_id`, or `None`.

    Never raises for a missing id -- only for the same package/track-
    level failures `load_keyframe_events` already raises for.
    """
    for event in load_keyframe_events(package_root, limits=limits):
        if event.get("id") == event_id:
            return event
    return None


def _overlaps(event: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    return t_start <= end_ms and t_end >= start_ms


def query_keyframes_by_time_range(
    package_root: Path | str,
    start_ms: int,
    end_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every keyframe whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

    Results keep the load order (ascending `t_start_ms`, then id). An
    empty result (no keyframe overlaps the range, or the track is
    absent) is not an error. An inverted range (`end_ms < start_ms`) is
    a caller mistake and raises `KeyframeRetrievalError`.
    """
    if end_ms < start_ms:
        raise KeyframeRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
    events = load_keyframe_events(package_root, limits=limits)
    return [event for event in events if _overlaps(event, start_ms, end_ms)]


def _distance_to(event: dict[str, Any], time_ms: int) -> int | None:
    """Distance in ms from `time_ms` to a keyframe's `[t_start_ms, t_end_ms]`.

    Zero when `time_ms` falls inside the interval. `None` when the
    record has no usable integer timestamps (so it can never be nearest).
    """
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return None
    if time_ms < t_start:
        return t_start - time_ms
    if time_ms > t_end:
        return time_ms - t_end
    return 0


def get_nearest_keyframe(
    package_root: Path | str,
    time_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the keyframe closest in time to `time_ms`, or `None` if none exist.

    Distance is measured to each keyframe's `[t_start_ms, t_end_ms]`
    interval (zero when `time_ms` is inside it). Ties are broken
    deterministically: smallest distance first, then earliest
    `t_start_ms`, then lexicographically smallest id -- so the same
    package and timestamp always yield the same frame. An empty track
    (or one with no usably-timed records) yields `None`, not an error.
    """
    best: dict[str, Any] | None = None
    best_key: tuple[int, int, str] | None = None
    for event in load_keyframe_events(package_root, limits=limits):
        distance = _distance_to(event, time_ms)
        if distance is None:
            continue
        t_start = event.get("t_start_ms")
        t_start = t_start if isinstance(t_start, int) else 0
        key = (distance, t_start, str(event.get("id", "")))
        if best_key is None or key < best_key:
            best_key = key
            best = event
    return best


def keyframe_image_missing(package_root: Path | str, event: dict[str, Any]) -> bool:
    """Return True if this keyframe's stored image file is absent from disk.

    A record with no recorded path counts as missing evidence. The path
    is resolved through `resolve_in_package` (containment-checked); an
    unsafe path is treated as missing here rather than re-raising, since
    `load_keyframe_events` already refuses unsafe paths up front.
    """
    image_path = _keyframe_image_path(event)
    if image_path is None:
        return True
    try:
        resolved = resolve_in_package(
            Path(package_root), image_path, field_name="keyframe.payload.path"
        )
    except PathSecurityError:
        return True
    try:
        return not resolved.is_file()
    except OSError:
        return True


def summarize_keyframe_retrieval(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Produce a small, shallow index of a package's keyframe evidence.

    Reports only counts and already-stored fields -- keyframe count,
    first/last timestamp, the ordered event ids, and which (if any)
    keyframe image files are missing from disk. Never generates prose,
    never infers meaning. A package with no keyframes track produces a
    zeroed-out, still-valid summary (exit path, not an error). Bounded:
    at most `DEFAULT_MAX_KEYFRAMES` ids/paths are listed.
    """
    events = load_keyframe_events(package_root, limits=limits)

    event_ids: list[str] = []
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    missing_image_paths: list[str] = []

    for event in events:
        event_id = event.get("id")
        if isinstance(event_id, str):
            event_ids.append(event_id)
        t_start = event.get("t_start_ms")
        t_end = event.get("t_end_ms")
        if isinstance(t_start, int):
            first_timestamp_ms = (
                t_start if first_timestamp_ms is None else min(first_timestamp_ms, t_start)
            )
        if isinstance(t_end, int):
            last_timestamp_ms = (
                t_end if last_timestamp_ms is None else max(last_timestamp_ms, t_end)
            )
        if keyframe_image_missing(package_root, event):
            path = _keyframe_image_path(event)
            missing_image_paths.append(path if path is not None else "(no path recorded)")

    return {
        "keyframe_count": len(events),
        "first_timestamp_ms": first_timestamp_ms,
        "last_timestamp_ms": last_timestamp_ms,
        "event_ids": event_ids[:DEFAULT_MAX_KEYFRAMES],
        "missing_image_count": len(missing_image_paths),
        "missing_image_paths": missing_image_paths[:DEFAULT_MAX_KEYFRAMES],
    }
