"""Phase 2.10: analysis adapter contracts.

Defines the safe internal contract a **future** analysis adapter must
follow before its outputs can become CLULatent analysis-lane evidence.
This module implements no video/audio analysis, no FFmpeg tracker, no
OCR runtime, no object detector, no ML model dependency, no semantic
truth generation, no adapter execution runtime, no Studio UI, and no
CLUBIN. It defines no runtime registry, no dynamic plugin loading, no
external-tool import, no subprocess execution, and no installed-adapter
discovery.

Core principle (unchanged since Phase 2.5, restated here because it
governs every check below): adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

What this module adds on top of the phases it builds on:

  - Phase 2.6 (`analysis_lanes.py`) already defines the lane catalog
    and the shape/bound checks a single raw event record or a whole
    lane track must pass. This module does not duplicate any of that
    -- `validate_adapter_result` calls straight through to
    `analysis_lanes.validate_analysis_track` for every lane's events.
  - Phase 2.7 (`analysis_writer.py`) already defines the only safe,
    lock-aware, atomic path onto disk. This module does not duplicate
    any of that either -- `write_adapter_result` (the "writer bridge")
    is a thin per-lane loop over `analysis_writer.append_analysis_events`,
    nothing more. It commits nothing directly; the writer remains the
    sole source of atomicity, manifest updates, receipts, and lock
    enforcement.
  - What is new here is a place for a future adapter implementation to
    describe *itself* (`AdapterMetadata`: identity, versions, declared
    parameters, declared input sources, declared warnings) and its
    *output* (`AdapterResult`: metadata plus `lane -> events`, run-level
    warnings, and optional receipt metadata) as one typed object,
    validated as a whole before anything is proposed for writing.

`adapter_version`: Phase 2.6/2.7's `AnalysisAdapterReceipt` /
`append_analysis_events` have no dedicated `adapter_version` field --
that schema is frozen and this phase does not touch it. Rather than
add one, `write_adapter_result` folds `metadata.adapter_version` into
the existing free-form `environment` dict both already accept, so an
adapter's version is preserved in the written receipt without any
change to frozen Phase 2.6/2.7 code.

Validation here follows the same non-raising `(errors, warnings)`
convention `analysis_lanes.py`/`review.py` already use --
`validate_adapter_metadata`/`validate_adapter_result` never raise, so
a caller collects every problem in one pass. Only the writer bridge
(`write_adapter_result`), which must commit-or-refuse as a single
decision, raises (`AnalysisAdapterError`).

An `AnalysisAdapter` `Protocol` is defined purely as a typing-level
structural contract for what a future adapter implementation looks
like. Nothing in this module ever imports, calls, discovers, or
registers a concrete adapter against it -- see module docstring
above.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .analysis_lanes import (
    ANALYSIS_LANE_NAMES,
    SUPPORTED_ANALYSIS_LANES,
    is_supported_analysis_lane,
    validate_analysis_track,
)
from .analysis_writer import AnalysisWriteError, AnalysisWriteResult, append_analysis_events
from .constants import TRACKS_DIR
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix

_OUTPUT_TRACK_PATHS = frozenset(f"{TRACKS_DIR}/{lane}.jsonl" for lane in ANALYSIS_LANE_NAMES)


class AnalysisAdapterError(ValueError):
    """Raised by `write_adapter_result` when a result fails validation or the writer refuses the write.

    Never raised by `validate_adapter_metadata`/`validate_adapter_result`
    themselves -- those follow the non-raising `(errors, warnings)`
    convention, so a caller can collect every problem in one pass. This
    exception exists only for the write path, which must commit-or-
    refuse as a single decision.
    """


# --- Data shapes -----------------------------------------------------------


@dataclass
class AdapterMetadata:
    """Identity metadata describing one adapter run.

    `adapter_name`/`adapter_version` identify the adapter implementation
    itself; `tool_name`/`tool_version` identify the underlying tool it
    wraps (mirroring `analysis_lanes.AnalysisAdapterReceipt`'s existing
    `adapter_name`/`tool_name`/`tool_version` fields, which this
    dataclass's fields of the same name map onto directly when bridged
    to the Phase 2.7 writer). `model_name`/`model_version` are optional
    (not every adapter wraps an ML model). `parameters` is the adapter's
    own run configuration; `input_sources` are relative-POSIX paths
    (inside the package) the adapter says it read; `warnings` are
    adapter-declared caveats about this run as a whole (distinct from
    `AdapterResult.warnings`, which covers the result/write path).
    """

    adapter_name: str
    adapter_version: str
    tool_name: str
    tool_version: str
    model_name: str | None = None
    model_version: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    input_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class AdapterDeclaredOutputs:
    """An adapter's own self-declared summary of what it produced.

    Distinct from `AdapterResult.events_by_lane` (the actual event
    data): this is a smaller, self-reported summary -- supported lanes,
    counts per lane, and output track references -- that an adapter
    (or a caller inspecting an `AdapterResult`) can use for a quick
    consistency check or a CLI-style report, without needing to walk
    every event. Never trusted on its own; `validate_adapter_result`
    only checks its *shape* (supported lane names, non-negative counts,
    safe track references), not that it matches `events_by_lane`.
    """

    lanes: list[str] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    output_tracks: list[str] = field(default_factory=list)


@dataclass
class AdapterResult:
    """One adapter run's full candidate output, before anything is written.

    `events_by_lane` maps a lane name to a list of raw, untrusted event
    record dicts (the same shape `analysis_lanes.validate_analysis_event`
    checks) -- not `EventEnvelope` instances. `warnings` are run-level
    warnings about the result as a whole (e.g. "N candidate events were
    dropped for exceeding a bound"), distinct from `metadata.warnings`.
    `receipt_metadata` is optional free-form data the adapter wants
    preserved in the written receipt's `environment` field alongside
    `metadata.adapter_version` (see module docstring); `declared_outputs`
    is optional and, if present, is validated for shape only.
    """

    metadata: AdapterMetadata
    events_by_lane: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    receipt_metadata: dict[str, Any] | None = None
    declared_outputs: AdapterDeclaredOutputs | None = None


@runtime_checkable
class AnalysisAdapter(Protocol):
    """Structural shape a future adapter implementation is expected to have.

    Typing-only. Phase 2.10 defines no runtime registry, no dynamic
    plugin loading, no external-tool import, no subprocess execution,
    and no installed-adapter discovery (see module docstring). A
    concrete future adapter class satisfies this protocol simply by
    having a `run(...)` method with this shape; nothing in this module
    ever imports, calls, discovers, or registers a concrete adapter
    against it.
    """

    def run(
        self, *, input_sources: list[str], parameters: dict[str, Any]
    ) -> AdapterResult: ...


# --- Field-level checks (append to an errors list; never raise) ------------


def _check_bounded_string(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool,
    errors: list[str],
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
    if "\x00" in value:
        errors.append(f"{field_name} must not contain NUL bytes")
        return
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound (got {encoded_len} bytes)")


def _check_json_payload_bounds(
    value: Any, field_name: str, *, limits: Limits, errors: list[str]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be a JSON object, got {type(value).__name__}")
        return
    try:
        encoded_len = len(json.dumps(value).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        errors.append(f"{field_name} is not JSON-serializable: {exc}")
        return
    if encoded_len > limits.max_analysis_payload_bytes:
        errors.append(
            f"{field_name} exceeds the {limits.max_analysis_payload_bytes}-byte bound "
            f"(got {encoded_len} bytes)"
        )


def _check_output_track_reference(value: Any, field_name: str, errors: list[str]) -> None:
    """`value` must be a bare supported lane name or its canonical `tracks/<lane>.jsonl` path.

    Both accepted forms are drawn from the same small, fixed whitelist,
    so this single membership check also rules out an absolute path, a
    parent-traversal segment, or a symlink-escape attempt on its own --
    mirrors `analysis_lanes._check_output_track_reference` (Phase 2.9),
    kept as a small local copy here rather than imported, matching this
    codebase's existing convention of each module owning its own
    private `_check_*` helpers.
    """
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if value in SUPPORTED_ANALYSIS_LANES or value in _OUTPUT_TRACK_PATHS:
        return
    errors.append(
        f"{field_name} must be a supported analysis lane name or its "
        f"'{TRACKS_DIR}/<lane>.jsonl' path, got {value!r}"
    )


def _check_input_sources(
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    limits: Limits,
    errors: list[str],
) -> None:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array of strings, got {type(value).__name__}")
        return
    for index, item in enumerate(value):
        item_field = f"{field_name}[{index}]"
        _check_bounded_string(
            item, item_field, max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )
        if not isinstance(item, str):
            continue
        try:
            if package_root is not None:
                resolve_in_package(package_root, item, field_name=item_field)
            else:
                validate_relative_posix(item, field_name=item_field)
        except PathSecurityError as exc:
            errors.append(str(exc))


def _validate_declared_outputs(
    declared: AdapterDeclaredOutputs, *, limits: Limits, errors: list[str]
) -> None:
    if not isinstance(declared.lanes, list):
        errors.append(f"declared_outputs.lanes must be an array, got {type(declared.lanes).__name__}")
    else:
        for index, lane in enumerate(declared.lanes):
            if not is_supported_analysis_lane(lane):
                errors.append(
                    f"declared_outputs.lanes[{index}] is not a supported analysis lane, got {lane!r}"
                )

    if not isinstance(declared.event_counts, dict):
        errors.append(
            f"declared_outputs.event_counts must be a JSON object, got "
            f"{type(declared.event_counts).__name__}"
        )
    else:
        for lane, count in declared.event_counts.items():
            if not is_supported_analysis_lane(lane):
                errors.append(
                    f"declared_outputs.event_counts key {lane!r} is not a supported analysis lane"
                )
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append(
                    f"declared_outputs.event_counts[{lane!r}] must be a non-negative integer, "
                    f"got {count!r}"
                )

    if not isinstance(declared.output_tracks, list):
        errors.append(
            f"declared_outputs.output_tracks must be an array, got "
            f"{type(declared.output_tracks).__name__}"
        )
    else:
        for index, item in enumerate(declared.output_tracks):
            _check_output_track_reference(item, f"declared_outputs.output_tracks[{index}]", errors)


# --- Public validation API ---------------------------------------------------


def validate_adapter_metadata(
    metadata: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one `AdapterMetadata` instance. Never raises.

    `package_root`, if given, additionally resolves `input_sources`
    through `security.paths.resolve_in_package` for full filesystem
    containment + symlink-escape safety; without it, entries still get
    the lexical `validate_relative_posix` check.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(metadata, AdapterMetadata):
        errors.append(f"metadata must be an AdapterMetadata instance, got {type(metadata).__name__}")
        return errors, warnings

    for field_name, value in (
        ("adapter_name", metadata.adapter_name),
        ("adapter_version", metadata.adapter_version),
        ("tool_name", metadata.tool_name),
        ("tool_version", metadata.tool_version),
    ):
        _check_bounded_string(
            value, field_name, max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )

    for field_name, value in (
        ("model_name", metadata.model_name),
        ("model_version", metadata.model_version),
    ):
        if value is not None:
            _check_bounded_string(
                value, field_name, max_bytes=limits.max_analysis_label_bytes, required=False, errors=errors
            )

    _check_json_payload_bounds(metadata.parameters, "parameters", limits=limits, errors=errors)

    _check_input_sources(
        metadata.input_sources, "input_sources", package_root=package_root, limits=limits, errors=errors
    )

    if not isinstance(metadata.warnings, list):
        errors.append(f"warnings must be an array of strings, got {type(metadata.warnings).__name__}")
    else:
        for index, item in enumerate(metadata.warnings):
            _check_bounded_string(
                item,
                f"warnings[{index}]",
                max_bytes=limits.max_analysis_text_bytes,
                required=True,
                errors=errors,
            )

    return errors, warnings


def validate_adapter_result(
    result: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one full `AdapterResult`: metadata plus every lane's events.

    Every non-empty lane's events are checked with
    `analysis_lanes.validate_analysis_track` (unsupported lane names,
    invalid events, identity claims in object/tracking lanes, and
    unsafe payload paths are all caught there -- not duplicated here).
    An empty `events_by_lane` mapping, or a lane mapped to an empty
    list, is not by itself an error at the validation stage (a result
    can legitimately declare "this run found nothing in this lane");
    `write_adapter_result` is what refuses an entirely-empty result for
    the write path. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(result, AdapterResult):
        errors.append(f"result must be an AdapterResult instance, got {type(result).__name__}")
        return errors, warnings

    metadata_errors, metadata_warnings = validate_adapter_metadata(
        result.metadata, package_root=package_root, limits=limits
    )
    errors.extend(metadata_errors)
    warnings.extend(metadata_warnings)

    if not isinstance(result.events_by_lane, dict):
        errors.append(
            f"events_by_lane must be a mapping of lane name to events, got "
            f"{type(result.events_by_lane).__name__}"
        )
    else:
        for lane, events in result.events_by_lane.items():
            if not is_supported_analysis_lane(lane):
                errors.append(
                    f"events_by_lane: {lane!r} is not a supported analysis lane "
                    f"(must be one of {sorted(SUPPORTED_ANALYSIS_LANES)})"
                )
                continue
            if not isinstance(events, list):
                errors.append(
                    f"events_by_lane[{lane!r}] must be an array of events, got {type(events).__name__}"
                )
                continue
            if not events:
                warnings.append(f"events_by_lane[{lane!r}] is empty; nothing to validate or write")
                continue
            lane_errors, lane_warnings = validate_analysis_track(
                events, lane=lane, package_root=package_root, limits=limits
            )
            errors.extend(lane_errors)
            warnings.extend(lane_warnings)

    if not isinstance(result.warnings, list):
        errors.append(f"result warnings must be an array of strings, got {type(result.warnings).__name__}")
    else:
        for index, item in enumerate(result.warnings):
            _check_bounded_string(
                item,
                f"warnings[{index}]",
                max_bytes=limits.max_analysis_text_bytes,
                required=True,
                errors=errors,
            )

    if result.receipt_metadata is not None:
        _check_json_payload_bounds(result.receipt_metadata, "receipt_metadata", limits=limits, errors=errors)

    if result.declared_outputs is not None:
        if not isinstance(result.declared_outputs, AdapterDeclaredOutputs):
            errors.append(
                f"declared_outputs must be an AdapterDeclaredOutputs instance, got "
                f"{type(result.declared_outputs).__name__}"
            )
        else:
            _validate_declared_outputs(result.declared_outputs, limits=limits, errors=errors)

    return errors, warnings


# --- Writer bridge (Phase 2.7 pass-through, adds no atomicity of its own) --


def write_adapter_result(
    package_path: Path,
    result: AdapterResult,
    *,
    write_receipt: bool = True,
    force_stale_lock: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> list[AnalysisWriteResult]:
    """Validate `result`, then append every non-empty lane through the Phase 2.7 writer.

    This is a thin adapter, not a second writer: it validates the full
    result with `validate_adapter_result` (refusing, without writing
    anything, if that reports any error), then calls
    `analysis_writer.append_analysis_events` once per lane that has at
    least one event. `metadata.adapter_version` (a Phase 2.10-only
    concept -- the frozen Phase 2.6/2.7 receipt schema has no such
    field) is folded into the writer's existing free-form `environment`
    kwarg, merged with `result.receipt_metadata` if given, so both are
    preserved in the written receipt without any change to
    `analysis_writer.py` or `analysis_lanes.py`.

    Each lane is written via its own independent call to
    `append_analysis_events` -- this function adds no cross-lane
    atomicity beyond what the writer already provides per call. If
    writing an earlier lane succeeds and a later lane's write then
    fails (e.g. the package became locked in between), the earlier
    lane's write is not rolled back; this mirrors calling
    `append_analysis_events` by hand once per lane.

    Refuses (raises `AnalysisAdapterError`, writes nothing) if:
      - the package does not exist or is not a directory.
      - `validate_adapter_result` reports any error.
      - every lane in `result.events_by_lane` is empty (nothing to
        write).
      - the underlying writer itself refuses (e.g. a locked package,
        an unrecognized field, a duplicate id) -- the writer's
        `AnalysisWriteError` is wrapped as `AnalysisAdapterError` so
        every refusal from this function raises the same exception
        type.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise AnalysisAdapterError(f"Package not found: {package_path}")

    errors, _warnings = validate_adapter_result(result, package_root=package_path, limits=limits)
    if errors:
        raise AnalysisAdapterError(
            "refusing to write adapter result: failed validation: " + "; ".join(errors)
        )

    non_empty_lanes = {
        lane: events for lane, events in result.events_by_lane.items() if events
    }
    if not non_empty_lanes:
        raise AnalysisAdapterError("adapter result has no events in any lane; nothing to write")

    environment: dict[str, Any] = {}
    if result.receipt_metadata:
        environment.update(result.receipt_metadata)
    environment["adapter_version"] = result.metadata.adapter_version

    write_results: list[AnalysisWriteResult] = []
    for lane, events in non_empty_lanes.items():
        try:
            write_results.append(
                append_analysis_events(
                    package_path,
                    lane,
                    events,
                    adapter_name=result.metadata.adapter_name,
                    tool_name=result.metadata.tool_name,
                    tool_version=result.metadata.tool_version,
                    model_name=result.metadata.model_name,
                    model_version=result.metadata.model_version,
                    parameters=result.metadata.parameters,
                    input_sources=result.metadata.input_sources,
                    environment=environment,
                    write_receipt=write_receipt,
                    force_stale_lock=force_stale_lock,
                    limits=limits,
                )
            )
        except AnalysisWriteError as exc:
            raise AnalysisAdapterError(str(exc)) from exc

    return write_results
