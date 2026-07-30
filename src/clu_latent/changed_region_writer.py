"""Phase 3.16: lock-aware writer for the changed-region evidence lane.

Mirrors `visual_change_writer.py`'s lock-aware, atomic, validate-before-
write conventions and "replace the whole track" semantics: `analyze`
replaces the whole `tracks/changed_region_candidates.jsonl` track in
one shot (every call recomputes every changed-region record from
scratch, one per currently-stored visual change candidate), rather than
appending new records to an existing track. `analyze_changed_regions`
refuses to run at all if the track already exists unless the caller
passes `force=True`.

This module still runs no visual AI, no object/scene/face/person
detection, no OCR, no text recognition, no captioning, and no semantic
summarization -- it only persists already-computed, already-validated
`changed_region_candidate` records (see
`changed_region.compute_changed_region_events`) into a real
`.clulatent` package.

Two independent safety mechanisms guard every write, exactly matching
`visual_change_writer.py`:

  - The **integrity lock** (`lock/package.lock.json`): if `lock_status()`
    reports `"locked"`, every write in this module refuses outright.
    There is no `--force` escape hatch for this one.
  - The **operation lock** (`lock/package.operation.lock.json`): guards
    against two concurrent writers racing each other.

Writing is atomic per-file (temp file + fsync + `os.replace`), and up
to three files are written in a fixed order -- changed-region track,
then manifest, then (if requested) the changed-region receipt -- with
best-effort rollback of every earlier file if a later write fails.

Never touches `sources/`, `media/`, any other `tracks/*.jsonl`
(including `tracks/visual_change_candidates.jsonl`, which is only ever
read here, never written), or `index/search.sqlite` -- only
`tracks/changed_region_candidates.jsonl`, `manifest.json`, and
(optionally) `receipts/changed_region.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .changed_region import compute_changed_region_events, validate_changed_region_track
from .constants import (
    CHANGED_REGION_RECEIPTS_FILE,
    CHANGED_REGION_TRACK_FILE,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
)
from .event import EventEnvelope
from .lock import lock_status
from .manifest import Manifest, TrackDescriptor
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package

CHANGED_REGION_TRACK_NAME = "changed_region_candidates"

_ENVELOPE_FIELDS = frozenset(
    {"id", "type", "t_start_ms", "t_end_ms", "producer", "confidence", "payload"}
)

_RECEIPT_STATUSES = frozenset({"success", "failure"})

_EVIDENCE_NOT_TRUTH_REMINDER = (
    "Changed-region records are evidence, not truth. They report bounded, "
    "numeric grid-cell difference candidates localized within a pair of "
    "already-stored keyframe images -- they do not identify objects, "
    "people, text, actions, intent, or scene meaning, and a low delta "
    "score does not mean nothing of interest happened."
)


class ChangedRegionWriteError(ValueError):
    """Raised when changed-region records (or a receipt) cannot be safely written to a package."""


@dataclass
class ChangedRegionWriteResult:
    package_path: Path
    track_file: str
    track_created: bool
    events_written: int
    event_ids: list[str]
    strength_counts: dict[str, int]
    change_scope_counts: dict[str, int]
    receipt_path: Path | None


@dataclass
class ChangedRegionReceipt:
    """Documented, code-enforced receipt shape for one changed-region write.

    Mirrors `visual_change_writer.VisualChangeReceipt`'s dataclass-plus-
    `to_dict()` shape.
    """

    operation: str
    status: str  # "success" | "failure"
    tool_name: str
    tool_version: str
    output_track: str
    event_count: int = 0
    strength_counts: dict[str, int] = field(default_factory=dict)
    change_scope_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failure_details: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    evidence_not_truth_reminder: str = _EVIDENCE_NOT_TRUTH_REMINDER

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "status": self.status,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "output_track": self.output_track,
            "event_count": self.event_count,
            "strength_counts": dict(self.strength_counts),
            "change_scope_counts": dict(self.change_scope_counts),
            "warnings": list(self.warnings),
            "failure_details": self.failure_details,
            "evidence_not_truth_reminder": self.evidence_not_truth_reminder,
        }


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise ChangedRegionWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise ChangedRegionWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ChangedRegionWriteError(f"manifest.json is invalid: {exc}") from exc


def _existing_track_descriptor(manifest: Manifest) -> TrackDescriptor | None:
    return next((t for t in manifest.tracks if t.name == CHANGED_REGION_TRACK_NAME), None)


def _check_no_unexpected_top_level_fields(record: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(record, dict):
        return
    for key in record:
        if key not in _ENVELOPE_FIELDS:
            errors.append(
                f"{prefix}: unexpected top-level field {key!r}; only {sorted(_ENVELOPE_FIELDS)} "
                "are permitted on a canonical changed-region event"
            )


def _envelope_kwargs(record: dict[str, Any]) -> dict[str, Any]:
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


def _check_receipt_bounds(receipt: ChangedRegionReceipt, limits: Limits) -> list[str]:
    errors: list[str] = []
    if receipt.status not in _RECEIPT_STATUSES:
        errors.append(
            f"receipt.status must be one of {sorted(_RECEIPT_STATUSES)}, got {receipt.status!r}"
        )

    for field_name, value, required in (
        ("receipt.operation", receipt.operation, True),
        ("receipt.tool_name", receipt.tool_name, True),
        ("receipt.tool_version", receipt.tool_version, True),
        ("receipt.output_track", receipt.output_track, True),
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

    for index, item in enumerate(receipt.warnings):
        _check_bounded_receipt_field(
            item,
            f"receipt.warnings[{index}]",
            max_bytes=limits.max_analysis_text_bytes,
            required=True,
            errors=errors,
        )

    for field_name, counts in (
        ("receipt.strength_counts", receipt.strength_counts),
        ("receipt.change_scope_counts", receipt.change_scope_counts),
    ):
        try:
            encoded_len = len(json.dumps(counts).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            errors.append(f"{field_name} is not JSON-serializable: {exc}")
        else:
            if encoded_len > limits.max_analysis_payload_bytes:
                errors.append(f"{field_name} exceeds the {limits.max_analysis_payload_bytes}-byte bound")

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
            raise ChangedRegionWriteError(f"{path}:{record.lineno}: {record.error}")
        entries.append(record.data)
    return entries


def _serialize_receipts(entries: list[dict[str, Any]]) -> bytes:
    if not entries:
        return b""
    lines = (json.dumps(entry) for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_receipt_locked(
    package_path: Path, receipt: ChangedRegionReceipt, *, limits: Limits
) -> Path:
    """Append one receipt entry to receipts/changed_region.jsonl. Caller must already hold the operation lock."""
    receipt_errors = _check_receipt_bounds(receipt, limits)
    if receipt_errors:
        raise ChangedRegionWriteError("refusing to write receipt: " + "; ".join(receipt_errors))

    try:
        receipt_path = resolve_in_package(
            package_path, CHANGED_REGION_RECEIPTS_FILE, field_name="receipts.changed_region", for_write=True
        )
    except PathSecurityError as exc:
        raise ChangedRegionWriteError(str(exc)) from exc

    existing = _read_receipts(receipt_path, limits=limits)
    existing.append(receipt.to_dict())
    _atomic_write(receipt_path, _serialize_receipts(existing))
    return receipt_path


def write_changed_region_receipt(
    package_path: Path,
    receipt: ChangedRegionReceipt,
    *,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Path:
    """Append one changed-region write receipt to `receipts/changed_region.jsonl`, on its own.

    Meant for a caller recording a receipt independently of
    `analyze_changed_regions` -- most notably a failed analysis run
    (`receipt.status="failure"`, `receipt.failure_details=...`) where no
    records were ever produced to write. Acquires the operation lock and
    refuses a locked package itself, exactly like
    `analyze_changed_regions`; do not call this from inside a caller
    that already holds the operation lock.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise ChangedRegionWriteError(f"Package not found: {package_path}")

    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise ChangedRegionWriteError(
            "package has a valid integrity lock (lock/package.lock.json); changed-region receipt "
            "writes refuse to write against a locked package"
        )

    try:
        with operation_lock(
            package_path, operation="changed_region", force_stale=force_stale_lock, limits=limits
        ):
            return _write_receipt_locked(package_path, receipt, limits=limits)
    except OperationLockError as exc:
        raise ChangedRegionWriteError(str(exc)) from exc


def analyze_changed_regions(
    package_path: Path,
    *,
    tool_name: str,
    tool_version: str,
    force: bool = False,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> ChangedRegionWriteResult:
    """Compute and write `tracks/changed_region_candidates.jsonl` for `package_path`.

    Computes one `changed_region_candidate` record per currently-stored
    visual change candidate (see
    `changed_region.compute_changed_region_events`), validates the full
    batch, and *replaces* the whole changed-region track in one atomic
    write -- this is a full recomputation, not an incremental append,
    unlike `audio_digest_writer.append_audio_digest_events`.

    Refuses (raises `ChangedRegionWriteError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - the package has a currently-valid integrity lock
        (`lock_status()` returns `"locked"`).
      - the operation lock cannot be acquired (another write in progress).
      - manifest.json is missing or invalid.
      - the changed-region track already exists (in the manifest) and
        `force=False`.
      - the computed candidate batch fails `changed_region
        .validate_changed_region_track` (checked with
        `package_root=package_path`).
      - `write_receipt=True` and the resulting receipt fails its own
        bound checks.

    Also propagates `changed_region.ChangedRegionComputeError` (no
    stored visual change candidates, too many candidates, missing/
    unreadable image) and `changed_region.ChangedRegionUnavailableError`
    (Pillow not installed) unchanged from `compute_changed_region_events`
    -- both are already clean, specific errors, raised before any file
    is touched.

    Nothing is written until every check above has passed for the
    entire computed batch.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise ChangedRegionWriteError(f"Package not found: {package_path}")

    try:
        with operation_lock(
            package_path, operation="changed_region", force_stale=force_stale_lock, limits=limits
        ):
            return _analyze_changed_regions_locked(
                package_path,
                tool_name=tool_name,
                tool_version=tool_version,
                force=force,
                write_receipt=write_receipt,
                limits=limits,
            )
    except OperationLockError as exc:
        raise ChangedRegionWriteError(str(exc)) from exc


def _analyze_changed_regions_locked(
    package_path: Path,
    *,
    tool_name: str,
    tool_version: str,
    force: bool,
    write_receipt: bool,
    limits: Limits,
) -> ChangedRegionWriteResult:
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise ChangedRegionWriteError(
            "package has a valid integrity lock (lock/package.lock.json); changed-region write "
            "commands refuse to write against a locked package"
        )

    manifest = _load_manifest(package_path, limits=limits)
    existing_track = _existing_track_descriptor(manifest)
    if existing_track is not None and not force:
        raise ChangedRegionWriteError(
            f"changed-region track {CHANGED_REGION_TRACK_NAME!r} already exists "
            f"({existing_track.file}); pass force=True (CLI: --force) to replace it"
        )

    # Import lazily so a caller that only needs the writer's other
    # entrypoints (e.g. `write_changed_region_receipt`) never pays for
    # the `changed_region` -> `visual_change_retrieval` import chain.
    candidate_events = compute_changed_region_events(
        package_path, tool_name=tool_name, tool_version=tool_version, limits=limits
    )

    errors, warnings = validate_changed_region_track(
        candidate_events, package_root=package_path, limits=limits
    )
    for index, record in enumerate(candidate_events):
        _check_no_unexpected_top_level_fields(record, f"changed_region_candidates[{index}]", errors)

    if errors:
        raise ChangedRegionWriteError(
            "refusing to write: computed changed-region events failed validation: " + "; ".join(errors)
        )

    new_envelopes: list[EventEnvelope] = []
    for record in candidate_events:
        try:
            new_envelopes.append(EventEnvelope.model_validate(_envelope_kwargs(record)))
        except ValidationError as exc:
            raise ChangedRegionWriteError(f"changed-region event fields are invalid: {exc}") from exc

    track_relpath = existing_track.file if existing_track is not None else CHANGED_REGION_TRACK_FILE
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name="tracks.changed_region_candidates", for_write=True
        )
    except PathSecurityError as exc:
        raise ChangedRegionWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    def _rollback_track() -> None:
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)

    _atomic_write(track_path, _serialize_track(new_envelopes))

    updated_tracks = [t for t in manifest.tracks if t.name != CHANGED_REGION_TRACK_NAME]
    updated_tracks.append(
        TrackDescriptor(
            name=CHANGED_REGION_TRACK_NAME,
            file=track_relpath,
            schema_id=EVENT_ENVELOPE_SCHEMA_ID,
            schema_version=EVENT_ENVELOPE_SCHEMA_VERSION,
            record_count=len(new_envelopes),
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

    strength_counts: dict[str, int] = {}
    change_scope_counts: dict[str, int] = {}
    for record in candidate_events:
        strength = record["payload"]["strength"]
        strength_counts[strength] = strength_counts.get(strength, 0) + 1
        change_scope = record["payload"]["change_scope"]
        change_scope_counts[change_scope] = change_scope_counts.get(change_scope, 0) + 1

    receipt_path: Path | None = None
    if write_receipt:
        receipt = ChangedRegionReceipt(
            operation="analyze_changed_regions",
            status="success",
            tool_name=tool_name,
            tool_version=tool_version,
            output_track=track_relpath,
            event_count=len(new_envelopes),
            strength_counts=strength_counts,
            change_scope_counts=change_scope_counts,
            warnings=list(warnings),
        )
        try:
            receipt_path = _write_receipt_locked(package_path, receipt, limits=limits)
        except BaseException:
            _atomic_write(manifest_path, original_manifest_bytes)
            _rollback_track()
            raise

    return ChangedRegionWriteResult(
        package_path=package_path,
        track_file=track_relpath,
        track_created=original_track_bytes is None,
        events_written=len(new_envelopes),
        event_ids=[event.id for event in new_envelopes],
        strength_counts=strength_counts,
        change_scope_counts=change_scope_counts,
        receipt_path=receipt_path,
    )
