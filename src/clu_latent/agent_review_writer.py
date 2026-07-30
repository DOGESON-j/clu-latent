"""Phase 3.17: lock-aware run + receipt + claim-file writer for agent review v0.

Reads exactly one already-built evidence bundle record (via
`evidence_bundle_retrieval.get_evidence_bundle_by_id`), optionally reads
a small, bounded, untrusted claim file, calls
`agent_review.compute_agent_review_findings` (a pure, deterministic,
rule-based function -- no external model, no network, no perception),
and appends one `agent_review_event` record to
`tracks/agent_review_events.jsonl`.

Write semantics mirror `evidence_bundle_writer.build_evidence_bundle`:
one review is appended per `run` call, refusing only if a review with
the same deterministic id (derived from the bundle id) already exists,
unless `force=True` (which replaces that one review, not the whole
track). The same two independent safety mechanisms guard every write
here as everywhere else in Phase 3.17 -- the integrity lock (no
`--force` escape hatch) and the operation lock (concurrent-writer
guard) -- and writes are atomic per-file (temp file + fsync +
`os.replace`) with best-effort rollback of every earlier file if a
later write fails.

Claim files (see `_load_and_sanitize_claims`) are treated as untrusted
local input: bounded in size, bounded in item count, bounded in
per-field size, restricted to a closed key whitelist, and rejected
outright (raising `AgentReviewWriteError`, writing nothing) if
malformed -- they are never passed to `compute_agent_review_findings`
unsanitized.

Never touches `sources/`, any other `tracks/*.jsonl`, or
`index/search.sqlite` -- only `tracks/agent_review_events.jsonl`,
`manifest.json`, and (optionally) `receipts/agent_review.jsonl`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .agent_review import (
    AGENT_REVIEW_CAVEATS,
    AGENT_REVIEW_METHOD,
    AGENT_REVIEW_RECORD_TYPE,
    AGENT_REVIEW_TRACK_NAME,
    DEFAULT_MAX_CLAIMS_PER_REQUEST,
    DEFAULT_MAX_EVIDENCE_REF_IDS_PER_FINDING,
    compute_agent_review_findings,
    validate_agent_review_track,
)
from .constants import (
    AGENT_REVIEW_RECEIPTS_FILE,
    AGENT_REVIEW_TRACK_FILE,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
)
from .event import EventEnvelope
from .evidence_bundle_retrieval import get_evidence_bundle_by_id
from .lock import lock_status
from .manifest import Manifest, TrackDescriptor
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded, read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package
from .tracks import TrackReadError, read_track_file

_RECEIPT_STATUSES = frozenset({"success", "failure"})

_REVIEW_NOT_TRUTH_REMINDER = (
    "Agent review is review of evidence, not invention of truth. It reports what "
    "package evidence supports, what is missing, and what needs human review; it "
    "never establishes what happened, who or what is present, or scene meaning."
)

# A claim file is a single small JSON document (not JSONL), so it is
# read as a bounded whole file, mirroring how manifest.json is read.
DEFAULT_MAX_CLAIM_FILE_BYTES = 256 * 1024  # 256 KiB

_CLAIM_FILE_KEYS = frozenset({"claims"})
_CLAIM_KEYS = frozenset({"id", "text", "claim_type", "evidence_ref_ids"})
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class AgentReviewWriteError(ValueError):
    """Raised when an agent review (or a receipt, or a claim file) cannot be safely written/read."""


@dataclass
class AgentReviewWriteResult:
    package_path: Path
    track_file: str
    track_created: bool
    review_id: str
    evidence_bundle_id: str
    review_status: str
    receipt_path: Path | None


@dataclass
class AgentReviewReceipt:
    """Documented, code-enforced receipt shape for one agent review write.

    Mirrors `evidence_bundle_writer.EvidenceBundleReceipt`'s
    dataclass-plus-`to_dict()` shape.
    """

    operation: str
    status: str  # "success" | "failure"
    tool_name: str
    tool_version: str
    output_track: str
    review_id: str | None = None
    evidence_bundle_id: str | None = None
    review_status: str | None = None
    findings_count: int = 0
    unsupported_claims_count: int = 0
    warnings: list[str] = field(default_factory=list)
    failure_details: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    review_not_truth_reminder: str = _REVIEW_NOT_TRUTH_REMINDER

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "status": self.status,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "output_track": self.output_track,
            "review_id": self.review_id,
            "evidence_bundle_id": self.evidence_bundle_id,
            "review_status": self.review_status,
            "findings_count": self.findings_count,
            "unsupported_claims_count": self.unsupported_claims_count,
            "warnings": list(self.warnings),
            "failure_details": self.failure_details,
            "review_not_truth_reminder": self.review_not_truth_reminder,
        }


def build_agent_review_id(bundle_id: str) -> str:
    """Deterministic review id for one evidence bundle.

    Deterministic (not a random/uuid id) so that calling `run` twice
    for the same bundle is recognized as the same review -- exactly
    the same "refuses duplicate id unless force=True" convention
    `evidence_bundle_writer.build_evidence_bundle_id` already
    establishes for this phase.
    """
    return f"ar_{bundle_id}"


# --- Claim file loading (bounded, untrusted local JSON) ---------------------


def _check_claim_bounded_string(
    value: Any, field_name: str, *, max_bytes: int, required: bool, errors: list[str]
) -> None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if required and value == "":
        errors.append(f"{field_name} must not be empty")
        return
    if _CONTROL_CHAR_RE.search(value):
        errors.append(f"{field_name} must not contain control characters")
        return
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound (got {encoded_len} bytes)")


def _sanitize_one_claim(raw_claim: Any, index: int, *, limits: Limits, errors: list[str]) -> dict[str, Any] | None:
    prefix = f"claims[{index}]"
    if not isinstance(raw_claim, dict):
        errors.append(f"{prefix} must be an object, got {type(raw_claim).__name__}")
        return None

    for key in raw_claim:
        if key not in _CLAIM_KEYS:
            errors.append(f"{prefix}: unexpected field {key!r}")

    before = len(errors)
    _check_claim_bounded_string(
        raw_claim.get("id"), f"{prefix}.id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )
    _check_claim_bounded_string(
        raw_claim.get("text"),
        f"{prefix}.text",
        max_bytes=limits.max_review_text_bytes,
        required=True,
        errors=errors,
    )
    _check_claim_bounded_string(
        raw_claim.get("claim_type"),
        f"{prefix}.claim_type",
        max_bytes=limits.max_analysis_label_bytes,
        required=False,
        errors=errors,
    )

    evidence_ref_ids = raw_claim.get("evidence_ref_ids", [])
    if not isinstance(evidence_ref_ids, list):
        errors.append(f"{prefix}.evidence_ref_ids must be an array, got {type(evidence_ref_ids).__name__}")
    elif len(evidence_ref_ids) > DEFAULT_MAX_EVIDENCE_REF_IDS_PER_FINDING:
        errors.append(
            f"{prefix}.evidence_ref_ids exceeds the {DEFAULT_MAX_EVIDENCE_REF_IDS_PER_FINDING}-item bound"
        )
    else:
        for ref_index, ref_id in enumerate(evidence_ref_ids):
            _check_claim_bounded_string(
                ref_id,
                f"{prefix}.evidence_ref_ids[{ref_index}]",
                max_bytes=limits.max_analysis_id_bytes,
                required=True,
                errors=errors,
            )

    if len(errors) != before:
        return None

    return {
        "id": raw_claim["id"],
        "text": raw_claim["text"],
        "claim_type": raw_claim.get("claim_type") or "",
        "evidence_ref_ids": list(evidence_ref_ids),
    }


def load_and_sanitize_claims(claim_file: Path | str, *, limits: Limits = DEFAULT_LIMITS) -> list[dict[str, Any]]:
    """Read, validate, and sanitize a local claim file. Raises `AgentReviewWriteError` on any problem.

    Treats the file as untrusted: bounded whole-file size, top-level
    shape restricted to `{"claims": [...]}`, claim count bounded to
    `DEFAULT_MAX_CLAIMS_PER_REQUEST`, every claim restricted to the
    closed `id`/`text`/`claim_type`/`evidence_ref_ids` key set with
    each field individually bounded, and duplicate claim ids within the
    file rejected. Never partially accepts a malformed file -- any
    error anywhere in the file causes the whole load to fail closed.
    """
    claim_path = Path(claim_file)
    if not claim_path.exists() or not claim_path.is_file():
        raise AgentReviewWriteError(f"claim file not found: {claim_path}")

    try:
        raw_bytes = read_bytes_bounded(claim_path, max_bytes=DEFAULT_MAX_CLAIM_FILE_BYTES, field_name="claim file")
    except JsonlLimitError as exc:
        raise AgentReviewWriteError(str(exc)) from exc

    try:
        document = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentReviewWriteError(f"claim file is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise AgentReviewWriteError(f"claim file must contain a JSON object, got {type(document).__name__}")
    for key in document:
        if key not in _CLAIM_FILE_KEYS:
            raise AgentReviewWriteError(f"claim file: unexpected top-level field {key!r}")

    raw_claims = document.get("claims")
    if not isinstance(raw_claims, list):
        raise AgentReviewWriteError(f"claim file 'claims' must be an array, got {type(raw_claims).__name__}")
    if len(raw_claims) > DEFAULT_MAX_CLAIMS_PER_REQUEST:
        raise AgentReviewWriteError(
            f"claim file 'claims' exceeds the {DEFAULT_MAX_CLAIMS_PER_REQUEST}-item bound "
            f"(got {len(raw_claims)} items)"
        )

    errors: list[str] = []
    sanitized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_claim in enumerate(raw_claims):
        claim = _sanitize_one_claim(raw_claim, index, limits=limits, errors=errors)
        if claim is None:
            continue
        if claim["id"] in seen_ids:
            errors.append(f"claims[{index}]: duplicate claim id {claim['id']!r} within this file")
            continue
        seen_ids.add(claim["id"])
        sanitized.append(claim)

    if errors:
        raise AgentReviewWriteError("claim file failed validation: " + "; ".join(errors[:20]))

    return sanitized


# --- Track / manifest / receipt IO (mirrors evidence_bundle_writer.py) ------


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise AgentReviewWriteError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except (JsonlLimitError, ValidationError) as exc:
        raise AgentReviewWriteError(f"manifest.json is invalid: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AgentReviewWriteError(f"manifest.json is invalid: {exc}") from exc


def _load_agent_review_track(
    package_path: Path, manifest: Manifest, *, limits: Limits
) -> tuple[list[EventEnvelope], TrackDescriptor | None]:
    track = next((t for t in manifest.tracks if t.name == AGENT_REVIEW_TRACK_NAME), None)
    if track is None:
        return [], None
    try:
        track_path = resolve_in_package(package_path, track.file, field_name=f"tracks[{track.name}].file")
    except PathSecurityError as exc:
        raise AgentReviewWriteError(str(exc)) from exc
    if not track_path.exists():
        return [], track
    try:
        events = read_track_file(track_path, limits=limits)
    except (JsonlLimitError, TrackReadError) as exc:
        raise AgentReviewWriteError(f"{track.file}: {exc}") from exc
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
            raise AgentReviewWriteError(f"{path}:{record.lineno}: {record.error}")
        entries.append(record.data)
    return entries


def _serialize_receipts(entries: list[dict[str, Any]]) -> bytes:
    if not entries:
        return b""
    lines = (json.dumps(entry) for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_bounded_receipt_field(
    value: Any, field_name: str, *, max_bytes: int, required: bool, errors: list[str]
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


def _check_receipt_bounds(receipt: AgentReviewReceipt, limits: Limits) -> list[str]:
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
        max_bytes = (
            limits.max_analysis_text_bytes if field_name == "receipt.failure_details" else limits.max_analysis_label_bytes
        )
        _check_bounded_receipt_field(value, field_name, max_bytes=max_bytes, required=required, errors=errors)
    for index, item in enumerate(receipt.warnings):
        _check_bounded_receipt_field(
            item, f"receipt.warnings[{index}]", max_bytes=limits.max_analysis_text_bytes, required=True, errors=errors
        )
    return errors


def _write_receipt_locked(package_path: Path, receipt: AgentReviewReceipt, *, limits: Limits) -> Path:
    receipt_errors = _check_receipt_bounds(receipt, limits)
    if receipt_errors:
        raise AgentReviewWriteError("refusing to write receipt: " + "; ".join(receipt_errors))
    try:
        receipt_path = resolve_in_package(
            package_path, AGENT_REVIEW_RECEIPTS_FILE, field_name="receipts.agent_review", for_write=True
        )
    except PathSecurityError as exc:
        raise AgentReviewWriteError(str(exc)) from exc
    existing = _read_receipts(receipt_path, limits=limits)
    existing.append(receipt.to_dict())
    _atomic_write(receipt_path, _serialize_receipts(existing))
    return receipt_path


def write_agent_review_receipt(
    package_path: Path,
    receipt: AgentReviewReceipt,
    *,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> Path:
    """Append one agent-review write receipt on its own (e.g. for a failed run)."""
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AgentReviewWriteError(f"Package not found: {package_path}")
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AgentReviewWriteError(
            "package has a valid integrity lock (lock/package.lock.json); agent review receipt "
            "writes refuse to write against a locked package"
        )
    try:
        with operation_lock(package_path, operation="agent_review", force_stale=force_stale_lock, limits=limits):
            return _write_receipt_locked(package_path, receipt, limits=limits)
    except OperationLockError as exc:
        raise AgentReviewWriteError(str(exc)) from exc


# --- The public entrypoint ---------------------------------------------------


def run_agent_review(
    package_path: Path,
    bundle_id: str,
    *,
    tool_name: str,
    tool_version: str,
    claim_file: Path | str | None = None,
    force: bool = False,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> AgentReviewWriteResult:
    """Review one evidence bundle and append one `agent_review_event` record.

    Refuses (raises `AgentReviewWriteError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - the bundle `bundle_id` does not exist in `tracks/evidence_bundles.jsonl`.
      - `claim_file` is given but is missing, oversized, malformed, or
        fails claim-shape validation (see `load_and_sanitize_claims`).
      - the package has a currently-valid integrity lock.
      - the operation lock cannot be acquired.
      - manifest.json is missing or invalid.
      - a review with the deterministic id for this bundle already
        exists and `force=False`.
      - the assembled candidate record fails
        `agent_review.validate_agent_review_track`.
      - `write_receipt=True` and the resulting receipt fails its own
        bound checks.

    Never calls an external model, never makes a network call -- the
    review content itself comes entirely from
    `agent_review.compute_agent_review_findings`, a pure, deterministic
    function of the bundle's already-computed evidence counts.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AgentReviewWriteError(f"Package not found: {package_path}")

    bundle = get_evidence_bundle_by_id(package_path, bundle_id, limits=limits)
    if bundle is None:
        raise AgentReviewWriteError(f"no evidence bundle with id {bundle_id!r} found in this package")

    claims: list[dict[str, Any]] | None = None
    if claim_file is not None:
        claims = load_and_sanitize_claims(claim_file, limits=limits)

    try:
        with operation_lock(package_path, operation="agent_review", force_stale=force_stale_lock, limits=limits):
            return _run_agent_review_locked(
                package_path,
                bundle=bundle,
                claims=claims,
                tool_name=tool_name,
                tool_version=tool_version,
                force=force,
                write_receipt=write_receipt,
                limits=limits,
            )
    except OperationLockError as exc:
        raise AgentReviewWriteError(str(exc)) from exc


def _run_agent_review_locked(
    package_path: Path,
    *,
    bundle: dict[str, Any],
    claims: list[dict[str, Any]] | None,
    tool_name: str,
    tool_version: str,
    force: bool,
    write_receipt: bool,
    limits: Limits,
) -> AgentReviewWriteResult:
    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise AgentReviewWriteError(
            "package has a valid integrity lock (lock/package.lock.json); agent review write "
            "commands refuse to write against a locked package"
        )

    manifest = _load_manifest(package_path, limits=limits)
    existing_events, existing_track = _load_agent_review_track(package_path, manifest, limits=limits)

    bundle_id = bundle["id"]
    review_id = build_agent_review_id(bundle_id)
    duplicate = next((event for event in existing_events if event.id == review_id), None)
    if duplicate is not None and not force:
        raise AgentReviewWriteError(
            f"an agent review with id {review_id!r} already exists for bundle {bundle_id!r}; pass "
            "force=True to replace it"
        )

    computed = compute_agent_review_findings(bundle, claims=claims)

    payload = {
        "evidence_bundle_id": bundle_id,
        "review_status": computed["review_status"],
        "findings": computed["findings"],
        "evidence_present": computed["evidence_present"],
        "evidence_missing": computed["evidence_missing"],
        "unsupported_claims": computed["unsupported_claims"],
        "recommended_next_step": computed["recommended_next_step"],
        "confidence": computed["confidence"],
        "caveats": list(AGENT_REVIEW_CAVEATS),
        "method": AGENT_REVIEW_METHOD,
        "created_by": f"{tool_name} {tool_version}",
    }

    candidate = {
        "id": review_id,
        "type": AGENT_REVIEW_RECORD_TYPE,
        "t_start_ms": bundle["t_start_ms"],
        "t_end_ms": bundle["t_end_ms"],
        "producer": {"name": tool_name, "version": tool_version},
        "payload": payload,
    }

    errors, warnings = validate_agent_review_track(
        [candidate], bundles_by_id={bundle_id: bundle}, limits=limits
    )
    if errors:
        raise AgentReviewWriteError(
            "refusing to write: candidate agent review failed validation: " + "; ".join(errors)
        )

    try:
        new_envelope = EventEnvelope.model_validate(candidate)
    except ValidationError as exc:
        raise AgentReviewWriteError(f"agent review fields are invalid: {exc}") from exc

    retained_events = [event for event in existing_events if event.id != review_id]
    updated_events = retained_events + [new_envelope]

    track_relpath = existing_track.file if existing_track is not None else AGENT_REVIEW_TRACK_FILE
    try:
        track_path = resolve_in_package(
            package_path, track_relpath, field_name="tracks.agent_review_events", for_write=True
        )
    except PathSecurityError as exc:
        raise AgentReviewWriteError(str(exc)) from exc

    original_track_bytes: bytes | None = None
    if track_path.exists():
        original_track_bytes = track_path.read_bytes()

    def _rollback_track() -> None:
        if original_track_bytes is None:
            track_path.unlink(missing_ok=True)
        else:
            _atomic_write(track_path, original_track_bytes)

    _atomic_write(track_path, _serialize_track(updated_events))

    updated_tracks = [t for t in manifest.tracks if t.name != AGENT_REVIEW_TRACK_NAME]
    updated_tracks.append(
        TrackDescriptor(
            name=AGENT_REVIEW_TRACK_NAME,
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
        receipt = AgentReviewReceipt(
            operation="run_agent_review",
            status="success",
            tool_name=tool_name,
            tool_version=tool_version,
            output_track=track_relpath,
            review_id=review_id,
            evidence_bundle_id=bundle_id,
            review_status=computed["review_status"],
            findings_count=len(computed["findings"]),
            unsupported_claims_count=len(computed["unsupported_claims"]),
            warnings=list(warnings),
        )
        try:
            receipt_path = _write_receipt_locked(package_path, receipt, limits=limits)
        except BaseException:
            _atomic_write(manifest_path, original_manifest_bytes)
            _rollback_track()
            raise

    return AgentReviewWriteResult(
        package_path=package_path,
        track_file=track_relpath,
        track_created=original_track_bytes is None,
        review_id=review_id,
        evidence_bundle_id=bundle_id,
        review_status=computed["review_status"],
        receipt_path=receipt_path,
    )
