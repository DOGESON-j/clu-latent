"""Tests for Phase 2.10: analysis adapter contracts (`analysis_adapters.py`).

Non-write-path tests (`validate_adapter_metadata`/`validate_adapter_result`)
need no real package on disk and are not ffmpeg-gated. Writer-bridge
tests (`write_adapter_result`) need a genuine `.clulatent` package, so
they reuse the same `tiny_video`/`valid_package` fixture pattern
established in `tests/test_analysis_writer.py` /
`tests/test_validate_analysis_lanes.py`, and are ffmpeg-gated the same
way.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import analysis_adapters as adapters_mod
from clu_latent import lock as lock_mod
from clu_latent.analysis_adapters import (
    AdapterDeclaredOutputs,
    AdapterMetadata,
    AdapterResult,
    AnalysisAdapterError,
    validate_adapter_metadata,
    validate_adapter_result,
    write_adapter_result,
)
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _metadata(**overrides) -> AdapterMetadata:
    kwargs = dict(
        adapter_name="pyscenedetect-bridge",
        adapter_version="1.0.0",
        tool_name="pyscenedetect",
        tool_version="0.6.0",
    )
    kwargs.update(overrides)
    return AdapterMetadata(**kwargs)


def _scene_event(**overrides):
    event = {
        "id": "scn_000000",
        "type": "scene_boundary",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:pyscenedetect", "version": "0.6.0"},
        "payload": {"boundary_kind": "cut"},
    }
    event.update(overrides)
    return event


def _object_event(**overrides):
    event = {
        "id": "obj_000000",
        "type": "object_proposal",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:opencv", "version": "4.9.0"},
        "payload": {"label": "ball-like candidate"},
    }
    event.update(overrides)
    return event


def _audio_event(**overrides):
    event = {
        "id": "aud_000000",
        "type": "audio_energy",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:librosa", "version": "0.10.0"},
        "payload": {"rms": 0.5},
    }
    event.update(overrides)
    return event


# --- AdapterMetadata validation ---------------------------------------------


def test_valid_adapter_metadata_accepted():
    errors, warnings = validate_adapter_metadata(_metadata())
    assert errors == []


def test_adapter_metadata_bad_type_rejected():
    errors, _warnings = validate_adapter_metadata("not-metadata")
    assert any("AdapterMetadata instance" in err for err in errors)


def test_oversized_metadata_string_rejected():
    errors, _warnings = validate_adapter_metadata(_metadata(adapter_name="x" * 1000))
    assert any("adapter_name" in err and "bound" in err for err in errors)


def test_oversized_parameters_payload_rejected():
    errors, _warnings = validate_adapter_metadata(
        _metadata(parameters={"blob": "x" * 20_000})
    )
    assert any("parameters" in err and "bound" in err for err in errors)


def test_unsafe_input_source_rejected():
    errors, _warnings = validate_adapter_metadata(
        _metadata(input_sources=["../../etc/passwd"])
    )
    assert any("input_sources" in err for err in errors)


def test_absolute_input_source_rejected():
    errors, _warnings = validate_adapter_metadata(_metadata(input_sources=["/etc/passwd"]))
    assert any("input_sources" in err for err in errors)


# --- AdapterResult validation ------------------------------------------------


def test_valid_adapter_result_multiple_lanes_validates():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={
            "scene_events": [_scene_event()],
            "audio_energy_events": [_audio_event()],
        },
    )
    errors, warnings = validate_adapter_result(result)
    assert errors == [], errors


def test_unsupported_lane_rejected():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"not_a_real_lane": [_scene_event()]},
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("not_a_real_lane" in err and "not a supported analysis lane" in err for err in errors)


def test_invalid_event_rejected_through_lane_validation():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event(t_start_ms="not-an-int")]},
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("t_start_ms must be an integer" in err for err in errors)


def test_identity_claim_rejected_for_object_lane():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={
            "object_proposal_events": [
                _object_event(payload={"label": "candidate", "person_name": "John Doe"})
            ]
        },
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("real-person identity claim" in err for err in errors)


def test_empty_lane_events_produce_warning_not_error():
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": []})
    errors, warnings = validate_adapter_result(result)
    assert errors == []
    assert any("empty" in warning for warning in warnings)


def test_declared_outputs_unsafe_output_track_rejected():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        declared_outputs=AdapterDeclaredOutputs(
            lanes=["scene_events"],
            event_counts={"scene_events": 1},
            output_tracks=["../../etc/escape.jsonl"],
        ),
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("output_tracks" in err for err in errors)


def test_declared_outputs_unsupported_lane_rejected():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        declared_outputs=AdapterDeclaredOutputs(lanes=["not_a_lane"]),
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("declared_outputs.lanes" in err for err in errors)


def test_declared_outputs_valid_accepted():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        declared_outputs=AdapterDeclaredOutputs(
            lanes=["scene_events"],
            event_counts={"scene_events": 1},
            output_tracks=["scene_events"],
        ),
    )
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


def test_oversized_receipt_metadata_rejected():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        receipt_metadata={"blob": "x" * 20_000},
    )
    errors, _warnings = validate_adapter_result(result)
    assert any("receipt_metadata" in err and "bound" in err for err in errors)


# --- Writer bridge (ffmpeg-gated: needs a real package) ---------------------


pytestmark_writer = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_adapters_fixture")
    video_path = directory / "tiny.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return video_path


@pytest.fixture
def valid_package(tmp_path, tiny_video):
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


@pytestmark_writer
def test_writer_bridge_writes_through_phase_2_7_writer(valid_package):
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
    )
    write_results = write_adapter_result(valid_package, result)
    assert len(write_results) == 1
    assert write_results[0].lane == "scene_events"
    assert write_results[0].events_written == 1

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


@pytestmark_writer
def test_writer_bridge_writes_multiple_lanes(valid_package):
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={
            "scene_events": [_scene_event()],
            "audio_energy_events": [_audio_event()],
        },
    )
    write_results = write_adapter_result(valid_package, result)
    lanes_written = {r.lane for r in write_results}
    assert lanes_written == {"scene_events", "audio_energy_events"}


@pytestmark_writer
def test_writer_bridge_rejects_empty_result(valid_package):
    result = AdapterResult(metadata=_metadata(), events_by_lane={})
    with pytest.raises(AnalysisAdapterError, match="no events"):
        write_adapter_result(valid_package, result)


@pytestmark_writer
def test_writer_bridge_rejects_result_with_only_empty_lanes(valid_package):
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": []})
    with pytest.raises(AnalysisAdapterError, match="no events"):
        write_adapter_result(valid_package, result)


@pytestmark_writer
def test_writer_bridge_refuses_locked_package(valid_package):
    lock_mod.create_lock(valid_package)
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
    )
    with pytest.raises(AnalysisAdapterError, match="valid integrity lock"):
        write_adapter_result(valid_package, result)


@pytestmark_writer
def test_writer_bridge_rejects_invalid_result_before_writing(valid_package):
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event(confidence=5.0)]},
    )
    with pytest.raises(AnalysisAdapterError, match="failed validation"):
        write_adapter_result(valid_package, result)
    # nothing was written -- no scene_events track exists on disk
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


@pytestmark_writer
def test_writer_bridge_preserves_receipt_metadata(valid_package):
    result = AdapterResult(
        metadata=_metadata(adapter_version="2.3.1"),
        events_by_lane={"scene_events": [_scene_event()]},
        receipt_metadata={"host": "ci-runner-1"},
    )
    write_adapter_result(valid_package, result)

    receipts_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipts_path.exists()
    import json as json_mod

    lines = [json_mod.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["environment"]["adapter_version"] == "2.3.1"
    assert lines[-1]["environment"]["host"] == "ci-runner-1"
    assert lines[-1]["adapter_name"] == "pyscenedetect-bridge"


@pytestmark_writer
def test_writer_bridge_nonexistent_package_gives_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
    )
    with pytest.raises(AnalysisAdapterError, match="Package not found"):
        write_adapter_result(missing, result)


# --- Regression: existing analysis CLI / package validation still pass -----


@pytestmark_writer
def test_existing_analysis_cli_commands_still_work(valid_package):
    import json as json_mod

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "lanes"])
    assert result.exit_code == 0, result.output
    assert "scene_events" in result.output

    result = runner.invoke(
        app,
        [
            "analysis",
            "append",
            str(valid_package),
            "scene_events",
            "--event-json",
            json_mod.dumps(_scene_event()),
        ],
    )
    assert result.exit_code == 0, result.output


@pytestmark_writer
def test_existing_package_validation_still_passes(valid_package):
    report = validate_package(valid_package)
    assert report.valid is True, report.errors
