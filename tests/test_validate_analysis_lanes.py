"""Tests for Phase 2.9: analysis lane validation wired into
`clulatent validate` / `validate_package`.

Uses the Phase 2.7 writer (`analysis_writer.append_analysis_event`) to
produce a genuinely valid on-disk lane track + manifest entry (and,
where noted, a receipt), then tampers with the raw track/receipt file
content the same way `tests/test_validate_reindex.py` already tampers
`tracks/keyframes.jsonl` -- the writer itself refuses to write an
invalid event, so an on-disk invalid record can only be produced by
editing the file directly after a valid write.

ffmpeg-gated: mirrors test_validate_reindex.py / test_analysis_writer.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import analysis_writer as analysis_writer_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

_WRITER_KWARGS = dict(adapter_name="test-adapter", tool_name="pyscenedetect", tool_version="0.6.0")


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    directory = tmp_path_factory.mktemp("clulatent_validate_analysis_fixture")
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


def _link_event(**overrides):
    event = {
        "id": "lnk_000000",
        "type": "cross_lane_link",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:linker", "version": "0.1.0"},
        "payload": {
            "source_event_ids": ["scn_000000"],
            "target_event_ids": ["obj_000000"],
            "relation_type": "co_occurs",
        },
    }
    event.update(overrides)
    return event


def _tamper_track_file(package_path, lane, event) -> None:
    """Overwrite tracks/<lane>.jsonl with exactly one raw record.

    Bypasses the Phase 2.7 writer's own validation entirely -- the
    only way to get a genuinely invalid record onto disk, since the
    writer refuses to write one.
    """
    track_path = package_path / "tracks" / f"{lane}.jsonl"
    track_path.write_text(json.dumps(event) + "\n", encoding="utf-8")


# --- happy paths --------------------------------------------------------


def test_validate_passes_with_valid_analysis_lane_track(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )

    report = validate_package(valid_package)
    assert report.valid is True, report.errors
    assert report.errors == []


def test_validate_ignores_absent_analysis_lanes(valid_package):
    manifest_before = valid_package / "manifest.json"
    assert "scene_events" not in manifest_before.read_text(encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is True
    assert report.errors == []


def test_validate_handles_analyze_receipt_if_present(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), write_receipt=True, **_WRITER_KWARGS
    )
    assert (valid_package / "receipts" / "analyze.jsonl").exists()

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


# --- lane-specific failure modes -----------------------------------------


def test_validate_fails_with_invalid_analysis_event_timestamp(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(valid_package, "scene_events", _scene_event(t_start_ms="not-an-int"))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("t_start_ms must be an integer" in err for err in report.errors)


def test_validate_fails_with_confidence_out_of_range(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(valid_package, "scene_events", _scene_event(confidence=5.0))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("confidence must be within [0.0, 1.0]" in err for err in report.errors)


def test_validate_fails_with_payload_not_object(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(valid_package, "scene_events", _scene_event(payload="not-an-object"))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("payload must be a JSON object" in err for err in report.errors)


def test_validate_fails_with_unsafe_path_in_payload(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "object_proposal_events", _object_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(
        valid_package,
        "object_proposal_events",
        _object_event(payload={"label": "candidate", "thumbnail_path": "../../etc/escape.jpg"}),
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("thumbnail_path" in err for err in report.errors)


def test_validate_fails_with_identity_claim_in_object_lane(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "object_proposal_events", _object_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(
        valid_package,
        "object_proposal_events",
        _object_event(payload={"label": "candidate", "person_name": "John Doe"}),
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("real-person identity claim" in err for err in report.errors)


def test_validate_fails_with_invalid_cross_lane_link_payload(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "cross_lane_link_events", _link_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(
        valid_package,
        "cross_lane_link_events",
        _link_event(
            payload={
                "source_event_ids": ["scn_000000"],
                "target_event_ids": ["obj_000000"],
                "relation_type": "causes",
            }
        ),
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("asserts causation" in err for err in report.errors)


# --- receipt validation ---------------------------------------------------


def test_validate_fails_cleanly_on_malformed_analyze_receipt(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), write_receipt=True, **_WRITER_KWARGS
    )
    receipts_path = valid_package / "receipts" / "analyze.jsonl"
    bad_receipt = {
        # adapter_name missing entirely -- required field.
        "tool_name": "pyscenedetect",
        "tool_version": "0.6.0",
        "status": "not-a-real-status",
        "output_tracks": ["/etc/passwd"],
    }
    receipts_path.write_text(json.dumps(bad_receipt) + "\n", encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("adapter_name is required" in err for err in report.errors)
    assert any("status must be one of" in err for err in report.errors)
    assert any("output_tracks[0]" in err for err in report.errors)


def test_validate_fails_cleanly_on_malformed_analyze_receipt_jsonl(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), write_receipt=True, **_WRITER_KWARGS
    )
    receipts_path = valid_package / "receipts" / "analyze.jsonl"
    receipts_path.write_text("{not valid json\n", encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("invalid JSON" in err for err in report.errors)


# --- interaction with existing review validation --------------------------


def test_validate_review_track_still_validates_alongside_analysis_lane(valid_package):
    from clu_latent import review_writer as review_writer_mod
    from clu_latent.constants import REVIEW_EVENTS_TRACK_FILE
    from clu_latent.manifest import Manifest
    from clu_latent.tracks import read_track_file

    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest.tracks if t.name == "scene_events")
    target_id = read_track_file(valid_package / track.file)[0].id

    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )

    report = validate_package(valid_package)
    assert report.valid is True, report.errors
    assert (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


# --- CLI surfacing (Phase 2.9 requirement: `clulatent validate` surfaces these) ---


def test_cli_validate_surfaces_analysis_lane_failure(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    _tamper_track_file(valid_package, "scene_events", _scene_event(confidence=5.0))

    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(valid_package)])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "confidence" in result.output


def test_cli_validate_passes_with_valid_analysis_lane_track(valid_package):
    analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )

    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_analysis_commands_still_work_after_validation_wiring(valid_package):
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
            json.dumps(_scene_event()),
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_analysis_validate_file_still_works(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_scene_event()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
