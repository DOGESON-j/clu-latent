"""Phase 2.7: lock-aware writer for future analysis-lane events.

Builds directly on the frozen Phase 2.6 schema primitives
(`analysis_lanes.py`, tag `phase-2.6-analysis-lane-schema-primitives-freeze`)
and mirrors the lock-aware, atomic, validate-before-write conventions
`review_writer.py` (Phase 2.2) already established for
`tracks/review_events.jsonl`. This module still implements no video/
audio analysis, no FFmpeg tracker, no OCR runtime, no object detector,
and no ML model dependency -- it only gives a future adapter (or a
test, or a human) a safe, conservative path to append *already-
produced* analysis-lane event dicts into a real `.clulatent` package.

Core principle (unchanged): adapters produce evidence, not truth.
Detected does not mean trusted. Generated does not mean canonical.
Canonical means validated, bounded, receipted, reviewable, and
lockable. This module adds validated + bounded + receipted; reviewable
and lockable already work unmodified once a lane track exists (see
`docs/PHASE_2_7_ANALYSIS_LANE_WRITER_RECEIPTS.md`).

Differences from `review_writer.py`, and why:

  - `review_writer.append_review_event` does **not** acquire the
    operation lock itself -- the `clulatent review ...` CLI commands
    wrap it in `security.operation_lock.operation_lock(...)`. Phase
    2.7 adds no CLI surface (no adapter runtime exists to drive one
    yet), so `append_analysis_events`/`write_analysis_receipt` acquire
    the operation lock themselves, around the full validate-then-write
    sequence, so calling either directly is still safe against
    concurrent writers without a CLI wrapper. A future
    `clulatent analyze ...` CLI (if one is ever added) would still
    work correctly calling these functions -- it would simply never
    need its own extra `operation_lock(...)` wrapper.
  - `duration_ms`, if a candidate record supplies it, is validated by
    `analysis_lanes.validate_analysis_event` for consistency with
    `t_end_ms - t_start_ms` but is never itself written to the on-disk
    `EventEnvelope` -- the shared envelope (`event.py`) has no
    `duration_ms` slot, and the field is fully redundant with
    `t_end_ms - t_start_ms` once consistency has been checked, so
    nothing is lost by not storing it separately. Any other top-level
    field beyond the envelope's own six
    (`id`/`type`/`t_start_ms`/`t_end_ms`/`producer`/`confidence`) plus
    `payload` and `duration_ms` is rejected outright (not silently
    dropped) -- an unrecognized top-level key most likely indicates an
    adapter bug, not an intentional field only the writer understands.

Two independent safety mechanisms guard every write (same two
mechanisms `review_writer.py` uses, just acquired one layer lower --
see above):

  - The **integrity lock** (`lock/package.lock.json`, see `lock.py`):
    if `lock_status()` reports `"locked"`, every write in this module
    refuses outright. There is no `--force` escape hatch and no
    "unlock" workflow, exactly like review writes.
  - The **operation lock** (`lock/package.operation.lock.json`, see
    `security/operation_lock.py`): guards against two concurrent
    writers racing each other.

Writing is atomic per-file (temp file + fsync + `os.replace`,
mirroring `review_writer._atomic_write`), and up to three files are
written in a fixed order -- lane track file, then manifest, then (if
requested) the adapter receipt -- with best-effort rollback of every
earlier file if a later write fails, so a crash partway through never
leaves the lane track, manifest, or receipt inconsistent with each
other. `validate_analysis_track` (Phase 2.6, run against the real
`package_root` for full path/symlink safety) runs against the full
candidate batch *before* any file is touched, so a shape violation is
caught and reported without writing anything at all.

Never touches `sources/`, any other `tracks/*.jsonl`, or
`index/search.sqlite` -- only the one lane track named, `manifest.json`,
and (optionally) `receipts/analyze.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .analysis_lanes import (
    AnalysisAdapterReceipt,
    AnalysisEventError,
    normalize_analysis_lane_name,
    validate_analysis_track,
)
from .constants import (
    ANALYSIS_RECEIPTS_FILE,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    TRACKS_DIR,
)
from .event import EventEnvelope
from .lock import lock_status
from .manifest import Manifest, TrackDescriptor
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package
from .tracks import TrackReadError, read_track_file

# The shared envelope's own fields (event.py). `duration_ms` is the one
# extra top-level field `analysis_lanes.validate_analysis_event` allows
# a candidate record to carry for its own consistency check -- see
# module docstring for why it is validated but never stored.
_ENVELOPE_FIELDS = frozenset(
    {"id", "type", "t_start_ms", "t_end_ms", "producer", "confidence", "payload"}
)
_ALLOWED_EXTRA_TOP_LEVEL_FIELDS = frozenset({"duration_ms"})

_RECEIPT_STATUSES = frozenset({"success", "partial", "failure"})


class AnalysisWriteError(ValueError):
    """Raised when analysis lane events (or a receipt) cannot be safely written to a package."""


@dataclass
class AnalysisWriteResult:
    package_path: Path
    lane: str
    track_file: str
    track_created: bool
    events_written: int
    event_ids: list[str]
    receipt_path: Path | None


def build_analysis_track_path(lane: str) -> str:
    """Return the conventional relative track path for a supported analysis lane.

    e.g. `build_analysis_track_path("scene_events") == "tracks/scene_events.jsonl"`.
    Raises `AnalysisWriteError` (not `analysis_lanes.AnalysisEventError`)
    for an unsupported lane name, so every public function in this
    module only ever raises one exception type.
    """
    try:
        lane = normalize_analysis_lane_name(lane)
    except AnalysisEventError as exc:
        raise AnalysisWriteError(str(exc)) from exc
    return f"{TRACKS_DIR}/{lane}.jsonl"


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise AnalysisWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise AnalysisWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AnalysisWriteError(f"manifest.json is invalid: {exc}") from exc


def _load_lane_track(
    package_path: Path, manifest: Manifest, lane: str, *, limits: Limits
) -> tuple[list[EventEnvelope], TrackDescriptor | None]:
    track = next((t for t in manifest.tracks if t.name == lane), None)
    if track is None:
        return [], None
    try:
        track_path = resolve_in_package(
            package_path, track.file, field_name=f"tracks[{track.name}].file"
        )
    except PathSecurityError as exc:
        raise AnalysisWriteError(str(exc)) from exc
    if not track_path.exists():
        return [], track
    try:
        events = read_track_file(track_path, limits=limits)
    except (JsonlLimitError, TrackReadError) as exc:
        raise AnalysisWriteError(f"{track.file}: {exc}") from exc
    return events, track


def _check_no_unexpected_top_level_fields(record: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(record, dict):
        return
    allowed = _ENVELOPE_FIELDS | _ALLOWED_EXTRA_TOP_LEVEL_FIELDS
    for key in record:
        if key not in allowed:
            errors.append(
                f"{prefix}: unexpected top-level field {key!r}; only {sorted(allowed)} "
                "are permitted on a canonical analysis event"
            )


def _envelope_kwargs(record: dict[str, Any]) -> dict[str, Any]:
    """Project a validated raw record onto the shared EventEnvelope's fields.

    `duration_ms`, if present, is intentionally omitted -- see module
    docstring. Every field used here has already been shape/bound-
    checked by `validate_analysis_track` before this is ever called.
    """
    kwargs: dict[str, Any] = {
        "id": record["id"],
        "type": record["type"],
        "t_start_ms": record["t_start_ms"],
        "t_end_ms": record["t_end_ms"],
        "producer": record["producer"],
        "payload": record.get("payload", {}),
    }
    if "confidence" in record:
        kwargs["confidence"] = record["confidence"]
    return kwargs


def _check_bounded_receipt_field(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool,
    errors: list[str],
) -> None:
    if value is None or value == "":
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if len(value.encode("utf-8")) > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound")


def _check_receipt_bounds(receipt: AnalysisAdapterReceipt, limits: Limits) -> list[str]:
    errors: list[str] = []
    if receipt.status not in _RECEIPT_STATUSES:
        errors.append(
            f"receipt.status must be one of {sorted(_RECEIPT_STATUSES)}, got {receipt.status!r}"
        )

    for field_name, value, required in (
        ("receipt.adapter_name", receipt.adapter_name, True),
        ("receipt.tool_name", receipt.tool_name, True),
        ("receipt.tool_version", receipt.tool_version, True),
        ("receipt.model_name", receipt.model_name, False),
        ("receipt.model_version", receipt.model_version, False),
        ("receipt.failure_details", receipt.failure_details, False),
    ):
        max_bytes = (
            limits.max_analysis_text_bytes
            if field_name == "receipt.failure_details"
            else limits.max_analysis_label_bytes
        )
        _check_bounded_receipt_field(
            value, field_name, max_bytes=max_bytes, required=required, errors=errors
        )

    for index, item in enumerate(receipt.output_tracks):
        _check_bounded_receipt_field(
            item,
            f"receipt.output_tracks[{index}]",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )
    for index, item in enumerate(receipt.input_sources):
        _check_bounded_receipt_field(
            item,
            f"receipt.input_sources[{index}]",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )
    for index, item in enumerate(receipt.warnings):
        _check_bounded_receipt_field(
            item,
            f"receipt.warnings[{index}]",
            max_bytes=limits.max_analysis_text_bytes,
            required=True,
            errors=errors,
        )

    for field_name, value in (
        ("parameters", receipt.parameters),
        ("environment", receipt.environment),
        ("event_counts", receipt.event_counts),
    ):
        try:
            encoded_len = len(json.dumps(value).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            errors.append(f"receipt.{field_name} is not JSON-serializable: {exc}")
            continue
        if encoded_len > limits.max_analysis_payload_bytes:
            errors.append(
                f"receipt.{field_name} exceeds the {limits.max_analysis_payload_bytes}-byte bound"
            )

    return errors


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
    return (
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def _read_receipts(path: Path, *, limits: Limits) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for record in iter_jsonl_bounded(path, limits=limits):
        if record.error is not None:
            raise AnalysisWriteError(f"{path}:{record.lineno}: {record.error}")
        entries.append(record.data)
    return entries


def _serialize_receipts(entries: list[dict[str, Any]]) -> bytes:
    if not entries:
        return b""
    lines = (json.dumps(entry) for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_receipt_locked(
    package_path: Path, receipt: AnalysisAdapterReceipt, *, limits: Limits
) -> Path:
    """Append one receipt entry to receipts/analyze.jsonl. Caller must already hold the operation lock."""
    receipt_errors = _check_receipt_bounds(receipt, limits)
    if receipt_errors:
        raise AnalysisWriteError("refusing to write receipt: " + "; ".join(receipt_errors))

    try:
        receipt_path = resolve_in_package(
            package_path, ANALYSIS_RECEIPTS_FILE, field_name="receipts.analyze", for_write=True
        )
    except PathSecurityError as exc:
        raise AnalysisWriteError(str(exc)) from exc

    existing = _read_receipts(receipt_path, limits=limits)
    existing.append(receipt.to_dict())
    _atomic_write(receipt_path, _serialize_receipts(existing))
    return receipt_path


def write_analysis_receipt(
    package_path: Path,
    receipt: AnalysisAdapterReceipt,
    *,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Path:
    """Append one adapter-run receipt to `receipts/analyze.jsonl`, on its own.

    Meant for a caller recording a receipt independently of
    `append_analysis_events` -- most notably a failed adapter run
    (`receipt.status="failure"`, `receipt.failure_details=...`) where
    no events were ever produced to append. Acquires the operation
    lock and refuses a locked package itself, exactly like
    `append_analysis_events`; do not call this from inside a caller
    that already holds the operation lock -- there is no reentrant
    lock here, matching `security.operation_lock`'s own no-reentrancy.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AnalysisWriteError(f"Package not found: {package_path}")

    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AnalysisWriteError(
            "package has a valid integrity lock (lock/package.lock.json); analysis receipt "
            "writes refuse to write against a locked package"
        )

    try:
        with operation_lock(
            package_path, operation="analyze", force_stale=force_stale_lock, limits=limits
        ):
            return _write_receipt_locked(package_path, receipt, limits=limits)
    except OperationLockError as exc:
        raise AnalysisWriteError(str(exc)) from exc


def append_analysis_events(
    package_path: Path,
    lane: str,
    events: list[dict[str, Any]],
    *,
    adapter_name: str,
    tool_name: str,
    tool_version: str,
    model_name: str | None = None,
    model_version: str | None = None,
    parameters: dict[str, Any] | None = None,
    input_sources: list[str] | None = None,
    environment: dict[str, Any] | None = None,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> AnalysisWriteResult:
    """Validate and append `events` to `tracks/<lane>.jsonl`, updating the manifest (and, by default, writing a receipt).

    `events` is a list of raw, untrusted record dicts (see
    `analysis_lanes.validate_analysis_event` for the required shape) --
    not `EventEnvelope` instances. `adapter_name`/`tool_name`/
    `tool_version` (and the optional adapter-metadata kwargs) describe
    the run and are only used to build the receipt written to
    `receipts/analyze.jsonl` when `write_receipt=True` (the default).

    Refuses (raises `AnalysisWriteError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - `lane` is not one of `analysis_lanes.ANALYSIS_LANE_NAMES`.
      - `events` is empty.
      - the package has a currently-valid integrity lock
        (`lock_status()` returns `"locked"`).
      - the operation lock cannot be acquired (another write in progress).
      - manifest.json is missing or invalid.
      - any candidate event fails `analysis_lanes.validate_analysis_track`
        (checked with `package_root=package_path`, so path-like payload
        fields get full filesystem containment + symlink-escape checks,
        not just the lexical check) -- including unsupported/identity-
        claiming payloads, absolute paths, and parent traversal.
      - any candidate event has an id already present in the existing
        lane track, or a top-level field the shared envelope does not
        recognize (other than the allowed `duration_ms`).
      - `write_receipt=True` and the resulting receipt fails its own
        bound checks.

    Nothing is written until every check above has passed for the
    *entire* batch -- a single invalid event in `events` blocks the
    whole call, not just that one record.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AnalysisWriteError(f"Package not found: {package_path}")

    try:
        lane = normalize_analysis_lane_name(lane)
    except AnalysisEventError as exc:
        raise AnalysisWriteError(str(exc)) from exc

    if not events:
        raise AnalysisWriteError("events must be a non-empty list")

    try:
        with operation_lock(
            package_path, operation="analyze", force_stale=force_stale_lock, limits=limits
        ):
            return _append_analysis_events_locked(
                package_path,
                lane,
                events,
                adapter_name=adapter_name,
                tool_name=tool_name,
                tool_version=tool_version,
                model_name=model_name,
                model_version=model_version,
                parameters=parameters,
                input_sources=input_sources,
                environment=environment,
                write_receipt=write_receipt,
                limits=limits,
            )
    except OperationLockError as exc:
        raise AnalysisWriteError(str(exc)) from exc


def _append_analysis_events_locked(
    package_path: Path,
    lane: str,
    events: list[dict[str, Any]],
    *,
    adapter_name: str,
    tool_name: str,
    tool_version: str,
    model_name: str | None,
    model_version: str | None,
    parameters: dict[str, Any] | None,
    input_sources: list[str] | None,
    environment: dict[str, Any] | None,
    write_receipt: bool,
    limits: Limits,
) -> AnalysisWriteResult:
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AnalysisWriteError(
            "package has a valid integrity lock (lock/package.lock.json); analysis lane write "
            "commands refuse to write against a locked package"
        )

    manifest = _load_manifest(package_path, limits=limits)
    existing_events, existing_track = _load_lane_track(package_path, manifest, lane, limits=limits)
    existing_ids = {event.id for event in existing_events}

    errors, warnings = validate_analysis_track(
        events, lane=lane, package_root=package_path, limits=limits
    )
    for index, record in enumerate(events):
        prefix = f"{lane}[{index}]"
        _check_no_unexpected_top_level_fields(record, prefix, errors)
        if (
            isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and record["id"] in existing_ids
        ):
            errors.append(
                f"{prefix}: event id {record['id']!r} already exists in the {lane} track"
            )

    if errors:
        raise AnalysisWriteError(
            f"refusing to write: candidate {lane} events failed validation: " + "; ".join(errors)
        )

    new_envelopes: list[EventEnvelope] = []
    for record in events:
        try:
            new_envelopes.append(EventEnvelope.model_validate(_envelope_kwargs(record)))
        except ValidationError as exc:
            raise AnalysisWriteError(f"analysis event fields are invalid: {exc}") from exc

    updated_events = existing_events + new_envelopes

    track_relpath = existing_track.file if existing_track is not None else build_analysis_track_path(lane)
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name=f"tracks[{lane}].file", for_write=True
        )
    except PathSecurityError as exc:
        raise AnalysisWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    def _rollback_track() -> None:
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)

    _atomic_write(track_path, _serialize_track(updated_events))

    updated_tracks = [t for t in manifest.tracks if t.name != lane]
    updated_tracks.append(
        TrackDescriptor(
            name=lane,
            file=track_relpath,
            schema_id=EVENT_ENVELOPE_SCHEMA_ID,
            schema_version=EVENT_ENVELOPE_SCHEMA_VERSION,
            record_count=len(updated_events),
            sorted_by="t_start_ms",
        )
    )
    updated_manifest = manifest.model_copy(update={"tracks": updated_tracks})

    manifest_path = package_path / "manifest.json"
    original_manifest_bytes = manifest_path.read_bytes()
    try:
        _atomic_write(manifest_path, _manifest_bytes(updated_manifest))
    except BaseException:
        _rollback_track()
        raise

    receipt_path: Path | None = None
    if write_receipt:
        receipt = AnalysisAdapterReceipt(
            adapter_name=adapter_name,
            tool_name=tool_name,
            tool_version=tool_version,
            status="success",
            output_tracks=[track_relpath],
            event_counts={lane: len(new_envelopes)},
            input_sources=list(input_sources) if input_sources else [],
            parameters=dict(parameters) if parameters else {},
            model_name=model_name,
            model_version=model_version,
            warnings=list(warnings),
            environment=dict(environment) if environment else {},
        )
        try:
            receipt_path = _write_receipt_locked(package_path, receipt, limits=limits)
        except BaseException:
            _atomic_write(manifest_path, original_manifest_bytes)
            _rollback_track()
            raise

    return AnalysisWriteResult(
        package_path=package_path,
        lane=lane,
        track_file=track_relpath,
        # `original_track_bytes is None` (not `existing_track is None`)
        # so this stays accurate even if the manifest already had a
        # TrackDescriptor for this lane whose file was missing on disk.
        track_created=original_track_bytes is None,
        events_written=len(new_envelopes),
        event_ids=[event.id for event in new_envelopes],
        receipt_path=receipt_path,
    )


def append_analysis_event(
    package_path: Path,
    lane: str,
    event: dict[str, Any],
    **kwargs: Any,
) -> AnalysisWriteResult:
    """Convenience wrapper: append exactly one analysis event. See `append_analysis_events`."""
    return append_analysis_events(package_path, lane, [event], **kwargs)
