"""Phase 2.13: the first non-ML analysis adapter fixture.

This module implements no video/audio analysis, no FFmpeg tracker, no
OCR runtime, no object detector, no ML model dependency, no semantic
truth generation, no dynamic plugin loading, no adapter discovery, no
subprocess execution, no Studio UI, and no CLUBIN.

It exists to prove -- end to end, without ever touching real media --
that the adapter pipeline built across Phase 2.6 (lane schemas), Phase
2.7 (writer/receipts), Phase 2.9 (package validation), Phase 2.10
(adapter contracts), Phase 2.11 (dry-run harness), and Phase 2.12
(result import) actually works: a tiny, deterministic, hand-written
`AdapterResult` can be built, validated, dry-run, and imported into a
real package, producing real (if synthetic) canonical tracks and a
real receipt.

Core principle (unchanged): adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

Every event this module produces uses deliberately hedged, safe
evidence language ("synthetic scene boundary", "fixture visual
change", "impact-like transient", "cross-lane fixture link") and makes
no real-world truth claim of any kind. This is example/test data, not
an analysis result about any real video or audio.
"""

from __future__ import annotations

import json
from typing import Any

from .analysis_adapters import AdapterDeclaredOutputs, AdapterMetadata, AdapterResult
from .constants import TOOL_VERSION

FIXTURE_ADAPTER_NAME = "clulatent-fixture-adapter"
FIXTURE_ADAPTER_VERSION = "1.0.0"
FIXTURE_TOOL_NAME = "clulatent-fixture"
FIXTURE_TOOL_VERSION = TOOL_VERSION

# A relative-POSIX path the fixture *claims* to have read. It need not
# exist on disk -- `AdapterMetadata.input_sources` is only ever checked
# for path-safety (no traversal, no absolute path, no NUL bytes), never
# for existence -- and this module never reads, writes, or otherwise
# touches this path.
FIXTURE_INPUT_SOURCE = "sources/fixture-input.txt"

_PRODUCER = {"name": f"adapter:{FIXTURE_ADAPTER_NAME}", "version": FIXTURE_ADAPTER_VERSION}

# The four lanes this fixture writes, in the fixed order they are
# generated and written. All four are members of
# `analysis_lanes.ANALYSIS_LANE_NAMES`.
FIXTURE_LANES: tuple[str, ...] = (
    "scene_events",
    "visual_change_events",
    "audio_transient_events",
    "cross_lane_link_events",
)

FIXTURE_SCENE_EVENT_ID = "fixture_scene_000"
FIXTURE_VISUAL_CHANGE_EVENT_ID = "fixture_visual_000"
FIXTURE_AUDIO_TRANSIENT_EVENT_ID = "fixture_audio_000"
FIXTURE_CROSS_LANE_LINK_EVENT_ID = "fixture_link_000"


def _scene_event() -> dict[str, Any]:
    return {
        "id": FIXTURE_SCENE_EVENT_ID,
        "type": "scene_boundary",
        "t_start_ms": 0,
        "t_end_ms": 1000,
        "producer": dict(_PRODUCER),
        "payload": {
            "boundary_kind": "cut",
            "description": "synthetic scene boundary",
        },
    }


def _visual_change_event() -> dict[str, Any]:
    return {
        "id": FIXTURE_VISUAL_CHANGE_EVENT_ID,
        "type": "visual_change",
        "t_start_ms": 1000,
        "t_end_ms": 2000,
        "producer": dict(_PRODUCER),
        "payload": {
            "change_kind": "fixture",
            "description": "fixture visual change",
        },
    }


def _audio_transient_event() -> dict[str, Any]:
    return {
        "id": FIXTURE_AUDIO_TRANSIENT_EVENT_ID,
        "type": "audio_transient",
        "t_start_ms": 2000,
        "t_end_ms": 2200,
        "producer": dict(_PRODUCER),
        "payload": {
            "transient_kind": "impact_like",
            "description": "impact-like transient",
        },
    }


def _cross_lane_link_event() -> dict[str, Any]:
    # References the fixture's own scene/audio event ids. This is safe:
    # `analysis_lanes.validate_analysis_track` checks a cross-lane-link
    # payload's shape (bounded string lists, no self-reference, no
    # forbidden causal relation type) but never requires the referenced
    # ids to already exist as written track records anywhere on disk.
    return {
        "id": FIXTURE_CROSS_LANE_LINK_EVENT_ID,
        "type": "cross_lane_link",
        "t_start_ms": 0,
        "t_end_ms": 2200,
        "producer": dict(_PRODUCER),
        "payload": {
            "source_event_ids": [FIXTURE_SCENE_EVENT_ID],
            "target_event_ids": [FIXTURE_AUDIO_TRANSIENT_EVENT_ID],
            "relation_type": "co_occurs",
            "description": "cross-lane fixture link",
        },
    }


def build_fixture_adapter_result() -> AdapterResult:
    """Return a fresh, deterministic, non-ML `AdapterResult`.

    Every call returns an equivalent (value-equal) result: all ids,
    timestamps, and payloads are fixed constants -- nothing here reads
    the clock, the filesystem, or any external source of randomness.
    """
    events_by_lane: dict[str, list[dict[str, Any]]] = {
        "scene_events": [_scene_event()],
        "visual_change_events": [_visual_change_event()],
        "audio_transient_events": [_audio_transient_event()],
        "cross_lane_link_events": [_cross_lane_link_event()],
    }

    metadata = AdapterMetadata(
        adapter_name=FIXTURE_ADAPTER_NAME,
        adapter_version=FIXTURE_ADAPTER_VERSION,
        tool_name=FIXTURE_TOOL_NAME,
        tool_version=FIXTURE_TOOL_VERSION,
        parameters={"fixture": True, "deterministic": True},
        input_sources=[FIXTURE_INPUT_SOURCE],
        warnings=["This is fixture/example evidence, not a real analysis result."],
    )

    declared_outputs = AdapterDeclaredOutputs(
        lanes=list(FIXTURE_LANES),
        event_counts={lane: len(events) for lane, events in events_by_lane.items()},
        output_tracks=list(FIXTURE_LANES),
    )

    return AdapterResult(
        metadata=metadata,
        events_by_lane=events_by_lane,
        warnings=[
            "Fixture adapter result: deterministic, non-ML, generated for pipeline "
            "testing only. Not a real analysis of any real video or audio."
        ],
        receipt_metadata={
            "fixture": True,
            "note": "Generated by the Phase 2.13 fixture adapter; not real analysis.",
        },
        declared_outputs=declared_outputs,
    )


def fixture_adapter_result_to_dict(result: AdapterResult | None = None) -> dict[str, Any]:
    """Serialize an `AdapterResult` into the plain JSON dict shape Phase 2.11/2.12 expect.

    Defaults to a fresh `build_fixture_adapter_result()` if `result` is
    not given. This mirrors the field names
    `analysis_adapter_dry_run.load_adapter_result_json` reads back out
    of a JSON file, so the output of this function round-trips cleanly
    through both the Phase 2.11 dry-run loader and the Phase 2.12
    import command.
    """
    if result is None:
        result = build_fixture_adapter_result()

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


def fixture_adapter_result_json(*, indent: int = 2) -> str:
    """Return the fixture `AdapterResult`, serialized as a JSON string."""
    return json.dumps(fixture_adapter_result_to_dict(), indent=indent, sort_keys=True)
