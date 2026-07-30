"""Tests for Phase 2.11: the analysis adapter dry-run harness
(`analysis_adapter_dry_run.py` + `clulatent analysis dry-run-adapter`).

Non-write-path tests (`load_adapter_result_json`/`dry_run_adapter_result`
called directly, or the CLI with no `--package`) need no real package on
disk and are not ffmpeg-gated. Package-aware tests (nonexistent/
non-directory/locked/writable package status, no-mutation proofs) need a
genuine `.clulatent` package, so they reuse the same `tiny_video`/
`valid_package` fixture pattern established in
`tests/test_analysis_adapters.py` / `tests/test_cli_analysis.py`, and are
ffmpeg-gated the same way.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent.analysis_adapter_dry_run import (
    PACKAGE_STATUS_LOCKED,
    PACKAGE_STATUS_NOT_A_DIRECTORY,
    PACKAGE_STATUS_NOT_FOUND,
    PACKAGE_STATUS_SKIPPED,
    PACKAGE_STATUS_WRITABLE,
    AdapterResultLoadError,
    dry_run_adapter_result,
    dry_run_is_hard_package_error,
    load_adapter_result_json,
)
from clu_latent.analysis_adapters import AdapterDeclaredOutputs, AdapterMetadata, AdapterResult
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


def _write_result_json(tmp_path, data: dict, name: str = "result.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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


# --- dry_run_adapter_result: core validation (no package) -------------------


def test_valid_adapter_result_dry_run_passes():
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    report = dry_run_adapter_result(result)
    assert report.valid is True
    assert report.errors == []
    assert report.lanes == ["scene_events"]
    assert report.event_counts == {"scene_events": 1}
    assert report.total_event_count == 1


def test_valid_multi_lane_adapter_result_dry_run_passes():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={
            "scene_events": [_scene_event()],
            "audio_energy_events": [_audio_event()],
        },
    )
    report = dry_run_adapter_result(result)
    assert report.valid is True
    assert sorted(report.lanes) == ["audio_energy_events", "scene_events"]
    assert report.total_event_count == 2


def test_unsupported_lane_rejected():
    result = AdapterResult(metadata=_metadata(), events_by_lane={"not_a_real_lane": [_scene_event()]})
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("not a supported analysis lane" in err for err in report.errors)


def test_invalid_event_rejected():
    result = AdapterResult(
        metadata=_metadata(), events_by_lane={"scene_events": [_scene_event(t_start_ms="not-an-int")]}
    )
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("t_start_ms must be an integer" in err for err in report.errors)


def test_identity_claim_rejected_for_object_lane():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={
            "object_proposal_events": [
                _object_event(payload={"label": "candidate", "person_name": "John Doe"})
            ]
        },
    )
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("real-person identity claim" in err for err in report.errors)


def test_unsafe_input_source_rejected():
    result = AdapterResult(
        metadata=_metadata(input_sources=["../../etc/passwd"]),
        events_by_lane={"scene_events": [_scene_event()]},
    )
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("input_sources" in err for err in report.errors)


def test_unsafe_output_track_rejected():
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        declared_outputs=AdapterDeclaredOutputs(
            lanes=["scene_events"],
            event_counts={"scene_events": 1},
            output_tracks=["../../etc/escape.jsonl"],
        ),
    )
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("output_tracks" in err for err in report.errors)


def test_missing_adapter_metadata_rejected():
    result = AdapterResult(metadata="not-metadata", events_by_lane={"scene_events": [_scene_event()]})
    report = dry_run_adapter_result(result)
    assert report.valid is False
    assert any("AdapterMetadata instance" in err for err in report.errors)


def test_dry_run_with_no_package_performs_validation_only():
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    report = dry_run_adapter_result(result)
    assert report.package_checked is False
    assert report.package_status == PACKAGE_STATUS_SKIPPED
    assert report.would_write is False
    assert dry_run_is_hard_package_error(report) is False


def test_dry_run_with_nonexistent_package_reports_clean_error(tmp_path):
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    missing = tmp_path / "does_not_exist.clulatent"
    report = dry_run_adapter_result(result, package_path=missing)
    assert report.package_checked is True
    assert report.package_status == PACKAGE_STATUS_NOT_FOUND
    assert report.would_write is False
    assert dry_run_is_hard_package_error(report) is True


def test_dry_run_with_non_directory_package_reports_clean_error(tmp_path):
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("hello", encoding="utf-8")
    report = dry_run_adapter_result(result, package_path=not_a_dir)
    assert report.package_status == PACKAGE_STATUS_NOT_A_DIRECTORY
    assert report.would_write is False
    assert dry_run_is_hard_package_error(report) is True


# --- load_adapter_result_json: safe untrusted-JSON loading -------------------


def test_malformed_json_rejected_cleanly(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(AdapterResultLoadError, match="invalid JSON"):
        load_adapter_result_json(path)


def test_non_object_json_rejected_cleanly(tmp_path):
    path = _write_result_json(tmp_path, None)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(AdapterResultLoadError, match="must be an object"):
        load_adapter_result_json(path)


def test_load_valid_json_round_trips_into_dry_run(tmp_path):
    path = _write_result_json(tmp_path, _valid_result_dict())
    result = load_adapter_result_json(path)
    report = dry_run_adapter_result(result)
    assert report.valid is True
    assert report.adapter_name == "pyscenedetect-bridge"


# --- CLI: clulatent analysis dry-run-adapter (no package needed) ------------


def test_cli_dry_run_adapter_valid_result_passes(tmp_path):
    path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_dry_run_adapter_invalid_result_fails(tmp_path):
    path = _write_result_json(
        tmp_path, _valid_result_dict(events_by_lane={"not_a_real_lane": [_scene_event()]})
    )
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_dry_run_adapter_malformed_json_fails(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 1


def test_cli_dry_run_adapter_non_object_json_fails(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 1


def test_cli_dry_run_adapter_missing_file_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_cli_dry_run_adapter_nonexistent_package_clean_error(tmp_path):
    path = _write_result_json(tmp_path, _valid_result_dict())
    missing_pkg = tmp_path / "does_not_exist.clulatent"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(path), "--package", str(missing_pkg)]
    )
    assert result.exit_code == 1


def test_cli_dry_run_adapter_does_not_write_anything(tmp_path):
    path = _write_result_json(tmp_path, _valid_result_dict())
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 0, result.output
    assert not any(tmp_path.rglob("manifest.json"))
    assert not any(tmp_path.rglob("*.jsonl.tmp"))
    assert not (tmp_path / "receipts").exists()
    assert not (tmp_path / "tracks").exists()


def test_cli_dry_run_adapter_output_includes_lane_counts(tmp_path):
    path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            events_by_lane={
                "scene_events": [_scene_event()],
                "audio_energy_events": [_audio_event(), _audio_event(id="aud_000001")],
            }
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 0, result.output
    assert "scene_events" in result.output
    assert "audio_energy_events" in result.output
    assert "2" in result.output  # audio_energy_events count


def test_cli_dry_run_adapter_escapes_untrusted_markup(tmp_path):
    markup_payload = "[bold red]INJECTED[/bold red]"
    ansi_payload = "\x1b[31mred\x1b[0m"
    path = _write_result_json(
        tmp_path,
        _valid_result_dict(
            metadata={
                "adapter_name": markup_payload + ansi_payload,
                "adapter_version": "1.0.0",
                "tool_name": "pyscenedetect",
                "tool_version": "0.6.0",
            }
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(path)])
    assert result.exit_code == 0, result.output
    assert "\x1b[31m" not in result.output
    assert "\x1b[1;31m" not in result.output
    assert "INJECTED" in result.output


# --- Package-aware tests (ffmpeg-gated: need a real package) ----------------


pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_dry_run_fixture")
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


def test_dry_run_with_valid_unlocked_package_reports_writable(valid_package):
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    report = dry_run_adapter_result(result, package_path=valid_package)
    assert report.package_status == PACKAGE_STATUS_WRITABLE
    assert report.would_write is True
    assert dry_run_is_hard_package_error(report) is False


def test_dry_run_with_locked_package_reports_would_refuse(valid_package):
    lock_mod.create_lock(valid_package)
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    report = dry_run_adapter_result(result, package_path=valid_package)
    assert report.package_status == PACKAGE_STATUS_LOCKED
    assert report.would_write is False
    # Locked is a legitimate, reportable outcome -- not a hard path error.
    assert dry_run_is_hard_package_error(report) is False


def test_cli_dry_run_adapter_locked_package_exit_code_is_explicit(valid_package):
    lock_mod.create_lock(valid_package)
    path = _write_result_json(valid_package.parent, _valid_result_dict())
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(path), "--package", str(valid_package)]
    )
    # Design decision (Phase 2.11): a locked package is validation-level
    # information, not a hard CLI failure -- the adapter result itself is
    # still structurally valid, so the dry run still exits 0.
    assert result.exit_code == 0, result.output
    assert "locked" in result.output.lower()


def test_dry_run_does_not_create_tracks(valid_package):
    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    dry_run_adapter_result(result, package_path=valid_package)
    assert not (valid_package / "tracks" / "scene_events.jsonl").exists()


def test_dry_run_does_not_create_receipts(valid_package):
    result = AdapterResult(
        metadata=_metadata(),
        events_by_lane={"scene_events": [_scene_event()]},
        receipt_metadata={"host": "ci-runner-1"},
    )
    dry_run_adapter_result(result, package_path=valid_package)
    assert not (valid_package / "receipts" / "analyze.jsonl").exists()


def test_dry_run_does_not_mutate_manifest(valid_package):
    manifest_path = valid_package / "manifest.json"
    before = manifest_path.read_bytes()

    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    dry_run_adapter_result(result, package_path=valid_package)

    after = manifest_path.read_bytes()
    assert before == after


def test_dry_run_does_not_mutate_package_validation_state(valid_package):
    before = validate_package(valid_package)
    assert before.valid is True, before.errors

    result = AdapterResult(metadata=_metadata(), events_by_lane={"scene_events": [_scene_event()]})
    dry_run_adapter_result(result, package_path=valid_package)

    after = validate_package(valid_package)
    assert after.valid is True, after.errors
    assert before.errors == after.errors


def test_cli_dry_run_adapter_with_valid_package_reports_writable(valid_package):
    path = _write_result_json(valid_package.parent, _valid_result_dict())
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(path), "--package", str(valid_package)]
    )
    assert result.exit_code == 0, result.output
    assert "would write:     True" in result.output


# --- Regression: existing suites still pass ----------------------------------


def test_existing_analysis_cli_commands_still_work():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "lanes"])
    assert result.exit_code == 0, result.output


def test_existing_package_validation_still_passes(valid_package):
    report = validate_package(valid_package)
    assert report.valid is True, report.errors
