"""Phase 2.2: controlled, lock-aware writer for review_events.jsonl.

Builds on the frozen Phase 2.0 factories/validation (`review.py`) and
Phase 2.1 read-only resolver (`review_resolver.py`). This module is the
first thing in CLULatent that actually *writes* a review event to disk.

Core principle (unchanged from Phase 1.9/2.0/2.1): human review is
additive evidence. `append_review_event` never mutates, deletes, or
reorders any existing record in any track — including
`review_events.jsonl` itself. It only ever appends one new
`EventEnvelope` to the in-memory list of existing review events, then
rewrites `tracks/review_events.jsonl` (and the `review_events`
`TrackDescriptor` in `manifest.json`) with that appended list. Every
other canonical track is only ever read, never touched.

Two independent safety mechanisms guard every write:

  - The **integrity lock** (`lock/package.lock.json`, see `lock.py`):
    if `lock_status()` reports `"locked"` (a currently-valid lock),
    `append_review_event` refuses outright. There is no `--force`
    escape hatch here and no "unlock" workflow — the only way to write
    a review event against a locked package is to re-run `clulatent
    lock` afterwards to bring the lock back in sync, exactly as any
    other canonical-file change already requires.
  - The **operation lock** (`lock/package.operation.lock.json`, see
    `security/operation_lock.py`): guards against two concurrent
    `clulatent review ...` (or `reindex`/`lock`) invocations racing
    each other. This module does not acquire it itself — callers (the
    `clulatent review` CLI commands) are expected to wrap
    `append_review_event` in `operation_lock(...)`, the same pattern
    already used by `reindex`/`lock` in `cli.py`.

Writing itself is atomic per-file (temp file + fsync + `os.replace`,
mirroring `lock.py`'s private `_atomic_write`), and the two files
involved (`tracks/review_events.jsonl`, `manifest.json`) are written in
a fixed order — track file first, manifest second — with a best-effort
rollback of the track file if the manifest write fails, so a crash
between the two writes never leaves `review_events.jsonl` silently
ahead of what `manifest.json` declares without at least an attempt to
restore consistency. `validate_review_track` is run against the full
appended-events list *before* either file is touched, so a shape
violation is caught and reported without writing anything at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import review as review_mod
from .constants import (
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    REVIEW_EVENTS_TRACK_FILE,
    REVIEW_EVENTS_TRACK_NAME,
)
from .event import EventEnvelope
from .lock import lock_status
from .manifest import Manifest, TrackDescriptor
from .review import ReviewEventError, validate_review_track
from .security.jsonl import JsonlLimitError
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package
from .tracks import TrackReadError, read_track_file

_TYPE_ID_PREFIX = {
    "review_status": "rv_status_",
    "review_approval": "rv_approval_",
    "review_rejection": "rv_rejection_",
    "review_correction": "rv_correction_",
    "review_override": "rv_override_",
    "human_note": "rv_note_",
    "review_session_summary": "rv_session_",
}


class ReviewWriteError(ValueError):
    """Raised when a review event cannot be safely appended to a package."""


@dataclass
class ReviewWriteResult:
    event_id: str
    package_path: Path
    review_track_created: bool


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not package_path.exists() or not package_path.is_dir():
        raise ReviewWriteError(f"Package not found: {package_path}")
    if not manifest_path.exists():
        raise ReviewWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise ReviewWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewWriteError(f"manifest.json is invalid: {exc}") from exc


def _next_index(existing_events: list[EventEnvelope], event_type: str) -> int:
    prefix = _TYPE_ID_PREFIX[event_type]
    max_index = -1
    for event in existing_events:
        if event.id.startswith(prefix):
            suffix = event.id[len(prefix) :]
            if suffix.isdigit():
                max_index = max(max_index, int(suffix))
    return max_index + 1


def _find_target_event(
    package_path: Path, manifest: Manifest, target_event_id: str, *, limits: Limits
) -> EventEnvelope:
    for track in manifest.tracks:
        if track.name == REVIEW_EVENTS_TRACK_NAME:
            continue
        try:
            track_path = resolve_in_package(
                package_path, track.file, field_name=f"tracks[{track.name}].file"
            )
        except PathSecurityError as exc:
            raise ReviewWriteError(str(exc)) from exc
        if not track_path.exists():
            continue
        try:
            events = read_track_file(track_path, limits=limits)
        except (JsonlLimitError, TrackReadError) as exc:
            raise ReviewWriteError(f"{track.file}: {exc}") from exc
        for event in events:
            if event.id == target_event_id:
                return event

    raise ReviewWriteError(
        f"event id {target_event_id!r} was not found in any canonical (non-review) track "
        "of this package"
    )


def _load_review_track(
    package_path: Path, manifest: Manifest, *, limits: Limits
) -> tuple[list[EventEnvelope], TrackDescriptor | None]:
    track = next((t for t in manifest.tracks if t.name == REVIEW_EVENTS_TRACK_NAME), None)
    if track is None:
        return [], None
    try:
        track_path = resolve_in_package(
            package_path, track.file, field_name=f"tracks[{track.name}].file"
        )
    except PathSecurityError as exc:
        raise ReviewWriteError(str(exc)) from exc
    if not track_path.exists():
        return [], track
    try:
        events = read_track_file(track_path, limits=limits)
    except (JsonlLimitError, TrackReadError) as exc:
        raise ReviewWriteError(f"{track.file}: {exc}") from exc
    return events, track


def _build_event(
    event_type: str,
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    reviewer_label: str | None,
    reason: str | None,
    reviewed_at: str,
    confidence: float | None,
    source_event_ids: list[str],
    note_text: str | None,
    review_state: str | None,
    original_payload: dict[str, Any] | None,
    corrected_payload: dict[str, Any] | None,
    override_kind: str | None,
    limits: Limits,
) -> EventEnvelope:
    common = dict(
        index=index,
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reviewed_at=reviewed_at,
        confidence=confidence,
        limits=limits,
    )
    try:
        if event_type == "review_approval":
            return review_mod.make_review_approval_event(
                source_event_ids=source_event_ids, reason=reason, **common
            )
        if event_type == "review_rejection":
            return review_mod.make_review_rejection_event(
                source_event_ids=source_event_ids, reason=reason, **common
            )
        if event_type == "review_status":
            if review_state is None:
                raise ReviewWriteError("review_status requires review_state (--state)")
            return review_mod.make_review_status_event(
                source_event_ids=source_event_ids,
                review_state=review_state,
                reason=reason,
                **common,
            )
        if event_type == "review_correction":
            if original_payload is None or corrected_payload is None:
                raise ReviewWriteError(
                    "review_correction requires both original_payload and corrected_payload"
                )
            return review_mod.make_review_correction_event(
                source_event_ids=source_event_ids,
                original_payload=original_payload,
                corrected_payload=corrected_payload,
                reason=reason,
                **common,
            )
        if event_type == "review_override":
            if original_payload is None or corrected_payload is None:
                raise ReviewWriteError(
                    "review_override requires both original_payload and corrected_payload"
                )
            if review_state is None:
                raise ReviewWriteError("review_override requires review_state (--state)")
            if override_kind is None:
                raise ReviewWriteError("review_override requires override_kind (--override-kind)")
            return review_mod.make_review_override_event(
                source_event_ids=source_event_ids,
                override_kind=override_kind,
                original_payload=original_payload,
                corrected_payload=corrected_payload,
                review_state=review_state,
                reason=reason,
                **common,
            )
        if event_type == "human_note":
            if note_text is None:
                raise ReviewWriteError("human_note requires note_text (--note)")
            note_common = dict(common)
            return review_mod.make_human_note_event(
                note_text=note_text,
                source_event_ids=source_event_ids or None,
                **note_common,
            )
    except ReviewEventError as exc:
        raise ReviewWriteError(str(exc)) from exc
    except ValidationError as exc:
        # A factory's own checks (review.py's `_check_*` helpers) cover
        # every review-specific field, but envelope-level rules (e.g.
        # `confidence` outside [0.0, 1.0]) are enforced by `EventEnvelope`
        # itself and raise pydantic's ValidationError, not
        # ReviewEventError — caught here too so an out-of-range
        # `--certainty` fails cleanly instead of leaking a raw pydantic
        # traceback.
        raise ReviewWriteError(f"review event fields are invalid: {exc}") from exc

    raise ReviewWriteError(f"unsupported review event type: {event_type!r}")


def _serialize_track(events: list[EventEnvelope]) -> bytes:
    ordered = sorted(events, key=lambda e: e.t_start_ms)
    if not ordered:
        return b""
    lines = (json.dumps(event.model_dump(mode="json")) for event in ordered)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically (temp file + fsync + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _manifest_bytes(manifest: Manifest) -> bytes:
    return (json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=False) + "\n").encode(
        "utf-8"
    )


def append_review_event(
    package_path: Path,
    event_type: str,
    *,
    target_event_id: str | None = None,
    reviewer_id: str,
    reviewer_label: str | None = None,
    reason: str | None = None,
    note_text: str | None = None,
    review_state: str | None = None,
    original_payload: dict[str, Any] | None = None,
    corrected_payload: dict[str, Any] | None = None,
    override_kind: str | None = None,
    confidence: float | None = None,
    reviewed_at: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ReviewWriteResult:
    """Append one review judgment/note event to `tracks/review_events.jsonl`.

    Does not acquire the operation lock itself — callers must wrap this
    in `security.operation_lock.operation_lock(...)` to guard against
    concurrent writes, exactly like `reindex`/`lock` already do.

    Refuses (raises `ReviewWriteError`, writes nothing) if:
      - `event_type` is not a recognized review event type.
      - the package has a currently-valid integrity lock
        (`lock_status()` returns `"locked"`).
      - manifest.json is missing or invalid.
      - (for every type except a source-less `human_note`) no track in
        the package contains an event with id `target_event_id`.
      - a type-specific required field is missing (e.g. `review_state`
        for `review_status`, both payload patches for
        `review_correction`/`review_override`).
      - any field fails `review.py`'s own shape/bound checks.
      - the resulting `review_events.jsonl` (existing events plus the
        new one) would fail `validate_review_track`.
    """
    if event_type not in _TYPE_ID_PREFIX:
        raise ReviewWriteError(f"unsupported review event type: {event_type!r}")

    package_path = Path(package_path)

    # Checked before `lock_status` (which resolves the path with
    # `strict=True` and raises a raw FileNotFoundError on a missing
    # package) so a nonexistent package path always surfaces as a clean
    # ReviewWriteError, never an unhandled exception.
    if not package_path.exists() or not package_path.is_dir():
        raise ReviewWriteError(f"Package not found: {package_path}")

    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise ReviewWriteError(
            "package has a valid integrity lock (lock/package.lock.json); review write "
            "commands refuse to write against a locked package — re-run `clulatent lock` "
            "after writing is not possible while locked, so this write is refused entirely"
        )

    manifest = _load_manifest(package_path, limits=limits)
    reviewed_at_value = reviewed_at or datetime.now(timezone.utc).isoformat()

    source_event_ids: list[str] = []
    t_start_ms = 0
    t_end_ms = 0
    if event_type == "human_note" and target_event_id is None:
        pass
    else:
        if not target_event_id:
            raise ReviewWriteError(f"{event_type} requires a target event id")
        target_event = _find_target_event(package_path, manifest, target_event_id, limits=limits)
        source_event_ids = [target_event_id]
        t_start_ms = target_event.t_start_ms
        t_end_ms = target_event.t_end_ms

    existing_events, existing_track = _load_review_track(package_path, manifest, limits=limits)
    index = _next_index(existing_events, event_type)

    new_event = _build_event(
        event_type,
        index=index,
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        reviewed_at=reviewed_at_value,
        confidence=confidence,
        source_event_ids=source_event_ids,
        note_text=note_text,
        review_state=review_state,
        original_payload=original_payload,
        corrected_payload=corrected_payload,
        override_kind=override_kind,
        limits=limits,
    )

    updated_events = existing_events + [new_event]

    errors, _warnings = validate_review_track(updated_events, limits=limits)
    if errors:
        raise ReviewWriteError(
            "refusing to write: the resulting review_events.jsonl would fail validation: "
            + "; ".join(errors)
        )

    track_relpath = existing_track.file if existing_track is not None else REVIEW_EVENTS_TRACK_FILE
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name="tracks[review_events].file", for_write=True
        )
    except PathSecurityError as exc:
        raise ReviewWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    _atomic_write(track_path, _serialize_track(updated_events))

    updated_tracks = [t for t in manifest.tracks if t.name != REVIEW_EVENTS_TRACK_NAME]
    updated_tracks.append(
        TrackDescriptor(
            name=REVIEW_EVENTS_TRACK_NAME,
            file=track_relpath,
            schema_id=EVENT_ENVELOPE_SCHEMA_ID,
            schema_version=EVENT_ENVELOPE_SCHEMA_VERSION,
            record_count=len(updated_events),
            sorted_by="t_start_ms",
        )
    )
    updated_manifest = manifest.model_copy(update={"tracks": updated_tracks})

    manifest_path = package_path / "manifest.json"
    try:
        _atomic_write(manifest_path, _manifest_bytes(updated_manifest))
    except BaseException:
        # Best-effort rollback: keep tracks/review_events.jsonl consistent
        # with the manifest that is actually on disk if the manifest write
        # itself failed, rather than leaving the new event "ahead" of what
        # manifest.json declares.
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)
        raise

    return ReviewWriteResult(
        event_id=new_event.id,
        package_path=package_path,
        # `original_track_bytes is None` (not `existing_track is None`)
        # so this is still accurate if the manifest already had a
        # review_events TrackDescriptor whose file was missing on disk —
        # this write really did (re)create the file.
        review_track_created=original_track_bytes is None,
    )
