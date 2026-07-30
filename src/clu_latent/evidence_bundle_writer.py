"""Phase 3.17: lock-aware gather + append writer for evidence bundles.

Reads already-computed, already-stored evidence out of a package's
other tracks (keyframes, visual change candidates, changed-region
candidates, audio events, speech events, audio digest events, review
events, analysis lane events) and its receipts, packages small bounded
summaries of what it finds for one requested time range, and appends
one `evidence_bundle` record to `tracks/evidence_bundles.jsonl`.

This module implements no perception. It never runs FFmpeg, Pillow, an
ML model, or an LLM, and it never decides what any of the collected
evidence *means* -- it only counts, indexes, and summarizes records
that some earlier phase already validated and wrote.

Write semantics (see `evidence_bundle.py`'s module docstring for the
rationale): unlike `changed_region_writer.analyze_changed_regions`'s
"recompute the whole track" `analyze` command, `build_evidence_bundle`
mirrors `audio_digest_writer.append_audio_digest_events`'s
append-with-duplicate-check pattern -- one bundle is appended per
`build` call, refusing only if a bundle with the same deterministic id
(derived from the requested time range) already exists, unless
`force=True` (which replaces that one bundle, not the whole track).

Two independent safety mechanisms guard every write, exactly like
`audio_digest_writer.py`:

  - The **integrity lock** (`lock/package.lock.json`): if `lock_status()`
    reports `"locked"`, every write in this module refuses outright.
    There is no `--force` escape hatch for this one.
  - The **operation lock** (`lock/package.operation.lock.json`): guards
    against two concurrent writers racing each other.

Writing is atomic per-file (temp file + fsync + `os.replace`), and up
to three files are written in a fixed order -- evidence bundle track
file, then manifest, then (if requested) the evidence bundle receipt --
with best-effort rollback of every earlier file if a later write fails.

Never touches `sources/`, any other `tracks/*.jsonl`, or
`index/search.sqlite` -- only `tracks/evidence_bundles.jsonl`,
`manifest.json`, and (optionally) `receipts/evidence_bundle.jsonl`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import review_resolver
from . import tracks as tracks_mod
from .analysis_lanes import SUPPORTED_ANALYSIS_LANES
from .audio_digest_retrieval import query_audio_digest_by_time_range
from .changed_region_retrieval import query_changed_regions_by_time_range
from .constants import (
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EVIDENCE_BUNDLE_RECEIPTS_FILE,
    EVIDENCE_BUNDLE_TRACK_FILE,
)
from .evidence_bundle import (
    EVIDENCE_BUNDLE_CAVEATS,
    EVIDENCE_BUNDLE_METHOD,
    EVIDENCE_BUNDLE_RECORD_TYPE,
    EVIDENCE_BUNDLE_TRACK_NAME,
    EVIDENCE_CATEGORIES,
    COVERAGE_KEY_FOR_CATEGORY,
    KNOWN_RECEIPT_FILES,
    validate_evidence_bundle_track,
)
from .event import EventEnvelope
from .keyframe_retrieval import query_keyframes_by_time_range
from .lock import lock_status
from .manifest import Manifest, TrackDescriptor
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package
from .tracks import TrackReadError, read_track_file
from .validate import validate_package
from .visual_change_retrieval import query_visual_change_by_time_range

_ENVELOPE_FIELDS = frozenset({"id", "type", "t_start_ms", "t_end_ms", "producer", "confidence", "payload"})
_RECEIPT_STATUSES = frozenset({"success", "failure"})

_EVIDENCE_NOT_TRUTH_REMINDER = (
    "Evidence bundles are evidence, not truth. They collect existing package "
    "evidence for a time range; they do not establish what happened, who or "
    "what is present, or what any of the collected evidence means."
)

# Salience -> strength bucket thresholds for `audio_digest_segment`
# records, mirroring `visual_change.strength_for_normalized_delta`'s
# low/medium/high split -- audio_digest.py itself has no discrete
# "strength" field (only a continuous `salience` float), so this
# bucketing is this module's own design choice, applied only when
# building the bundle-level summary ref.
_SALIENCE_LOW_THRESHOLD = 0.34
_SALIENCE_HIGH_THRESHOLD = 0.67


class EvidenceBundleWriteError(ValueError):
    """Raised when an evidence bundle (or a receipt) cannot be safely written to a package."""


@dataclass
class EvidenceBundleWriteResult:
    package_path: Path
    track_file: str
    track_created: bool
    bundle_id: str
    evidence_counts: dict[str, int]
    receipt_path: Path | None


@dataclass
class EvidenceBundleReceipt:
    """Documented, code-enforced receipt shape for one evidence bundle write.

    Mirrors `audio_digest_writer.AudioDigestReceipt`'s dataclass-plus-
    `to_dict()` shape.
    """

    operation: str
    status: str  # "success" | "failure"
    tool_name: str
    tool_version: str
    output_track: str
    bundle_id: str | None = None
    evidence_counts: dict[str, int] = field(default_factory=dict)
    coverage: dict[str, bool] = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)
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
            "bundle_id": self.bundle_id,
            "evidence_counts": dict(self.evidence_counts),
            "coverage": dict(self.coverage),
            "missing_evidence": list(self.missing_evidence),
            "warnings": list(self.warnings),
            "failure_details": self.failure_details,
            "evidence_not_truth_reminder": self.evidence_not_truth_reminder,
        }


def build_evidence_bundle_id(start_ms: int, end_ms: int) -> str:
    """Deterministic bundle id for a `[start_ms, end_ms]` range.

    Deterministic (not a random/uuid id) so that calling `build` twice
    for the identical range is recognized as the same bundle -- the
    duplicate-id check below is what "refuses duplicate bundle id or
    overwrite unless --force" (per the Phase 3.17 spec) actually means
    for this lane.
    """
    return f"eb_{start_ms:012d}_{end_ms:012d}"


def _format_timecode(ms: int) -> str:
    total_seconds, millis = divmod(max(ms, 0), 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _overlaps(t_start: Any, t_end: Any, start_ms: int, end_ms: int) -> bool:
    if not isinstance(t_start, int) or not isinstance(t_end, int):
        return False
    if isinstance(t_start, bool) or isinstance(t_end, bool):
        return False
    return t_start <= end_ms and t_end >= start_ms


def _salience_strength(salience: Any) -> str | None:
    if not isinstance(salience, (int, float)) or isinstance(salience, bool):
        return None
    value = float(salience)
    if value < _SALIENCE_LOW_THRESHOLD:
        return "low"
    if value < _SALIENCE_HIGH_THRESHOLD:
        return "medium"
    return "high"


def _review_event_status(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if event_type == "review_approval":
        return "approved"
    if event_type == "review_rejection":
        return "rejected"
    if event_type == "review_correction":
        return "corrected"
    if event_type in ("review_override", "review_status"):
        state = payload.get("review_state")
        if isinstance(state, str) and state:
            return state
    return event_type if isinstance(event_type, str) and event_type else "unknown"


def _keyframe_ref(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "id": event.get("id"),
        "t_ms": event.get("t_start_ms"),
        "image_path": payload.get("path"),
    }


def _visual_change_ref(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
    return {
        "id": event.get("id"),
        "t_start_ms": event.get("t_start_ms"),
        "t_end_ms": event.get("t_end_ms"),
        "strength": payload.get("strength"),
        "normalized_delta": metrics.get("normalized_delta"),
    }


def _changed_region_ref(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "id": event.get("id"),
        "visual_change_id": payload.get("visual_change_id"),
        "t_start_ms": event.get("t_start_ms"),
        "t_end_ms": event.get("t_end_ms"),
        "strength": payload.get("strength"),
        "change_scope": payload.get("change_scope"),
        "region": payload.get("region"),
        "normalized_region": payload.get("normalized_region"),
    }


def _audio_event_ref(event: EventEnvelope) -> dict[str, Any]:
    return {
        "id": event.id,
        "t_start_ms": event.t_start_ms,
        "t_end_ms": event.t_end_ms,
        "kind": event.type,
    }


def _speech_event_ref(event: EventEnvelope) -> dict[str, Any]:
    has_text = event.type == "speech_segment" and bool(event.payload.get("text"))
    return {
        "id": event.id,
        "t_start_ms": event.t_start_ms,
        "t_end_ms": event.t_end_ms,
        "has_text": has_text,
    }


def _audio_digest_ref(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    strength = _salience_strength(payload.get("salience")) if event.get("type") == "audio_digest_segment" else None
    return {
        "id": event.get("id"),
        "t_start_ms": event.get("t_start_ms"),
        "t_end_ms": event.get("t_end_ms"),
        "kind": event.get("type"),
        "strength": strength,
    }


def _review_event_ref(event: EventEnvelope) -> dict[str, Any]:
    return {
        "id": event.id,
        "t_start_ms": event.t_start_ms,
        "t_end_ms": event.t_end_ms,
        "status": _review_event_status({"type": event.type, "payload": event.payload}),
    }


def _analysis_event_ref(lane: str, event: EventEnvelope) -> dict[str, Any]:
    return {
        "lane": lane,
        "id": event.id,
        "t_start_ms": event.t_start_ms,
        "t_end_ms": event.t_end_ms,
        "status": event.type,
    }


def _gather_raw_track_refs(
    package_path: Path,
    manifest: Manifest,
    track_name: str,
    start_ms: int,
    end_ms: int,
    *,
    ref_builder,
    limits: Limits,
) -> list[dict[str, Any]]:
    descriptor = next((t for t in manifest.tracks if t.name == track_name), None)
    if descriptor is None:
        return []
    try:
        track_path = resolve_in_package(package_path, descriptor.file, field_name=f"tracks[{track_name}].file")
    except PathSecurityError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc
    if not track_path.exists():
        raise EvidenceBundleWriteError(f"{track_name} track file listed in manifest is missing: {descriptor.file}")
    try:
        events = read_track_file(track_path, limits=limits)
    except (TrackReadError, JsonlLimitError) as exc:
        raise EvidenceBundleWriteError(f"{descriptor.file}: {exc}") from exc
    return [ref_builder(event) for event in events if _overlaps(event.t_start_ms, event.t_end_ms, start_ms, end_ms)]


def _gather_analysis_event_refs(
    package_path: Path, manifest: Manifest, start_ms: int, end_ms: int, *, limits: Limits
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for descriptor in manifest.tracks:
        if descriptor.name not in SUPPORTED_ANALYSIS_LANES:
            continue
        try:
            track_path = resolve_in_package(
                package_path, descriptor.file, field_name=f"tracks[{descriptor.name}].file"
            )
        except PathSecurityError as exc:
            raise EvidenceBundleWriteError(str(exc)) from exc
        if not track_path.exists():
            raise EvidenceBundleWriteError(
                f"{descriptor.name} track file listed in manifest is missing: {descriptor.file}"
            )
        try:
            events = read_track_file(track_path, limits=limits)
        except (TrackReadError, JsonlLimitError) as exc:
            raise EvidenceBundleWriteError(f"{descriptor.file}: {exc}") from exc
        refs.extend(
            _analysis_event_ref(descriptor.name, event)
            for event in events
            if _overlaps(event.t_start_ms, event.t_end_ms, start_ms, end_ms)
        )
    return refs


def gather_evidence_bundle_payload(
    package_path: Path,
    manifest: Manifest,
    *,
    start_ms: int,
    end_ms: int,
    tool_name: str,
    tool_version: str,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Read every evidence category overlapping `[start_ms, end_ms]` and assemble one bundle payload.

    Read-only: nothing here writes anything. Raises `EvidenceBundleWriteError`
    if a manifest-declared source track is missing/malformed, or a
    keyframe image path is unsafe (via the underlying retrieval
    modules). Absence of an optional track (visual change, changed
    region, audio digest, review, or any analysis lane) is never an
    error -- it simply contributes zero refs to that category.
    """
    try:
        keyframes = [_keyframe_ref(event) for event in query_keyframes_by_time_range(package_path, start_ms, end_ms, limits=limits)]
        visual_change_candidates = [
            _visual_change_ref(event) for event in query_visual_change_by_time_range(package_path, start_ms, end_ms, limits=limits)
        ]
        changed_region_candidates = [
            _changed_region_ref(event)
            for event in query_changed_regions_by_time_range(package_path, start_ms, end_ms, limits=limits)
        ]
        audio_digest_events = [
            _audio_digest_ref(event) for event in query_audio_digest_by_time_range(package_path, start_ms, end_ms, limits=limits)
        ]
    except ValueError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc

    audio_events = _gather_raw_track_refs(
        package_path, manifest, "audio_events", start_ms, end_ms, ref_builder=_audio_event_ref, limits=limits
    )
    speech_events = _gather_raw_track_refs(
        package_path, manifest, "speech_events", start_ms, end_ms, ref_builder=_speech_event_ref, limits=limits
    )

    review_events_all, review_warnings = review_resolver.load_review_events(package_path, limits=limits)
    if review_warnings:
        raise EvidenceBundleWriteError(
            "review_events track could not be read cleanly: " + "; ".join(review_warnings[:5])
        )
    review_events = [
        _review_event_ref(event)
        for event in review_events_all
        if _overlaps(event.t_start_ms, event.t_end_ms, start_ms, end_ms)
    ]

    analysis_events = _gather_analysis_event_refs(package_path, manifest, start_ms, end_ms, limits=limits)

    evidence_refs: dict[str, list[dict[str, Any]]] = {
        "keyframes": keyframes,
        "visual_change_candidates": visual_change_candidates,
        "changed_region_candidates": changed_region_candidates,
        "audio_events": audio_events,
        "speech_events": speech_events,
        "audio_digest_events": audio_digest_events,
        "review_events": review_events,
        "analysis_events": analysis_events,
    }

    evidence_counts = {category: len(evidence_refs[category]) for category in EVIDENCE_CATEGORIES}
    coverage = {
        COVERAGE_KEY_FOR_CATEGORY[category]: evidence_counts[category] > 0 for category in EVIDENCE_CATEGORIES
    }
    missing_evidence = [category for category in EVIDENCE_CATEGORIES if evidence_counts[category] == 0]

    report = validate_package(package_path, limits=limits)
    validation = {
        "package_valid_at_build_time": report.valid,
        "validation_summary": f"{len(report.errors)} error(s), {len(report.warnings)} warning(s) at build time",
    }

    receipt_paths = sorted(path for path in KNOWN_RECEIPT_FILES if (package_path / path).exists())
    receipts_summary = {"receipt_paths": receipt_paths, "receipt_count": len(receipt_paths)}

    return {
        "timecode_start": _format_timecode(start_ms),
        "timecode_end": _format_timecode(end_ms),
        "evidence_refs": evidence_refs,
        "evidence_counts": evidence_counts,
        "coverage": coverage,
        "missing_evidence": missing_evidence,
        "validation": validation,
        "receipts_summary": receipts_summary,
        "caveats": list(EVIDENCE_BUNDLE_CAVEATS),
        "method": EVIDENCE_BUNDLE_METHOD,
        "created_by": f"{tool_name} {tool_version}",
    }


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise EvidenceBundleWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise EvidenceBundleWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvidenceBundleWriteError(f"manifest.json is invalid: {exc}") from exc


def _load_evidence_bundle_track(
    package_path: Path, manifest: Manifest, *, limits: Limits
) -> tuple[list[EventEnvelope], TrackDescriptor | None]:
    track = next((t for t in manifest.tracks if t.name == EVIDENCE_BUNDLE_TRACK_NAME), None)
    if track is None:
        return [], None
    try:
        track_path = resolve_in_package(package_path, track.file, field_name=f"tracks[{track.name}].file")
    except PathSecurityError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc
    if not track_path.exists():
        return [], track
    try:
        events = read_track_file(track_path, limits=limits)
    except (JsonlLimitError, TrackReadError) as exc:
        raise EvidenceBundleWriteError(f"{track.file}: {exc}") from exc
    return events, track


def _atomic_write(path: Path, data: bytes) -> None:
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
    return (json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=False) + "\n").encode("utf-8")


def _serialize_track(events: list[EventEnvelope]) -> bytes:
    ordered = sorted(events, key=lambda e: e.t_start_ms)
    if not ordered:
        return b""
    lines = (json.dumps(event.model_dump(mode="json")) for event in ordered)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _read_receipts(path: Path, *, limits: Limits) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for record in iter_jsonl_bounded(path, limits=limits):
        if record.error is not None:
            raise EvidenceBundleWriteError(f"{path}:{record.lineno}: {record.error}")
        entries.append(record.data)
    return entries


def _serialize_receipts(entries: list[dict[str, Any]]) -> bytes:
    if not entries:
        return b""
    lines = (json.dumps(entry) for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_bounded_receipt_field(value: Any, field_name: str, *, max_bytes: int, required: bool, errors: list[str]) -> None:
    if value is None or value == "":
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if len(value.encode("utf-8")) > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound")


def _check_receipt_bounds(receipt: EvidenceBundleReceipt, limits: Limits) -> list[str]:
    errors: list[str] = []
    if receipt.status not in _RECEIPT_STATUSES:
        errors.append(f"receipt.status must be one of {sorted(_RECEIPT_STATUSES)}, got {receipt.status!r}")
    for field_name, value, required in (
        ("receipt.operation", receipt.operation, True),
        ("receipt.tool_name", receipt.tool_name, True),
        ("receipt.tool_version", receipt.tool_version, True),
        ("receipt.output_track", receipt.output_track, True),
        ("receipt.failure_details", receipt.failure_details, False),
    ):
        max_bytes = limits.max_analysis_text_bytes if field_name == "receipt.failure_details" else limits.max_analysis_label_bytes
        _check_bounded_receipt_field(value, field_name, max_bytes=max_bytes, required=required, errors=errors)
    for index, item in enumerate(receipt.warnings):
        _check_bounded_receipt_field(
            item, f"receipt.warnings[{index}]", max_bytes=limits.max_analysis_text_bytes, required=True, errors=errors
        )
    for field_name, value in (
        ("evidence_counts", receipt.evidence_counts),
        ("coverage", receipt.coverage),
        ("missing_evidence", receipt.missing_evidence),
    ):
        try:
            encoded_len = len(json.dumps(value).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            errors.append(f"receipt.{field_name} is not JSON-serializable: {exc}")
            continue
        if encoded_len > limits.max_analysis_payload_bytes:
            errors.append(f"receipt.{field_name} exceeds the {limits.max_analysis_payload_bytes}-byte bound")
    return errors


def _write_receipt_locked(package_path: Path, receipt: EvidenceBundleReceipt, *, limits: Limits) -> Path:
    receipt_errors = _check_receipt_bounds(receipt, limits)
    if receipt_errors:
        raise EvidenceBundleWriteError("refusing to write receipt: " + "; ".join(receipt_errors))
    try:
        receipt_path = resolve_in_package(
            package_path, EVIDENCE_BUNDLE_RECEIPTS_FILE, field_name="receipts.evidence_bundle", for_write=True
        )
    except PathSecurityError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc
    existing = _read_receipts(receipt_path, limits=limits)
    existing.append(receipt.to_dict())
    _atomic_write(receipt_path, _serialize_receipts(existing))
    return receipt_path


def write_evidence_bundle_receipt(
    package_path: Path,
    receipt: EvidenceBundleReceipt,
    *,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Path:
    """Append one evidence-bundle write receipt on its own (e.g. for a failed build)."""
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise EvidenceBundleWriteError(f"Package not found: {package_path}")
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise EvidenceBundleWriteError(
            "package has a valid integrity lock (lock/package.lock.json); evidence bundle receipt "
            "writes refuse to write against a locked package"
        )
    try:
        with operation_lock(package_path, operation="evidence_bundle", force_stale=force_stale_lock, limits=limits):
            return _write_receipt_locked(package_path, receipt, limits=limits)
    except OperationLockError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc


def build_evidence_bundle(
    package_path: Path,
    *,
    start_ms: int,
    end_ms: int,
    tool_name: str,
    tool_version: str,
    force: bool = False,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> EvidenceBundleWriteResult:
    """Gather evidence for `[start_ms, end_ms]` and append one bundle record.

    Refuses (raises `EvidenceBundleWriteError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - `end_ms < start_ms`.
      - the package has a currently-valid integrity lock.
      - the operation lock cannot be acquired.
      - manifest.json is missing or invalid.
      - a manifest-declared source track is missing/malformed, or a
        keyframe/visual-change/changed-region image path is unsafe.
      - a bundle with the deterministic id for this range already
        exists and `force=False`.
      - the assembled candidate record fails
        `evidence_bundle.validate_evidence_bundle_track`.
      - `write_receipt=True` and the resulting receipt fails its own
        bound checks.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise EvidenceBundleWriteError(f"Package not found: {package_path}")
    if end_ms < start_ms:
        raise EvidenceBundleWriteError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")

    try:
        with operation_lock(package_path, operation="evidence_bundle", force_stale=force_stale_lock, limits=limits):
            return _build_evidence_bundle_locked(
                package_path,
                start_ms=start_ms,
                end_ms=end_ms,
                tool_name=tool_name,
                tool_version=tool_version,
                force=force,
                write_receipt=write_receipt,
                limits=limits,
            )
    except OperationLockError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc


def _build_evidence_bundle_locked(
    package_path: Path,
    *,
    start_ms: int,
    end_ms: int,
    tool_name: str,
    tool_version: str,
    force: bool,
    write_receipt: bool,
    limits: Limits,
) -> EvidenceBundleWriteResult:
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise EvidenceBundleWriteError(
            "package has a valid integrity lock (lock/package.lock.json); evidence bundle write "
            "commands refuse to write against a locked package"
        )

    manifest = _load_manifest(package_path, limits=limits)
    existing_events, existing_track = _load_evidence_bundle_track(package_path, manifest, limits=limits)

    bundle_id = build_evidence_bundle_id(start_ms, end_ms)
    duplicate = next((event for event in existing_events if event.id == bundle_id), None)
    if duplicate is not None and not force:
        raise EvidenceBundleWriteError(
            f"an evidence bundle with id {bundle_id!r} already exists for this time range; pass "
            "force=True to replace it"
        )

    payload = gather_evidence_bundle_payload(
        package_path,
        manifest,
        start_ms=start_ms,
        end_ms=end_ms,
        tool_name=tool_name,
        tool_version=tool_version,
        limits=limits,
    )

    candidate = {
        "id": bundle_id,
        "type": EVIDENCE_BUNDLE_RECORD_TYPE,
        "t_start_ms": start_ms,
        "t_end_ms": end_ms,
        "producer": {"name": tool_name, "version": tool_version},
        "payload": payload,
    }

    errors, warnings = validate_evidence_bundle_track(
        [candidate], package_root=package_path, known_receipt_paths=set(KNOWN_RECEIPT_FILES), limits=limits
    )
    if errors:
        raise EvidenceBundleWriteError(
            "refusing to write: candidate evidence bundle failed validation: " + "; ".join(errors)
        )

    try:
        new_envelope = EventEnvelope.model_validate(candidate)
    except ValidationError as exc:
        raise EvidenceBundleWriteError(f"evidence bundle fields are invalid: {exc}") from exc

    retained_events = [event for event in existing_events if event.id != bundle_id]
    updated_events = retained_events + [new_envelope]

    track_relpath = existing_track.file if existing_track is not None else EVIDENCE_BUNDLE_TRACK_FILE
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name="tracks.evidence_bundles", for_write=True
        )
    except PathSecurityError as exc:
        raise EvidenceBundleWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    def _rollback_track() -> None:
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)

    _atomic_write(track_path, _serialize_track(updated_events))

    updated_tracks = [t for t in manifest.tracks if t.name != EVIDENCE_BUNDLE_TRACK_NAME]
    updated_tracks.append(
        TrackDescriptor(
            name=EVIDENCE_BUNDLE_TRACK_NAME,
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
        receipt = EvidenceBundleReceipt(
            operation="build_evidence_bundle",
            status="success",
            tool_name=tool_name,
            tool_version=tool_version,
            output_track=track_relpath,
            bundle_id=bundle_id,
            evidence_counts=dict(payload["evidence_counts"]),
            coverage=dict(payload["coverage"]),
            missing_evidence=list(payload["missing_evidence"]),
            warnings=list(warnings),
        )
        try:
            receipt_path = _write_receipt_locked(package_path, receipt, limits=limits)
        except BaseException:
            _atomic_write(manifest_path, original_manifest_bytes)
            _rollback_track()
            raise

    return EvidenceBundleWriteResult(
        package_path=package_path,
        track_file=track_relpath,
        track_created=original_track_bytes is None,
        bundle_id=bundle_id,
        evidence_counts=dict(payload["evidence_counts"]),
        receipt_path=receipt_path,
    )
