"""Phase 2.11: a conservative dry-run harness for already-produced adapter results.

Lets a developer or future adapter author check whether a candidate
`analysis_adapters.AdapterResult` (loaded from a plain JSON file) is
structurally valid, which lanes it would write, how many events per
lane, what warnings/errors exist, and whether a given package would
currently accept the write -- **without writing anything**. This
module implements no video/audio analysis, no FFmpeg tracker, no OCR
runtime, no object detector, no ML model dependency, no semantic truth
generation, no dynamic plugin loading, no adapter discovery, no
subprocess execution, no Studio UI, and no CLUBIN.

Core principle (unchanged): adapters produce evidence, not truth.
Detected does not mean trusted. Generated does not mean canonical.
Canonical means validated, bounded, receipted, reviewable, and
lockable. A *dry run* checks whether a candidate result is eligible to
become canonical -- it never makes it canonical itself. Only
`analysis_writer.append_analysis_events` (Phase 2.7), reached either
directly, through `analysis_adapters.write_adapter_result` (Phase
2.10), or through `clulatent analysis append`/`append-file` (Phase
2.8), ever writes a track, a manifest, or a receipt.

No duplication: this module reuses `analysis_adapters.validate_adapter_result`
(Phase 2.10) for every shape/bound/path-safety/identity-claim check,
which itself reuses `analysis_lanes.validate_analysis_track` (Phase
2.6) for every lane's events. It reuses `lock.lock_status` (unchanged
since Phase 1.7.5) for package lock detection, and
`security.jsonl.read_bytes_bounded` for safely reading the candidate
JSON file. It adds no new validation rule of its own beyond "is a
package a safe target to write into right now" (exists, is a
directory, is not locked) -- a read-only filesystem check, not a data
validation rule.

Package-root handling: `resolve_in_package` (used transitively by
`validate_adapter_result` for path-safety checks) calls
`Path(package_root).resolve(strict=True)`, which raises a raw
`FileNotFoundError` for a package that does not exist. To keep this
module's own errors clean (never a raw traceback), a `package_path` is
only ever passed through to `validate_adapter_result` as
`package_root` once it has already been confirmed to exist and be a
directory; a missing or non-directory package is instead reported as
its own `package_status`, and validation still runs without a
`package_root` (lexical-only path safety), exactly like
`clulatent analysis validate-file` already does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analysis_adapters import AdapterDeclaredOutputs, AdapterMetadata, AdapterResult, validate_adapter_result
from .lock import lock_status
from .security.jsonl import JsonlLimitError, read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits

# --- Package write-status vocabulary ----------------------------------------

PACKAGE_STATUS_SKIPPED = "skipped"  # no --package given; write checks not attempted
PACKAGE_STATUS_NOT_FOUND = "not_found"
PACKAGE_STATUS_NOT_A_DIRECTORY = "not_a_directory"
PACKAGE_STATUS_LOCKED = "locked"  # would refuse: valid integrity lock present
PACKAGE_STATUS_LOCK_INVALID = "lock_invalid"  # lock files present but fail verification
PACKAGE_STATUS_LOCK_PARTIAL = "lock_partial"  # only one of lock.json/lock.sha256 present
PACKAGE_STATUS_WRITABLE = "writable"  # exists, is a directory, unlocked

_PACKAGE_HARD_ERROR_STATUSES = frozenset({PACKAGE_STATUS_NOT_FOUND, PACKAGE_STATUS_NOT_A_DIRECTORY})


class AdapterResultLoadError(ValueError):
    """Raised when a candidate adapter-result JSON file cannot be parsed/loaded.

    Covers a missing/oversized file, invalid JSON, and a non-object
    top-level JSON value -- never a raw `json.JSONDecodeError` or
    `OSError` escapes `load_adapter_result_json`.
    """


@dataclass
class DryRunReport:
    """Everything a dry run reports, never anything it wrote."""

    adapter_name: str | None
    adapter_version: str | None
    tool_name: str | None
    tool_version: str | None
    model_name: str | None
    model_version: str | None
    lanes: list[str] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    total_event_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    valid: bool = False
    package_path: Path | None = None
    package_checked: bool = False
    package_status: str = PACKAGE_STATUS_SKIPPED
    would_write: bool = False


# --- Loading a candidate AdapterResult from untrusted JSON ------------------


def _metadata_from_dict(raw: Any) -> AdapterMetadata:
    if not isinstance(raw, dict):
        raw = {}
    return AdapterMetadata(
        adapter_name=raw.get("adapter_name"),
        adapter_version=raw.get("adapter_version"),
        tool_name=raw.get("tool_name"),
        tool_version=raw.get("tool_version"),
        model_name=raw.get("model_name"),
        model_version=raw.get("model_version"),
        parameters=raw.get("parameters", {}),
        input_sources=raw.get("input_sources", []),
        warnings=raw.get("warnings", []),
    )


def _declared_outputs_from_dict(raw: Any) -> Any:
    """Return an `AdapterDeclaredOutputs` for a dict, or the raw value unchanged.

    A non-dict, non-None `raw` is returned as-is so
    `validate_adapter_result`'s own `isinstance(..., AdapterDeclaredOutputs)`
    check reports it cleanly, rather than this loader silently
    swallowing a shape problem the validator already knows how to
    report.
    """
    if raw is None or isinstance(raw, dict) is False:
        return raw
    return AdapterDeclaredOutputs(
        lanes=raw.get("lanes", []),
        event_counts=raw.get("event_counts", {}),
        output_tracks=raw.get("output_tracks", []),
    )


def _adapter_result_from_dict(data: dict[str, Any]) -> AdapterResult:
    """Build a candidate `AdapterResult` from an untrusted parsed-JSON dict.

    Never validates or raises on bad shapes -- every field is passed
    through as close to verbatim as possible (only a missing key gets
    a default; a present-but-wrong-typed value is preserved unchanged)
    so `validate_adapter_result` reports the *specific* problem, the
    same way a hand-typed `AdapterResult` would be reported.
    """
    return AdapterResult(
        metadata=_metadata_from_dict(data.get("metadata")),
        events_by_lane=data.get("events_by_lane", {}),
        warnings=data.get("warnings", []),
        receipt_metadata=data.get("receipt_metadata"),
        declared_outputs=_declared_outputs_from_dict(data.get("declared_outputs")),
    )


def load_adapter_result_json(path: Path, *, limits: Limits = DEFAULT_LIMITS) -> AdapterResult:
    """Safely parse `path` as one candidate `AdapterResult`.

    Raises `AdapterResultLoadError` (never a raw exception) for an
    oversized file, invalid JSON, or a non-object top-level JSON value.
    Does not validate the result's contents -- call
    `dry_run_adapter_result`/`validate_adapter_result` for that.
    """
    path = Path(path)
    try:
        raw_bytes = read_bytes_bounded(path, max_bytes=limits.max_manifest_bytes, field_name=str(path))
    except JsonlLimitError as exc:
        raise AdapterResultLoadError(str(exc)) from exc

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdapterResultLoadError(f"{path}: not valid UTF-8: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterResultLoadError(f"{path}: invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise AdapterResultLoadError(
            f"{path}: top-level JSON value must be an object, got {type(data).__name__}"
        )

    return _adapter_result_from_dict(data)


# --- The dry run itself ------------------------------------------------------


def dry_run_adapter_result(
    result: AdapterResult,
    *,
    package_path: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> DryRunReport:
    """Validate `result` and report what it would do -- writes nothing, ever.

    Reuses `analysis_adapters.validate_adapter_result` for every shape/
    bound/path-safety/identity-claim check (Phase 2.6/2.10) -- no
    validation rule is duplicated here. If `package_path` is given and
    is an existing directory, its lock status
    (`lock.lock_status`, Phase 1.7.5+) is additionally checked and the
    same directory is passed through as `package_root` so path-like
    fields get full filesystem-containment/symlink-escape checking, not
    just the lexical check; if it does not exist or is not a directory,
    that is reported as its own `package_status` and validation runs
    without a `package_root` (lexical-only), never touching the
    filesystem beyond the existence/directory check itself.

    `report.would_write` is only ever True when the result validates
    with zero errors, at least one event is present across every lane,
    and the package (if checked) is an existing, unlocked directory --
    i.e. exactly the conditions under which
    `analysis_writer.append_analysis_events` would proceed rather than
    refuse, without this function ever calling it.
    """
    package_checked = package_path is not None
    resolved_package_path = Path(package_path) if package_path is not None else None
    package_status = PACKAGE_STATUS_SKIPPED
    package_root_for_validation: Path | None = None

    if package_checked:
        if not resolved_package_path.exists():
            package_status = PACKAGE_STATUS_NOT_FOUND
        elif not resolved_package_path.is_dir():
            package_status = PACKAGE_STATUS_NOT_A_DIRECTORY
        else:
            package_root_for_validation = resolved_package_path
            status, _report = lock_status(resolved_package_path, limits=limits)
            if status == "locked":
                package_status = PACKAGE_STATUS_LOCKED
            elif status == "lock-invalid":
                package_status = PACKAGE_STATUS_LOCK_INVALID
            elif status == "lock-partial":
                package_status = PACKAGE_STATUS_LOCK_PARTIAL
            else:
                package_status = PACKAGE_STATUS_WRITABLE

    errors, warnings = validate_adapter_result(
        result, package_root=package_root_for_validation, limits=limits
    )

    lanes: list[str] = []
    event_counts: dict[str, int] = {}
    total_event_count = 0
    events_by_lane = result.events_by_lane if isinstance(result, AdapterResult) else None
    if isinstance(events_by_lane, dict):
        for lane in sorted(events_by_lane):
            events = events_by_lane[lane]
            count = len(events) if isinstance(events, list) else 0
            lanes.append(lane)
            event_counts[lane] = count
            total_event_count += count

    metadata = result.metadata if isinstance(result, AdapterResult) else None
    metadata = metadata if isinstance(metadata, AdapterMetadata) else None

    result_valid = not errors
    would_write = (
        result_valid
        and total_event_count > 0
        and package_status == PACKAGE_STATUS_WRITABLE
    )

    return DryRunReport(
        adapter_name=metadata.adapter_name if metadata is not None else None,
        adapter_version=metadata.adapter_version if metadata is not None else None,
        tool_name=metadata.tool_name if metadata is not None else None,
        tool_version=metadata.tool_version if metadata is not None else None,
        model_name=metadata.model_name if metadata is not None else None,
        model_version=metadata.model_version if metadata is not None else None,
        lanes=lanes,
        event_counts=event_counts,
        total_event_count=total_event_count,
        errors=errors,
        warnings=warnings,
        valid=result_valid,
        package_path=resolved_package_path,
        package_checked=package_checked,
        package_status=package_status,
        would_write=would_write,
    )


def dry_run_is_hard_package_error(report: DryRunReport) -> bool:
    """True if `report.package_status` reflects a package-path problem, not a lock.

    A locked/lock-invalid/lock-partial package is a legitimate, expected
    dry-run outcome (the write would currently be refused, but nothing
    about the *path itself* is wrong); a missing or non-directory
    package path is a caller mistake. Callers (e.g. the CLI) can use
    this to decide exit-code behavior without re-deriving the status set.
    """
    return report.package_status in _PACKAGE_HARD_ERROR_STATUSES
