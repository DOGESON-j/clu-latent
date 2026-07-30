"""Phase 3.8: read-only retrieval primitives for validated audio digest evidence.

Phase 3.4 (`audio_digest.py`) validates a digest record is shaped
safely. Phase 3.5 (`audio_digest_writer.py`) writes already-validated
records into `tracks/audio_digest_events.jsonl` with a receipt. Phase
3.6 put a CLI front door on both. Phase 3.7 (`validate.py`) validates
the whole track at package-validation time. Phase 3.8 is the first
place this evidence becomes *retrievable* -- by id, time range, record
type, linked evidence id, or salience/recommended-for-context flag --
without a caller re-implementing JSONL parsing or re-deriving Phase
3.4's validation rules themselves.

Core rule (Phase 3.3, restated here because it governs every function
below): **store deep, show shallow, retrieve detail only when
needed.** `select_audio_digest_llm_context` is the concrete expression
of this: it prefers the already-bounded, already-caveated
`audio_llm_context_packet` records (Level 5) when they exist, and only
falls back to raw `audio_digest_segment` records (Level 3) when no
packet has been produced yet -- it never touches
`audio_feature_series` (Level 1) payloads directly, since those exist
specifically to keep dense data *out* of normal context, referenced by
`data_path` instead.

What this module is NOT:

  - No audio adapter. No FFmpeg/librosa/Essentia/aubio/Basic
    Pitch/Demucs/YAMNet/PANNs/OpenL3 invocation of any kind.
  - No dense audio extraction, no stem separation, no ML dependency.
  - No auto-generated summaries. `summarize_audio_digest_retrieval_result`
    only counts and indexes records already on disk -- it never writes
    new text, never infers meaning, and never claims CLULatent
    understands audio.
  - Read-only. Nothing in this module writes a track file, a receipt,
    or the manifest, and nothing here touches `index/search.sqlite`.
  - Not a CLI command. That is a natural follow-on for a later phase,
    not this one.

Records are only ever handed back to a caller after passing the same
`audio_digest.validate_audio_digest_track` check `validate_package`
(Phase 3.7) already runs -- a caller of this module can never be handed
an unvalidated, forbidden-language, or raw-dense-array-carrying record,
even if one was somehow written to disk outside the Phase 3.5 writer
(e.g. hand-edited). If the on-disk track fails that check, every
retrieval function refuses cleanly (`AudioDigestRetrievalError`) rather
than silently returning partial or unsafe data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audio_digest import validate_audio_digest_track
from .audio_digest_writer import AUDIO_DIGEST_TRACK_NAME
from .manifest import Manifest
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

DEFAULT_MAX_CONTEXT_EVENTS = 20

_LINKED_ID_PAYLOAD_KEYS: tuple[str, ...] = (
    "linked_event_ids",
    "linked_feature_series_ids",
    "linked_digest_segment_ids",
)


class AudioDigestRetrievalError(ValueError):
    """Raised when audio digest evidence cannot be safely read from a package.

    Covers: a missing/non-directory package, an unreadable manifest, a
    manifest-declared audio digest track file that is missing from
    disk, a track file that exceeds size limits or contains malformed
    JSON, and a track whose records fail Phase 3.4 validation. Never
    raised for an *absent* audio digest track -- that is not an error,
    see `load_audio_digest_events`.
    """


@dataclass
class AudioDigestRetrievalResult:
    """A bounded set of already-validated audio digest records plus any
    non-fatal warnings surfaced while validating them."""

    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _resolve_package_root(package_root: Path | str) -> Path:
    package_root = Path(package_root)
    if not package_root.exists() or not package_root.is_dir():
        raise AudioDigestRetrievalError(
            f"Package not found or not a directory: {package_root}"
        )
    if not (package_root / "manifest.json").exists():
        raise AudioDigestRetrievalError(f"manifest.json not found in package: {package_root}")
    return package_root


def load_audio_digest_events(
    package_root: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Load and validate every record in `tracks/audio_digest_events.jsonl`.

    Returns an empty list if the package has no audio digest track
    declared in its manifest -- absence is not an error, mirroring the
    same optional-track convention `validate_package` (Phase 3.7)
    already uses for every analysis/audio-digest track. Raises
    `AudioDigestRetrievalError` if the package itself doesn't exist, if
    the manifest can't be read, if a track *is* declared but its file
    is missing/oversized/malformed, or if the track's records fail
    Phase 3.4 validation -- a caller must never be handed unvalidated
    audio digest evidence. Never mutates the package. Never raises for
    a *valid*, empty track (a zero-byte or manifest-absent track both
    return `[]`).
    """
    package_root = _resolve_package_root(package_root)

    try:
        manifest = Manifest.from_json_file(package_root / "manifest.json", limits=limits)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise AudioDigestRetrievalError(f"manifest.json could not be read: {exc}") from exc

    track = next((t for t in manifest.tracks if t.name == AUDIO_DIGEST_TRACK_NAME), None)
    if track is None:
        return []

    try:
        track_path = resolve_in_package(
            package_root, track.file, field_name="tracks[audio_digest_events].file"
        )
    except PathSecurityError as exc:
        raise AudioDigestRetrievalError(str(exc)) from exc

    if not track_path.exists():
        raise AudioDigestRetrievalError(
            f"audio digest track file listed in manifest is missing: {track.file}"
        )

    raw_records: list[dict[str, Any]] = []
    if track_path.stat().st_size > 0:
        try:
            for record in iter_jsonl_bounded(track_path, limits=limits):
                if record.error is not None:
                    raise AudioDigestRetrievalError(
                        f"{track.file}:{record.lineno}: {record.error}"
                    )
                raw_records.append(record.data)
        except JsonlLimitError as exc:
            raise AudioDigestRetrievalError(f"{track.file}: exceeds size limits: {exc}") from exc

    errors, _warnings = validate_audio_digest_track(
        raw_records, package_root=package_root, limits=limits
    )
    if errors:
        raise AudioDigestRetrievalError(
            "audio digest track failed validation and cannot be safely retrieved: "
            + "; ".join(errors[:5])
            + (f" (+{len(errors) - 5} more)" if len(errors) > 5 else "")
        )

    return raw_records


def get_audio_digest_event_by_id(
    package_root: Path | str,
    event_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any] | None:
    """Return the one record with `id == event_id`, or `None` if absent.

    Never raises for a missing id -- only for the same package/track-
    level failures `load_audio_digest_events` already raises for.
    """
    for event in load_audio_digest_events(package_root, limits=limits):
        if event.get("id") == event_id:
            return event
    return None


def _overlaps(event: dict[str, Any], start_ms: float, end_ms: float) -> bool:
    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    return t_start <= end_ms and t_end >= start_ms


def query_audio_digest_by_time_range(
    package_root: Path | str,
    start_ms: int,
    end_ms: int,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every record whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

    An empty result (no records overlap the range, or the track is
    absent) is not an error.
    """
    if end_ms < start_ms:
        raise AudioDigestRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
    events = load_audio_digest_events(package_root, limits=limits)
    return [event for event in events if _overlaps(event, start_ms, end_ms)]


def query_audio_digest_by_type(
    package_root: Path | str,
    record_type: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every record whose `type == record_type`.

    `record_type` is not restricted to `AUDIO_DIGEST_RECORD_TYPES` here
    -- an unrecognized type simply matches nothing (the track itself
    would already have failed validation in `load_audio_digest_events`
    if it actually contained one). An empty result is not an error.
    """
    events = load_audio_digest_events(package_root, limits=limits)
    return [event for event in events if event.get("type") == record_type]


def query_audio_digest_by_linked_evidence_id(
    package_root: Path | str,
    evidence_id: str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return every record that references `evidence_id`.

    Matches a record whose own `id` equals `evidence_id`, or whose
    payload lists it in `linked_event_ids`, `linked_feature_series_ids`,
    or `linked_digest_segment_ids` (whichever of those fields that
    record type carries). Preserves the evidence-linking contract
    Phase 3.4 already validates -- this only searches for it, it never
    invents or infers a link. An empty result is not an error.
    """
    events = load_audio_digest_events(package_root, limits=limits)
    matches: list[dict[str, Any]] = []
    for event in events:
        if event.get("id") == evidence_id:
            matches.append(event)
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in _LINKED_ID_PAYLOAD_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and evidence_id in value:
                matches.append(event)
                break
    return matches


def query_audio_digest_by_salience(
    package_root: Path | str,
    *,
    min_salience: float | None = None,
    recommended_for_llm_context: bool | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Return `audio_digest_segment` records matching salience/recommendation filters.

    Only `audio_digest_segment` carries `payload.salience` and
    `payload.recommended_for_llm_context` (Phase 3.4), so records of
    any other type never match. At least one of `min_salience` /
    `recommended_for_llm_context` must be given, or every segment
    record is returned unfiltered. An empty result is not an error.
    """
    events = load_audio_digest_events(package_root, limits=limits)
    results: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "audio_digest_segment":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if min_salience is not None:
            salience = payload.get("salience")
            if (
                not isinstance(salience, (int, float))
                or isinstance(salience, bool)
                or salience < min_salience
            ):
                continue
        if recommended_for_llm_context is not None:
            if payload.get("recommended_for_llm_context") != recommended_for_llm_context:
                continue
        results.append(event)
    return results


def select_audio_digest_llm_context(
    package_root: Path | str,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
    max_events: int = DEFAULT_MAX_CONTEXT_EVENTS,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Select a small, bounded set of records safe to hand to an LLM by default.

    Store deep, show shallow: prefers already-bounded, already-caveated
    `audio_llm_context_packet` records (Level 5) overlapping the given
    range (or every packet, if no range is given). Only when no packet
    exists does this fall back to `audio_digest_segment` records
    (Level 3), sorted by `payload.salience` descending so the most
    salient evidence-linked segments come first. Never returns
    `audio_feature_series` (Level 1) records -- those exist precisely
    to keep dense data referenced-by-path and out of normal context.
    Always bounded to at most `max_events` records. An empty result
    (no track, or no packet/segment overlaps the range) is not an
    error.
    """
    if max_events <= 0:
        raise AudioDigestRetrievalError(f"max_events must be a positive integer, got {max_events}")
    if start_ms is not None and end_ms is not None and end_ms < start_ms:
        raise AudioDigestRetrievalError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")

    events = load_audio_digest_events(package_root, limits=limits)

    def _in_range(event: dict[str, Any]) -> bool:
        if start_ms is None and end_ms is None:
            return True
        lo = start_ms if start_ms is not None else 0
        hi = end_ms if end_ms is not None else float("inf")
        return _overlaps(event, lo, hi)

    packets = [
        event
        for event in events
        if event.get("type") == "audio_llm_context_packet" and _in_range(event)
    ]
    if packets:
        packets.sort(key=lambda event: event.get("t_start_ms", 0))
        return packets[:max_events]

    def _salience(event: dict[str, Any]) -> float:
        payload = event.get("payload")
        value = payload.get("salience") if isinstance(payload, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return 0.0

    segments = [
        event
        for event in events
        if event.get("type") == "audio_digest_segment" and _in_range(event)
    ]
    segments.sort(key=_salience, reverse=True)
    return segments[:max_events]


def summarize_audio_digest_retrieval_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce a small, shallow index of an already-retrieved event list.

    Never generates prose, never infers meaning -- only counts and
    indexes fields already present on the given records (record-type
    counts, ids, and the min/max time range covered). Safe to log or
    hand to an LLM as an overview before retrieving any individual
    record's detail (`get_audio_digest_event_by_id`), matching "store
    deep, show shallow, retrieve detail only when needed." An empty
    `events` list produces a zeroed-out, still-valid summary, not an
    error.
    """
    record_type_counts: dict[str, int] = {}
    event_ids: list[str] = []
    min_t_start: int | None = None
    max_t_end: int | None = None

    for event in events:
        record_type = event.get("type")
        if isinstance(record_type, str):
            record_type_counts[record_type] = record_type_counts.get(record_type, 0) + 1
        event_id = event.get("id")
        if isinstance(event_id, str):
            event_ids.append(event_id)
        t_start = event.get("t_start_ms")
        t_end = event.get("t_end_ms")
        if isinstance(t_start, int):
            min_t_start = t_start if min_t_start is None else min(min_t_start, t_start)
        if isinstance(t_end, int):
            max_t_end = t_end if max_t_end is None else max(max_t_end, t_end)

    return {
        "event_count": len(events),
        "record_type_counts": record_type_counts,
        "event_ids": event_ids,
        "t_start_ms": min_t_start,
        "t_end_ms": max_t_end,
    }
