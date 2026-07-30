"""Tests for Phase 2.13: the first non-ML analysis adapter fixture.

Covers the fixture module directly (determinism, shape, safety) and,
where a real `.clulatent` package is needed to prove the fixture result
can actually be dry-run and imported end-to-end, mirrors the
ffmpeg-gated `tiny_video`/`valid_package` pattern already used in
`tests/test_analysis_adapter_import.py` and
`tests/test_analysis_adapter_dry_run.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.analysis_adapter_dry_run import dry_run_adapter_result
from clu_latent.analysis_adapters import (
    AdapterResult,
    validate_adapter_result,
    write_adapter_result,
)
from clu_latent.analysis_fixture_adapter import (
    FIXTURE_ADAPTER_NAME,
    FIXTURE_INPUT_SOURCE,
    FIXTURE_LANES,
    build_fixture_adapter_result,
    fixture_adapter_result_json,
    fixture_adapter_result_to_dict,
)
from clu_latent.analysis_lanes import (
    ANALYSIS_LANE_NAMES,
    FORBIDDEN_IDENTITY_FIELDS,
    IDENTITY_FLAG_FIELDS,
)
from clu_latent.cli import app
from clu_latent.security.paths import validate_relative_posix

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Module-level / pure-Python checks (no package, no ffmpeg needed) --------


def test_module_imports_cleanly():
    import clu_latent.analysis_fixture_adapter  # noqa: F401


def test_build_fixture_adapter_result_returns_adapter_result():
    result = build_fixture_adapter_result()
    assert isinstance(result, AdapterResult)
    assert result.metadata.adapter_name == FIXTURE_ADAPTER_NAME


def test_generated_result_is_deterministic_as_dataclass():
    first = fixture_adapter_result_to_dict(build_fixture_adapter_result())
    second = fixture_adapter_result_to_dict(build_fixture_adapter_result())
    assert first == second


def test_generated_dict_is_deterministic():
    assert fixture_adapter_result_to_dict() == fixture_adapter_result_to_dict()


def test_generated_json_is_deterministic():
    assert fixture_adapter_result_json() == fixture_adapter_result_json()


def test_generated_result_validates_with_adapter_contracts():
    result = build_fixture_adapter_result()
    errors, warnings = validate_adapter_result(result)
    assert errors == []


def test_generated_result_dry_runs_successfully():
    result = build_fixture_adapter_result()
    report = dry_run_adapter_result(result, package_path=None)
    assert report.valid is True
    assert report.errors == []


def test_generated_events_use_supported_lanes_only():
    result = build_fixture_adapter_result()
    for lane in result.events_by_lane:
        assert lane in ANALYSIS_LANE_NAMES
    for lane in FIXTURE_LANES:
        assert lane in ANALYSIS_LANE_NAMES


def test_generated_events_avoid_identity_claims():
    result = build_fixture_adapter_result()
    for events in result.events_by_lane.values():
        for event in events:
            payload = event["payload"]
            for forbidden_field in FORBIDDEN_IDENTITY_FIELDS:
                assert forbidden_field not in payload
            for flag_field in IDENTITY_FLAG_FIELDS:
                assert flag_field not in payload


def test_generated_input_sources_are_safe():
    result = build_fixture_adapter_result()
    for source in result.metadata.input_sources:
        # Raises ValueError on an unsafe relative path; a clean return
        # means it is safe.
        validate_relative_posix(source, field_name="input_sources")
    assert FIXTURE_INPUT_SOURCE in result.metadata.input_sources


def test_fixture_events_use_safe_evidence_language():
    result = build_fixture_adapter_result()
    descriptions = [
        event["payload"].get("description", "")
        for events in result.events_by_lane.values()
        for event in events
    ]
    assert "synthetic scene boundary" in descriptions
    assert "fixture visual change" in descriptions
    assert "impact-like transient" in descriptions
    assert "cross-lane fixture link" in descriptions


def test_cross_lane_link_event_does_not_self_reference():
    result = build_fixture_adapter_result()
    link_event = result.events_by_lane["cross_lane_link_events"][0]
    link_id = link_event["id"]
    payload = link_event["payload"]
    assert link_id not in payload["source_event_ids"]
    assert link_id not in payload["target_event_ids"]


def test_declared_outputs_shape_is_consistent():
    result = build_fixture_adapter_result()
    declared = result.declared_outputs
    assert declared is not None
    assert set(declared.lanes) == set(FIXTURE_LANES)
    for lane, count in declared.event_counts.items():
        assert count == len(result.events_by_lane[lane])


# --- CLI: generate-fixture-adapter-result ------------------------------------


def test_cli_generate_fixture_emits_valid_json_to_stdout():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["metadata"]["adapter_name"] == FIXTURE_ADAPTER_NAME


def test_cli_generate_fixture_stdout_has_no_control_sequences():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


def test_cli_generate_fixture_writes_valid_json_to_output_file(tmp_path):
    output_path = tmp_path / "fixture-result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-fixture-adapter-result", "--output", str(output_path)]
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists()
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["metadata"]["adapter_name"] == FIXTURE_ADAPTER_NAME


def test_cli_generate_fixture_refuses_to_overwrite_without_force(tmp_path):
    output_path = tmp_path / "fixture-result.json"
    output_path.write_text("existing content", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-fixture-adapter-result", "--output", str(output_path)]
    )
    assert result.exit_code != 0
    assert output_path.read_text(encoding="utf-8") == "existing content"


def test_cli_generate_fixture_overwrites_with_force(tmp_path):
    output_path = tmp_path / "fixture-result.json"
    output_path.write_text("existing content", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", "generate-fixture-adapter-result", "--output", str(output_path), "--force"],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["metadata"]["adapter_name"] == FIXTURE_ADAPTER_NAME


def test_cli_generate_fixture_rejects_nonexistent_output_directory(tmp_path):
    output_path = tmp_path / "does-not-exist" / "fixture-result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-fixture-adapter-result", "--output", str(output_path)]
    )
    assert result.exit_code != 0
    assert not output_path.exists()


def test_cli_generate_fixture_does_not_touch_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert result.exit_code == 0, result.output
    assert list(tmp_path.iterdir()) == []


# --- End-to-end: dry-run + import into a real package (ffmpeg-gated) --------


pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_fixture_adapter")
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
    from clu_latent.ingest import ingest_video

    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


@pytestmark_e2e
def test_fixture_result_dry_runs_against_real_package(valid_package):
    result = build_fixture_adapter_result()
    report = dry_run_adapter_result(result, package_path=valid_package)
    assert report.valid is True
    assert report.would_write is True


@pytestmark_e2e
def test_fixture_result_imports_successfully_via_writer(valid_package):
    result = build_fixture_adapter_result()
    write_results = write_adapter_result(valid_package, result)
    lanes_written = {write_result.lane for write_result in write_results}
    assert lanes_written == set(FIXTURE_LANES)


@pytestmark_e2e
def test_fixture_import_creates_expected_analysis_tracks(valid_package):
    from clu_latent.tracks import read_track_file

    result = build_fixture_adapter_result()
    write_adapter_result(valid_package, result)

    for lane in FIXTURE_LANES:
        track_path = valid_package / "tracks" / f"{lane}.jsonl"
        assert track_path.exists()
        events = read_track_file(track_path)
        assert len(events) == 1


@pytestmark_e2e
def test_fixture_import_creates_analyze_receipt(valid_package):
    result = build_fixture_adapter_result()
    write_adapter_result(valid_package, result)

    receipts_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipts_path.exists()
    lines = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()]
    assert any(line["adapter_name"] == FIXTURE_ADAPTER_NAME for line in lines)


@pytestmark_e2e
def test_package_validates_after_fixture_import(valid_package):
    from clu_latent.validate import validate_package

    result = build_fixture_adapter_result()
    write_adapter_result(valid_package, result)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


@pytestmark_e2e
def test_fixture_result_imports_successfully_via_cli(valid_package, tmp_path):
    result_path = tmp_path / "fixture-result.json"
    result_path.write_text(fixture_adapter_result_json(), encoding="utf-8")

    runner = CliRunner()
    generated = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert generated.exit_code == 0, generated.output

    result = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert result.exit_code == 0, result.output
    assert "imported" in result.output.lower()
    for lane in FIXTURE_LANES:
        assert lane in result.output


@pytestmark_e2e
def test_fixture_result_dry_run_cli_succeeds(tmp_path):
    result_path = tmp_path / "fixture-result.json"
    result_path.write_text(fixture_adapter_result_json(), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "dry-run-adapter", str(result_path)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
