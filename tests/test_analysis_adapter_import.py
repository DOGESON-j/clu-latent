"""Tests for Phase 2.12: analysis adapter result import
(`clulatent analysis import-adapter-result`).

All tests need a genuine `.clulatent` package (import always writes, or
refuses to write, against a real package), so this whole file is
ffmpeg-gated, mirroring `tests/test_analysis_adapters.py` /
`tests/test_analysis_adapter_dry_run.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


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


def _valid_result_dict(**overrides) -> dict:
    data = {
        "metadata": {
            "adapter_name": "pyscenedetect-bridge",
            "adapter_version": "1.0.0",
            "tool_name": "pyscenedetect",
            "tool_version": "0.6.0",
        },
        "events_by_lane": {"scene_events": [_scene_event()]},
    }
    data.update(overrides)
    return data


def _write_result_json(tmp_path, data: dict, name: str = "result.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clulatent_import_fixture")
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


def _run_import(runner, package_path, result_path, *extra_args):
    return runner.invoke(
        app, ["analysis", "import-adapter-result", str(package_path), str(result_path), *extra_args]
    )


# --- Successful imports -------------------------------------------------------


def test_valid_single_lane_import_succeeds(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output
    assert "imported" in result.output.lower()
    assert "scene_events" in result.output


def test_valid_multi_lane_import_succeeds(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            events_by_lane={
                "scene_events": [_scene_event()],
                "audio_energy_events": [_audio_event()],
            }
        ),
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output
    assert "scene_events" in result.output
    assert "audio_energy_events" in result.output
    assert "total events written: 2" in result.output


def test_import_creates_analysis_tracks_via_writer(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output

    events = read_track_file(valid_package / "tracks" / "scene_events.jsonl")
    assert len(events) == 1
    assert events[0].id == "scn_000000"


def test_import_creates_analyze_receipt(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output

    receipts_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipts_path.exists()
    lines = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()]
    assert lines[-1]["adapter_name"] == "pyscenedetect-bridge"


def test_import_updates_manifest_through_writer(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    manifest_path = valid_package / "manifest.json"
    before = json.loads(manifest_path.read_text(encoding="utf-8"))
    track_names_before = {t["name"] for t in before["tracks"]}
    assert "scene_events" not in track_names_before

    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output

    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    track_names_after = {t["name"] for t in after["tracks"]}
    assert "scene_events" in track_names_after


def test_import_no_receipt_flag_skips_receipt(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path, "--no-receipt")
    assert result.exit_code == 0, result.output
    assert not (valid_package / "receipts" / "analyze.jsonl").exists()


def test_successful_import_remains_valid_under_clulatent_validate(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 0, result.output

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


# --- Refusals: malformed input -------------------------------------------------


def test_import_refuses_malformed_json(valid_package, tmp_path):
    result_path = tmp_path / "bad.json"
    result_path.write_text("not json at all", encoding="utf-8")
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


def test_import_refuses_non_object_json(valid_package, tmp_path):
    result_path = tmp_path / "bad.json"
    result_path.write_text("[1, 2, 3]", encoding="utf-8")
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1


def test_import_refuses_unsupported_lane(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path, _valid_result_dict(events_by_lane={"not_a_real_lane": [_scene_event()]})
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "not_a_real_lane.jsonl").exists()


def test_import_refuses_invalid_event(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path,
        _valid_result_dict(events_by_lane={"scene_events": [_scene_event(t_start_ms=-1)]}),
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


def test_import_refuses_identity_claim_in_object_lane(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            events_by_lane={
                "object_proposal_events": [
                    _object_event(payload={"label": "candidate", "person_name": "John Doe"})
                ]
            }
        ),
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "object_proposal_events.jsonl").exists()


def test_import_refuses_unsafe_input_source(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            metadata={
                "adapter_name": "pyscenedetect-bridge",
                "adapter_version": "1.0.0",
                "tool_name": "pyscenedetect",
                "tool_version": "0.6.0",
                "input_sources": ["../../etc/passwd"],
            }
        ),
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


def test_import_refuses_unsafe_output_track(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            declared_outputs={
                "lanes": ["scene_events"],
                "event_counts": {"scene_events": 1},
                "output_tracks": ["../../etc/escape.jsonl"],
            }
        ),
    )
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


# --- Refusals: package problems ------------------------------------------------


def test_import_refuses_nonexistent_package_cleanly(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, missing, result_path)
    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "not a directory" in result.output.lower()


def test_import_refuses_non_directory_package_cleanly(tmp_path):
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("hello", encoding="utf-8")
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, not_a_dir, result_path)
    assert result.exit_code == 1


def test_import_refuses_locked_package(valid_package, tmp_path):
    lock_mod.create_lock(valid_package)
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path)
    assert result.exit_code == 1
    assert "integrity lock" in result.output
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


# --- No-mutation-on-failure proofs ---------------------------------------------


def test_failed_import_does_not_create_tracks(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path, _valid_result_dict(events_by_lane={"scene_events": [_scene_event(t_start_ms=-1)]})
    )
    runner = CliRunner()
    _run_import(runner, valid_package, result_path)
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


def test_failed_import_does_not_create_receipts(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path, _valid_result_dict(events_by_lane={"scene_events": [_scene_event(t_start_ms=-1)]})
    )
    runner = CliRunner()
    _run_import(runner, valid_package, result_path)
    assert not (valid_package / "receipts" / "analyze.jsonl").exists()


def test_failed_import_does_not_mutate_manifest(valid_package, tmp_path):
    result_path = _write_result_json(
        tmp_path, _valid_result_dict(events_by_lane={"scene_events": [_scene_event(t_start_ms=-1)]})
    )
    manifest_path = valid_package / "manifest.json"
    before = manifest_path.read_bytes()

    runner = CliRunner()
    _run_import(runner, valid_package, result_path)

    after = manifest_path.read_bytes()
    assert before == after


# --- Dry-run relationship -------------------------------------------------------


def test_import_dry_run_flag_does_not_mutate(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    manifest_path = valid_package / "manifest.json"
    before = manifest_path.read_bytes()

    runner = CliRunner()
    result = _run_import(runner, valid_package, result_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "nothing written" in result.output.lower()

    after = manifest_path.read_bytes()
    assert before == after
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()
    assert not (valid_package / "receipts" / "analyze.jsonl").exists()


def test_standalone_dry_run_command_still_does_not_mutate(valid_package, tmp_path):
    result_path = _write_result_json(tmp_path, _valid_result_dict())
    manifest_path = valid_package / "manifest.json"
    before = manifest_path.read_bytes()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", "dry-run-adapter", str(result_path), "--package", str(valid_package)],
    )
    assert result.exit_code == 0, result.output

    after = manifest_path.read_bytes()
    assert before == after
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


# --- Regression: existing suites still pass -------------------------------------


def test_existing_analysis_cli_commands_still_work():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "lanes"])
    assert result.exit_code == 0, result.output


def test_existing_package_validation_still_passes(valid_package):
    report = validate_package(valid_package)
    assert report.valid is True, report.errors
