"""Phase 2.15: the first real, non-ML analysis adapter.

Wraps ffmpeg's built-in `scdet` (scene-change detection) video filter to
produce candidate `visual_change_events` -- the first adapter in this
codebase whose output is derived from actually reading a real media
file, rather than fixed fixture data (Phase 2.13) or an unimplemented
protocol (Phase 2.10).

This module implements no ML model, no OCR runtime, no object detector,
no semantic scene understanding, no dynamic plugin loading, no adapter
discovery, no shell execution, no Studio UI, and no CLUBIN. FFmpeg is
invoked exactly as every other ffmpeg call in this codebase already is
-- through `security.subprocess.run_tool` (no `shell=True`, a hard
timeout, bounded stdout/stderr capture) -- and nothing else.

Core principle (unchanged): adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

`scdet` is a low-level, purely numeric signal-processing filter: for
each frame it computes a mean-absolute-frame-difference-derived score
against a user-set threshold (0-100) and logs a `lavfi.scd.score`/
`lavfi.scd.time` line to stderr whenever a candidate scene change is
found. It has no concept of "scene" in any semantic sense (no shot
classification, no object/person awareness, no cut-type judgment) --
every event this module produces is described as a "visual-change
candidate" or "ffmpeg scene-score candidate", never a confirmed cut,
confirmed scene boundary, or semantic scene.

This module never writes to a `.clulatent` package. It only reads a
media file (via ffmpeg) and returns/serializes an in-memory
`AdapterResult` (Phase 2.10 shape). Turning that result into canonical
package tracks is exactly the pre-existing Phase 2.11 dry-run / Phase
2.12 import path -- unchanged by this phase.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .analysis_adapters import AdapterDeclaredOutputs, AdapterMetadata, AdapterResult
from .ffmpeg_tools import get_tool_version
from .security.limits import DEFAULT_LIMITS, Limits
from .security.subprocess import ToolNotFoundError, resolve_tool, run_tool

ADAPTER_NAME = "clulatent-ffmpeg-visual-change-adapter"
ADAPTER_VERSION = "1.0.0"
TOOL_NAME = "ffmpeg"

VISUAL_CHANGE_LANE = "visual_change_events"

# `scdet`'s own AVOption range/default (`ffmpeg -h filter=scdet`): "set
# scene change detect threshold (from 0 to 100) (default 10)".
DEFAULT_THRESHOLD = 10.0
MIN_THRESHOLD = 0.0
MAX_THRESHOLD = 100.0

# A conservative, generous-enough-for-normal-use cap on how many
# candidate events one run will ever produce/keep, so a pathological
# (or maliciously crafted) media file cannot make this adapter emit an
# unbounded number of events. Mirrors the "bounded count" spirit of
# `security/limits.py` without needing a new `Limits` field -- this
# bound is adapter-specific, not a package-wide resource limit.
DEFAULT_MAX_EVENTS = 200
MIN_MAX_EVENTS = 1
MAX_MAX_EVENTS = 2000

# A tiny, fixed, non-zero duration for each point-like visual-change
# candidate. `t_end_ms == t_start_ms` (zero duration) would itself pass
# Phase 2.6 validation (`t_end_ms >= t_start_ms` allows equality), but a
# strictly positive span is unambiguous evidence of "this is a point
# event with a real (if tiny) extent", not an artifact of a boundary
# check.
POINT_EVENT_DURATION_MS = 1

_PRODUCER = {"name": f"adapter:{ADAPTER_NAME}", "version": ADAPTER_VERSION}

_SCDET_LOG_RE = re.compile(
    r"lavfi\.scd\.score:\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*lavfi\.scd\.time:\s*([0-9]+(?:\.[0-9]+)?)"
)

# A fixed, safe, relative-POSIX label used in place of the real
# (frequently absolute, filesystem-specific) input media path. This
# adapter runs outside any `.clulatent` package, so `input_sources`
# cannot safely echo the caller's real path -- `AdapterMetadata.input_
# sources`/the Phase 2.7 receipt writer both validate every entry with
# `security.paths.validate_relative_posix`, which rejects absolute
# paths outright. The real filename is preserved instead inside the
# bounded, non-path-validated `parameters` dict (see
# `build_visual_change_adapter_result`).
_INPUT_SOURCE_LABEL = "external-media/input"


class VisualChangeAdapterError(RuntimeError):
    """Raised for any error building a visual-change adapter result.

    Covers missing ffmpeg, an unreadable/invalid media file, an ffmpeg
    timeout or nonzero exit, and out-of-bounds `threshold`/`max_events`
    parameters -- always as a clean, user-facing message, never a raw
    `subprocess`/`ToolNotFoundError`/`ToolExecutionError` traceback.
    """


def _validate_threshold(threshold: Any) -> float:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise VisualChangeAdapterError(
            f"threshold must be a number, got {type(threshold).__name__}"
        )
    value = float(threshold)
    if not (MIN_THRESHOLD <= value <= MAX_THRESHOLD):
        raise VisualChangeAdapterError(
            f"threshold must be within [{MIN_THRESHOLD}, {MAX_THRESHOLD}], got {value!r}"
        )
    return value


def _validate_max_events(max_events: Any) -> int:
    if isinstance(max_events, bool) or not isinstance(max_events, int):
        raise VisualChangeAdapterError(
            f"max_events must be an integer, got {type(max_events).__name__}"
        )
    if not (MIN_MAX_EVENTS <= max_events <= MAX_MAX_EVENTS):
        raise VisualChangeAdapterError(
            f"max_events must be within [{MIN_MAX_EVENTS}, {MAX_MAX_EVENTS}], got {max_events!r}"
        )
    return max_events


def parse_scdet_log(stderr_text: str) -> list[tuple[int, float]]:
    """Parse ffmpeg `scdet` stderr log lines into `(t_ms, score)` pairs.

    Pure function -- no I/O. `scdet` logs one `lavfi.scd.score: <score>,
    lavfi.scd.time: <seconds>` line per detected candidate; this
    extracts every such pair, in the order they appear (ffmpeg emits
    them in chronological/frame order), converting the time to an
    integer millisecond timestamp. Never raises -- a stderr capture with
    no matching lines simply yields an empty list.
    """
    candidates: list[tuple[int, float]] = []
    for match in _SCDET_LOG_RE.finditer(stderr_text):
        score = float(match.group(1))
        time_s = float(match.group(2))
        t_ms = int(round(time_s * 1000))
        candidates.append((t_ms, score))
    return candidates


def _detect_visual_changes(
    media_path: Path, *, threshold: float, limits: Limits
) -> list[tuple[int, float]]:
    """Run ffmpeg's `scdet` filter over `media_path` and return candidates.

    Raises `VisualChangeAdapterError` (never a raw ffmpeg/subprocess
    exception) if ffmpeg is missing, the run times out, or ffmpeg exits
    nonzero (e.g. because `media_path` is not readable as media).
    """
    try:
        executable = resolve_tool("ffmpeg")
    except ToolNotFoundError as exc:
        raise VisualChangeAdapterError(str(exc)) from exc

    args = [
        "-hide_banner",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(media_path),
        "-vf",
        f"scdet=threshold={threshold}",
        "-f",
        "null",
        "-",
    ]
    result = run_tool(executable, args, timeout_s=limits.ffmpeg_timeout_s, limits=limits)
    if result.timed_out:
        raise VisualChangeAdapterError(
            f"ffmpeg scdet detection timed out after {limits.ffmpeg_timeout_s}s for {media_path}"
        )
    if not result.success:
        raise VisualChangeAdapterError(
            f"ffmpeg scdet detection failed for {media_path}: {result.stderr_tail.strip()}"
        )

    return parse_scdet_log(result.stderr)


def _visual_change_event(index: int, t_ms: int, score: float, *, threshold: float) -> dict[str, Any]:
    confidence = max(0.0, min(1.0, score / 100.0))
    return {
        "id": f"ffmpeg_visual_change_{index:04d}",
        "type": "visual_change",
        "t_start_ms": t_ms,
        "t_end_ms": t_ms + POINT_EVENT_DURATION_MS,
        "producer": dict(_PRODUCER),
        "confidence": confidence,
        "payload": {
            "signal": "ffmpeg_scene_score",
            "score": round(score, 6),
            "threshold": threshold,
            "evidence_label": "ffmpeg visual-change candidate",
            "description": "ffmpeg scene-score candidate",
        },
    }


def build_visual_change_adapter_result(
    media_path: Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_events: int = DEFAULT_MAX_EVENTS,
    limits: Limits = DEFAULT_LIMITS,
) -> AdapterResult:
    """Run ffmpeg's `scdet` filter over `media_path` and return an `AdapterResult`.

    Reads `media_path` via ffmpeg only -- never touches, requires, or
    creates a `.clulatent` package. Every candidate event lands in the
    single `visual_change_events` lane, using deliberately hedged
    language ("visual-change candidate", "ffmpeg scene-score
    candidate") and a `confidence` derived (not measured) from
    `scdet`'s 0-100 score, clamped to `[0.0, 1.0]`. An empty result
    (zero candidates at the given threshold) is a legitimate, valid
    `AdapterResult` -- not an error.

    Raises `VisualChangeAdapterError` for an out-of-bounds `threshold`/
    `max_events`, a missing/non-file `media_path`, missing ffmpeg, or
    an ffmpeg failure/timeout -- always a clean message, never a raw
    exception from `security.subprocess`.
    """
    threshold = _validate_threshold(threshold)
    max_events = _validate_max_events(max_events)

    media_path = Path(media_path)
    if not media_path.exists() or not media_path.is_file():
        raise VisualChangeAdapterError(f"media file does not exist or is not a file: {media_path}")

    candidates = _detect_visual_changes(media_path, threshold=threshold, limits=limits)

    truncated = len(candidates) > max_events
    if truncated:
        candidates = candidates[:max_events]

    events = [
        _visual_change_event(index, t_ms, score, threshold=threshold)
        for index, (t_ms, score) in enumerate(candidates)
    ]

    warnings = [
        "FFmpeg visual-change adapter result: ffmpeg scdet scene-score candidates only. "
        "Not a semantic scene detector, not an ML model, and not confirmed truth about any "
        "real scene change.",
    ]
    metadata_warnings = [
        "This is FFmpeg-detected candidate evidence, not confirmed truth about any real "
        "scene change.",
    ]
    if not events:
        metadata_warnings.append(
            f"No visual-change candidates were detected at threshold={threshold}."
        )
    if truncated:
        metadata_warnings.append(
            f"Candidate events were truncated to max_events={max_events}; "
            f"{len(candidates)} candidate(s) kept, additional detections were dropped."
        )

    metadata = AdapterMetadata(
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        tool_name=TOOL_NAME,
        tool_version=get_tool_version(TOOL_NAME),
        parameters={
            "threshold": threshold,
            "max_events": max_events,
            "filter": "scdet",
            "media_filename": media_path.name,
        },
        input_sources=[_INPUT_SOURCE_LABEL],
        warnings=metadata_warnings,
    )

    declared_outputs = AdapterDeclaredOutputs(
        lanes=[VISUAL_CHANGE_LANE],
        event_counts={VISUAL_CHANGE_LANE: len(events)},
        output_tracks=[VISUAL_CHANGE_LANE],
    )

    return AdapterResult(
        metadata=metadata,
        events_by_lane={VISUAL_CHANGE_LANE: events},
        warnings=warnings,
        receipt_metadata={
            "ffmpeg_filter": "scdet",
            "note": "Generated by the Phase 2.15 ffmpeg visual-change adapter; candidate "
            "evidence only, not confirmed truth.",
        },
        declared_outputs=declared_outputs,
    )


def visual_change_adapter_result_to_dict(result: AdapterResult) -> dict[str, Any]:
    """Serialize an `AdapterResult` into the plain JSON dict shape Phase 2.11/2.12 expect.

    Mirrors `analysis_fixture_adapter.fixture_adapter_result_to_dict`'s
    field layout exactly (each module owns its own small serializer,
    matching this codebase's existing convention), so the output round-
    trips cleanly through both the Phase 2.11 dry-run loader and the
    Phase 2.12 import command.
    """
    metadata = result.metadata
    data: dict[str, Any] = {
        "metadata": {
            "adapter_name": metadata.adapter_name,
            "adapter_version": metadata.adapter_version,
            "tool_name": metadata.tool_name,
            "tool_version": metadata.tool_version,
            "model_name": metadata.model_name,
            "model_version": metadata.model_version,
            "parameters": dict(metadata.parameters),
            "input_sources": list(metadata.input_sources),
            "warnings": list(metadata.warnings),
        },
        "events_by_lane": {
            lane: [dict(event) for event in events] for lane, events in result.events_by_lane.items()
        },
        "warnings": list(result.warnings),
        "receipt_metadata": dict(result.receipt_metadata) if result.receipt_metadata is not None else None,
    }

    if result.declared_outputs is not None:
        data["declared_outputs"] = {
            "lanes": list(result.declared_outputs.lanes),
            "event_counts": dict(result.declared_outputs.event_counts),
            "output_tracks": list(result.declared_outputs.output_tracks),
        }
    else:
        data["declared_outputs"] = None

    return data


def visual_change_adapter_result_json(
    media_path: Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_events: int = DEFAULT_MAX_EVENTS,
    limits: Limits = DEFAULT_LIMITS,
    indent: int = 2,
) -> str:
    """Build a visual-change `AdapterResult` for `media_path` and return it as JSON."""
    result = build_visual_change_adapter_result(
        media_path, threshold=threshold, max_events=max_events, limits=limits
    )
    return json.dumps(visual_change_adapter_result_to_dict(result), indent=indent, sort_keys=True)
