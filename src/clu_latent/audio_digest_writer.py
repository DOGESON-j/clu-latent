"""Phase 3.5: lock-aware writer for validated audio digest records.

Builds directly on the frozen Phase 3.4 schema primitives
(`audio_digest.py`, tag
`phase-3.4-audio-digest-schema-primitives-freeze`) and mirrors the
lock-aware, atomic, validate-before-write conventions
`analysis_writer.py` (Phase 2.7) already established for
`tracks/<lane>.jsonl`. This module still implements no audio analysis,
no FFmpeg/librosa/Essentia/aubio/Demucs invocation, no dense audio
extraction, no ML model dependency, and no LLM call -- it only gives a
caller (a future digest builder, or a human, or a test) a safe,
conservative path to append *already-validated* audio digest record
dicts into a real `.clulatent` package.

Core principle (unchanged, restated from Phase 3.3/3.4): this writer
stores validated audio digest evidence. It does not generate audio
evidence. It does not claim CLULatent understands audio. It preserves
the rule: **store deep, show shallow, retrieve detail only when
needed.**

Unlike `analysis_writer.py`, which routes to one of many per-lane
track files (`tracks/<lane>.jsonl`), every audio digest record -- of
any of the three Phase 3.4 types (`audio_feature_series`,
`audio_digest_segment`, `audio_llm_context_packet`) -- is appended to
one shared, mixed-type track file, `tracks/audio_digest_events.jsonl`
(`AUDIO_DIGEST_TRACK_FILE`), matching how
`audio_digest.validate_audio_digest_track` already expects to validate
a mixed-type iterable in one pass.

Two independent safety mechanisms guard every write (same two
mechanisms `analysis_writer.py`/`review_writer.py` use):

  - The **integrity lock** (`lock/package.lock.json`, see `lock.py`):
    if `lock_status()` reports `"locked"`, every write in this module
    refuses outright. There is no `--force` escape hatch.
  - The **operation lock** (`lock/package.operation.lock.json`, see
    `security/operation_lock.py`): guards against two concurrent
    writers racing each other.

Writing is atomic per-file (temp file + fsync + `os.replace`,
mirroring `analysis_writer._atomic_write`), and up to three files are
written in a fixed order -- audio digest track file, then manifest,
then (if requested) the audio digest receipt -- with best-effort
rollback of every earlier file if a later write fails, so a crash
partway through never leaves the track, manifest, or receipt
inconsistent with each other. `audio_digest.validate_audio_digest_track`
(run against the real `package_root` for full path/symlink safety on
any `audio_feature_series.payload.data_path`) runs against the full
candidate batch *before* any file is touched, so a shape violation --
including a duplicate id inside the batch, which
`validate_audio_digest_track` already detects -- is caught and
reported without writing anything at all. A duplicate id against the
*existing* on-disk track (which `validate_audio_digest_track` cannot
know about on its own) is checked separately by this module, the same
way `analysis_writer.py` checks it for lane tracks.

Never touches `sources/`, any other `tracks/*.jsonl` (including any
Phase 2.5/2.6 analysis lane track), or `index/search.sqlite` -- only
`tracks/audio_digest_events.jsonl`, `manifest.json`, and (optionally)
`receipts/audio_digest.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .audio_digest import validate_audio_digest_track
from .constants import (
    AUDIO_DIGEST_RECEIPTS_FILE,
    AUDIO_DIGEST_TRACK_FILE,
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
from .tracks import TrackReadError, read_track_file

AUDIO_DIGEST_TRACK_NAME = "audio_digest_events"

_ENVELOPE_FIELDS = frozenset(
    {"id", "type", "t_start_ms", "t_end_ms", "producer", "confidence", "payload"}
)

_RECEIPT_STATUSES = frozenset({"success", "failure"})

_EVIDENCE_NOT_TRUTH_REMINDER = (
    "Audio digest records are evidence, not truth. They do not establish "
    "intent, meaning, or a listener's emotional response, and omitted or "
    "lower-salience evidence still exists in the package and remains "
    "retrievable, not discarded."
)


class AudioDigestWriteError(ValueError):
    """Raised when audio digest records (or a receipt) cannot be safely written to a package."""


@dataclass
class AudioDigestWriteResult:
    package_path: Path
    track_file: str
    track_created: bool
    events_written: int
    event_ids: list[str]
    record_type_counts: dict[str, int]
    receipt_path: Path | None


@dataclass
class AudioDigestReceipt:
    """Documented, code-enforced receipt shape for one audio digest write.

    Mirrors `analysis_lanes.AnalysisAdapterReceipt`'s
    dataclass-plus-`to_dict()` shape, with `timestamp`/`operation`
    fields added (matching `receipts.ReceiptLog.add`'s existing
    ingest-receipt convention) since a digest write, unlike an
    analysis-lane adapter run, has no separate adapter identity beyond
    the tool that produced/validated the records.
    """

    operation: str
    status: str  # "success" | "failure"
    tool_name: str
    tool_version: str
    output_track: str
    event_count: int = 0
    record_type_counts: dict[str, int] = field(default_factory=dict)
    linked_evidence: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    failure_details: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
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
            "record_type_counts": dict(self.record_type_counts),
            "linked_evidence": dict(self.linked_evidence),
            "warnings": list(self.warnings),
            "failure_details": self.failure_details,
            "evidence_not_truth_reminder": self.evidence_not_truth_reminder,
        }


def build_audio_digest_track_path(package_root: Path) -> Path:
    """Return the real, containment-checked path to `tracks/audio_digest_events.jsonl` inside `package_root`.

    Raises `AudioDigestWriteError` (not `PathSecurityError`) so every
    public function in this module only ever raises one exception
    type.
    """
    package_root = Path(package_root)
    try:
        return resolve_in_package(
            package_root,
            AUDIO_DIGEST_TRACK_FILE,
            field_name="tracks.audio_digest_events",
            for_write=True,
        )
    except PathSecurityError as exc:
        raise AudioDigestWriteError(str(exc)) from exc


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise AudioDigestWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise AudioDigestWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AudioDigestWriteError(f"manifest.json is invalid: {exc}") from exc


def _load_audio_digest_track(
    package_path: Path, manifest: Manifest, *, limits: Limits
) -> tuple[list[EventEnvelope], TrackDescriptor | None]:
    track = next((t for t in manifest.tracks if t.name == AUDIO_DIGEST_TRACK_NAME), None)
    if track is None:
        return [], None
    try:
        track_path = resolve_in_package(
            package_path, track.file, field_name=f"tracks[{track.name}].file"
        )
    except PathSecurityError as exc:
        raise AudioDigestWriteError(str(exc)) from exc
    if not track_path.exists():
        return [], track
    try:
        events = read_track_file(track_path, limits=limits)
    except (JsonlLimitError, TrackReadError) as exc:
        raise AudioDigestWriteError(f"{track.file}: {exc}") from exc
    return events, track


def _check_no_unexpected_top_level_fields(record: Any, prefix: str, errors: list[str]) -> None:
    if not isinstance(record, dict):
        return
    for key in record:
        if key not in _ENVELOPE_FIELDS:
            errors.append(
                f"{prefix}: unexpected top-level field {key!r}; only {sorted(_ENVELOPE_FIELDS)} "
                "are permitted on a canonical audio digest event"
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


def _check_receipt_bounds(receipt: AudioDigestReceipt, limits: Limits) -> list[str]:
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

    for field_name, value in (
        ("record_type_counts", receipt.record_type_counts),
        ("linked_evidence", receipt.linked_evidence),
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
            raise AudioDigestWriteError(f"{path}:{record.lineno}: {record.error}")
        entries.append(record.data)
    return entries


def _serialize_receipts(entries: list[dict[str, Any]]) -> bytes:
    if not entries:
        return b""
    lines = (json.dumps(entry) for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_receipt_locked(
    package_path: Path, receipt: AudioDigestReceipt, *, limits: Limits
) -> Path:
    """Append one receipt entry to receipts/audio_digest.jsonl. Caller must already hold the operation lock."""
    receipt_errors = _check_receipt_bounds(receipt, limits)
    if receipt_errors:
        raise AudioDigestWriteError("refusing to write receipt: " + "; ".join(receipt_errors))

    try:
        receipt_path = resolve_in_package(
            package_path, AUDIO_DIGEST_RECEIPTS_FILE, field_name="receipts.audio_digest", for_write=True
        )
    except PathSecurityError as exc:
        raise AudioDigestWriteError(str(exc)) from exc

    existing = _read_receipts(receipt_path, limits=limits)
    existing.append(receipt.to_dict())
    _atomic_write(receipt_path, _serialize_receipts(existing))
    return receipt_path


def write_audio_digest_receipt(
    package_path: Path,
    receipt: AudioDigestReceipt,
    *,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Path:
    """Append one audio digest write receipt to `receipts/audio_digest.jsonl`, on its own.

    Meant for a caller recording a receipt independently of
    `append_audio_digest_events` -- most notably a failed write
    (`receipt.status="failure"`, `receipt.failure_details=...`) where
    no records were ever produced to append. Acquires the operation
    lock and refuses a locked package itself, exactly like
    `append_audio_digest_events`; do not call this from inside a
    caller that already holds the operation lock.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AudioDigestWriteError(f"Package not found: {package_path}")

    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AudioDigestWriteError(
            "package has a valid integrity lock (lock/package.lock.json); audio digest receipt "
            "writes refuse to write against a locked package"
        )

    try:
        with operation_lock(
            package_path, operation="audio_digest", force_stale=force_stale_lock, limits=limits
        ):
            return _write_receipt_locked(package_path, receipt, limits=limits)
    except OperationLockError as exc:
        raise AudioDigestWriteError(str(exc)) from exc


def append_audio_digest_events(
    package_path: Path,
    events: list[dict[str, Any]],
    *,
    tool_name: str,
    tool_version: str,
    linked_evidence: dict[str, Any] | None = None,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> AudioDigestWriteResult:
    """Validate and append `events` to `tracks/audio_digest_events.jsonl`, updating the manifest (and, by default, writing a receipt).

    `events` is a list of raw, untrusted record dicts of any mix of the
    three Phase 3.4 digest types (see
    `audio_digest.validate_audio_digest_track` for the required
    shapes) -- not `EventEnvelope` instances. `tool_name`/`tool_version`
    describe the caller producing/validating the batch and are only
    used to build the receipt written to `receipts/audio_digest.jsonl`
    when `write_receipt=True` (the default). `linked_evidence` is an
    optional, bounded, JSON-serializable dict of input/linked-evidence
    metadata (e.g. which lane events or feature series a batch of
    digest segments was built from) recorded on the receipt only --
    never written into the track itself.

    Refuses (raises `AudioDigestWriteError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - `events` is empty.
      - the package has a currently-valid integrity lock
        (`lock_status()` returns `"locked"`).
      - the operation lock cannot be acquired (another write in progress).
      - manifest.json is missing or invalid.
      - any candidate event fails
        `audio_digest.validate_audio_digest_track` (checked with
        `package_root=package_path`, so `audio_feature_series.payload
        .data_path` gets full filesystem containment + symlink-escape
        checks) -- including unsupported types, forbidden language,
        raw dense arrays, and duplicate ids *within* the batch.
      - any candidate event has an id already present in the existing
        audio digest track, or a top-level field the shared envelope
        does not recognize.
      - `write_receipt=True` and the resulting receipt fails its own
        bound checks.

    Nothing is written until every check above has passed for the
    *entire* batch -- a single invalid event in `events` blocks the
    whole call, not just that one record.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AudioDigestWriteError(f"Package not found: {package_path}")

    if not events:
        raise AudioDigestWriteError("events must be a non-empty list")

    try:
        with operation_lock(
            package_path, operation="audio_digest", force_stale=force_stale_lock, limits=limits
        ):
            return _append_audio_digest_events_locked(
                package_path,
                events,
                tool_name=tool_name,
                tool_version=tool_version,
                linked_evidence=linked_evidence,
                write_receipt=write_receipt,
                limits=limits,
            )
    except OperationLockError as exc:
        raise AudioDigestWriteError(str(exc)) from exc


def _append_audio_digest_events_locked(
    package_path: Path,
    events: list[dict[str, Any]],
    *,
    tool_name: str,
    tool_version: str,
    linked_evidence: dict[str, Any] | None,
    write_receipt: bool,
    limits: Limits,
) -> AudioDigestWriteResult:
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AudioDigestWriteError(
            "package has a valid integrity lock (lock/package.lock.json); audio digest write "
            "commands refuse to write against a locked package"
        )

    manifest = _load_manifest(package_path, limits=limits)
    existing_events, existing_track = _load_audio_digest_track(package_path, manifest, limits=limits)
    existing_ids = {event.id for event in existing_events}

    errors, warnings = validate_audio_digest_track(events, package_root=package_path, limits=limits)
    for index, record in enumerate(events):
        prefix = f"audio_digest_events[{index}]"
        _check_no_unexpected_top_level_fields(record, prefix, errors)
        if (
            isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and record["id"] in existing_ids
        ):
            errors.append(
                f"{prefix}: event id {record['id']!r} already exists in the audio digest track"
            )

    if errors:
        raise AudioDigestWriteError(
            "refusing to write: candidate audio digest events failed validation: " + "; ".join(errors)
        )

    new_envelopes: list[EventEnvelope] = []
    for record in events:
        try:
            new_envelopes.append(EventEnvelope.model_validate(_envelope_kwargs(record)))
        except ValidationError as exc:
            raise AudioDigestWriteError(f"audio digest event fields are invalid: {exc}") from exc

    updated_events = existing_events + new_envelopes

    track_relpath = existing_track.file if existing_track is not None else AUDIO_DIGEST_TRACK_FILE
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name="tracks.audio_digest_events", for_write=True
        )
    except PathSecurityError as exc:
        raise AudioDigestWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    def _rollback_track() -> None:
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)

    _atomic_write(track_path, _serialize_track(updated_events))

    updated_tracks = [t for t in manifest.tracks if t.name != AUDIO_DIGEST_TRACK_NAME]
    updated_tracks.append(
        TrackDescriptor(
            name=AUDIO_DIGEST_TRACK_NAME,
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

    record_type_counts: dict[str, int] = {}
    for envelope in new_envelopes:
        record_type_counts[envelope.type] = record_type_counts.get(envelope.type, 0) + 1

    receipt_path: Path | None = None
    if write_receipt:
        receipt = AudioDigestReceipt(
            operation="append_audio_digest_events",
            status="success",
            tool_name=tool_name,
            tool_version=tool_version,
            output_track=track_relpath,
            event_count=len(new_envelopes),
            record_type_counts=record_type_counts,
            linked_evidence=dict(linked_evidence) if linked_evidence else {},
            warnings=list(warnings),
        )
        try:
            receipt_path = _write_receipt_locked(package_path, receipt, limits=limits)
        except BaseException:
            _atomic_write(manifest_path, original_manifest_bytes)
            _rollback_track()
            raise

    return AudioDigestWriteResult(
        package_path=package_path,
        track_file=track_relpath,
        # `original_track_bytes is None` (not `existing_track is None`)
        # so this stays accurate even if the manifest already had a
        # TrackDescriptor whose file was missing on disk.
        track_created=original_track_bytes is None,
        events_written=len(new_envelopes),
        event_ids=[event.id for event in new_envelopes],
        record_type_counts=record_type_counts,
        receipt_path=receipt_path,
    )


def append_audio_digest_event(
    package_path: Path,
    event: dict[str, Any],
    **kwargs: Any,
) -> AudioDigestWriteResult:
    """Convenience wrapper: append exactly one audio digest event. See `append_audio_digest_events`."""
    return append_audio_digest_events(package_path, [event], **kwargs)
