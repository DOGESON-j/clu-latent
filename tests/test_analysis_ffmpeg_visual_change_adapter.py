"""Tests for Phase 2.15: the first real, non-ML analysis adapter.

Mirrors the structure of `tests/test_analysis_fixture_adapter.py`:
pure/no-media tests first (module shape, parameter validation, the
`parse_scdet_log` pure parser), then ffmpeg-gated tests that generate
tiny deterministic media in `tmp_path` and exercise the real adapter
end to end (generate -> dry-run -> import -> validate), following the
same `tiny_video`/`valid_package` fixture pattern already used across
`tests/test_analysis_adapter_import.py`, `tests/test_analysis_adapter_
dry_run.py`, and `tests/test_analysis_fixture_adapter.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.analysis_adapter_dry_run import dry_run_adapter_result
from clu_latent.analysis_adapters import validate_adapter_result, write_adapter_result
from clu_latent.analysis_ffmpeg_visual_change_adapter import (
    ADAPTER_NAME,
    DEFAULT_MAX_EVENTS,
    DEFAULT_THRESHOLD,
    MAX_MAX_EVENTS,
    MAX_THRESHOLD,
    MIN_MAX_EVENTS,
    MIN_THRESHOLD,
    VISUAL_CHANGE_LANE,
    VisualChangeAdapterError,
    build_visual_change_adapter_result,
    parse_scdet_log,
    visual_change_adapter_result_to_dict,
)
from clu_latent.analysis_lanes import (
    ANALYSIS_LANE_NAMES,
    FORBIDDEN_IDENTITY_FIELDS,
    IDENTITY_FLAG_FIELDS,
)
from clu_latent.cli import app
from clu_latent.security.subprocess import reset_tool_cache

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Module-level / pure-Python checks (no media, no ffmpeg needed) ---------


def test_module_imports_cleanly():
    import clu_latent.analysis_ffmpeg_visual_change_adapter  # noqa: F401


def test_parse_scdet_log_extracts_score_and_time():
    stderr_text = (
        "[scdet @ 0x1] lavfi.scd.score: 15.625, lavfi.scd.time: 1\n"
        "[scdet @ 0x1] lavfi.scd.score: 42.5, lavfi.scd.time: 2.333333\n"
    )
    candidates = parse_scdet_log(stderr_text)
    assert candidates == [(1000, 15.625), (2333, 42.5)]


def test_parse_scdet_log_empty_on_no_matches():
    assert parse_scdet_log("no scene changes here\njust noise\n") == []


def test_invalid_threshold_below_range_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, threshold=MIN_THRESHOLD - 1)


def test_invalid_threshold_above_range_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, threshold=MAX_THRESHOLD + 1)


def test_invalid_threshold_wrong_type_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, threshold="not-a-number")  # type: ignore[arg-type]


def test_invalid_max_events_below_range_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, max_events=MIN_MAX_EVENTS - 1)


def test_invalid_max_events_above_range_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, max_events=MAX_MAX_EVENTS + 1)


def test_invalid_max_events_wrong_type_rejected(tmp_path):
    media = tmp_path / "does-not-need-to-exist.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(media, max_events=1.5)  # type: ignore[arg-type]


def test_missing_media_path_rejected_cleanly(tmp_path):
    missing = tmp_path / "nonexistent.mp4"
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(missing)


def test_missing_ffmpeg_handled_cleanly(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"irrelevant, ffmpeg resolution fails before this is read")
    reset_tool_cache()
    monkeypatch.setenv("PATH", "/definitely/not/a/real/path")
    try:
        with pytest.raises(VisualChangeAdapterError, match="ffmpeg"):
            build_visual_change_adapter_result(media)
    finally:
        reset_tool_cache()


# --- CLI: generate-ffmpeg-visual-change-adapter-result (parameter errors) ---


def test_cli_rejects_missing_media_path_cleanly(tmp_path):
    missing = tmp_path / "nonexistent.mp4"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-ffmpeg-visual-change-adapter-result", str(missing)]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_cli_rejects_out_of_range_threshold(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"placeholder")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(media),
            "--threshold",
            "1000",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_cli_rejects_out_of_range_max_events(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"placeholder")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(media),
            "--max-events",
            "0",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# --- ffmpeg-gated: real media, real detection, real pipeline ----------------


pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


def _run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


@pytest.fixture(scope="module")
def scene_change_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_visual_change_adapter")
    video_path = directory / "scene_change.mp4"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=64x64:duration=1:rate=5",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=64x64:duration=1:rate=5",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ]
    )
    return video_path


@pytest.fixture(scope="module")
def flat_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_visual_change_adapter_flat")
    video_path = directory / "flat.mp4"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=64x64:duration=1:rate=5",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ]
    )
    return video_path


@pytest.fixture
def valid_package(tmp_path, scene_change_video):
    from clu_latent.ingest import ingest_video

    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(scene_change_video, output_path)
    return result.package_path


@pytestmark_e2e
def test_detects_candidate_on_scene_change_video(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    events = result.events_by_lane[VISUAL_CHANGE_LANE]
    assert len(events) >= 1


@pytestmark_e2e
def test_no_event_media_handled_cleanly(flat_video):
    result = build_visual_change_adapter_result(flat_video, threshold=DEFAULT_THRESHOLD)
    assert result.events_by_lane[VISUAL_CHANGE_LANE] == []
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


@pytestmark_e2e
def test_invalid_media_handled_cleanly(tmp_path):
    bogus = tmp_path / "not_a_video.mp4"
    bogus.write_text("this is not a real media file", encoding="utf-8")
    with pytest.raises(VisualChangeAdapterError):
        build_visual_change_adapter_result(bogus)


@pytestmark_e2e
def test_generated_result_uses_supported_lane_only(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    for lane in result.events_by_lane:
        assert lane in ANALYSIS_LANE_NAMES
    assert set(result.events_by_lane) == {VISUAL_CHANGE_LANE}


@pytestmark_e2e
def test_generated_events_avoid_identity_claims(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    for events in result.events_by_lane.values():
        for event in events:
            payload = event["payload"]
            for forbidden_field in FORBIDDEN_IDENTITY_FIELDS:
                assert forbidden_field not in payload
            for flag_field in IDENTITY_FLAG_FIELDS:
                assert flag_field not in payload


@pytestmark_e2e
def test_generated_timestamps_are_integer_milliseconds(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    events = result.events_by_lane[VISUAL_CHANGE_LANE]
    assert events, "expected at least one candidate event on a scene-change video"
    for event in events:
        assert isinstance(event["t_start_ms"], int) and not isinstance(event["t_start_ms"], bool)
        assert isinstance(event["t_end_ms"], int) and not isinstance(event["t_end_ms"], bool)
        assert event["t_end_ms"] >= event["t_start_ms"]


@pytestmark_e2e
def test_generated_result_validates_with_adapter_contracts(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


@pytestmark_e2e
def test_generated_result_dry_runs_successfully(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    report = dry_run_adapter_result(result, package_path=None)
    assert report.valid is True
    assert report.errors == []


@pytestmark_e2e
def test_generated_result_dry_runs_against_real_package(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    report = dry_run_adapter_result(result, package_path=valid_package)
    assert report.valid is True
    assert report.would_write is True


@pytestmark_e2e
def test_generated_result_imports_successfully_via_writer(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_results = write_adapter_result(valid_package, result)
    assert {w.lane for w in write_results} == {VISUAL_CHANGE_LANE}


@pytestmark_e2e
def test_package_validates_after_import(scene_change_video, valid_package):
    from clu_latent.validate import validate_package

    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


@pytestmark_e2e
def test_max_events_truncates_candidates(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=0.1, max_events=1)
    events = result.events_by_lane[VISUAL_CHANGE_LANE]
    assert len(events) <= 1


# --- CLI: generate-ffmpeg-visual-change-adapter-result (real media) ---------


@pytestmark_e2e
def test_cli_stdout_emits_valid_json(scene_change_video):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--threshold",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME


@pytestmark_e2e
def test_cli_stdout_has_no_control_sequences(scene_change_video):
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-ffmpeg-visual-change-adapter-result", str(scene_change_video)]
    )
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


@pytestmark_e2e
def test_cli_output_file_mode_writes_valid_json(scene_change_video, tmp_path):
    output_path = tmp_path / "visual-change-result.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(output_path),
            "--threshold",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME


@pytestmark_e2e
def test_cli_refuses_overwrite_without_force(scene_change_video, tmp_path):
    output_path = tmp_path / "visual-change-result.json"
    output_path.write_text("existing content", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(output_path),
        ],
    )
    assert result.exit_code != 0
    assert output_path.read_text(encoding="utf-8") == "existing content"


@pytestmark_e2e
def test_cli_overwrites_with_force(scene_change_video, tmp_path):
    output_path = tmp_path / "visual-change-result.json"
    output_path.write_text("existing content", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(output_path),
            "--force",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME


@pytestmark_e2e
def test_cli_generated_result_imports_via_cli(scene_change_video, valid_package, tmp_path):
    result_path = tmp_path / "visual-change-result.json"
    runner = CliRunner()
    generated = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(result_path),
            "--threshold",
            "5",
        ],
    )
    assert generated.exit_code == 0, generated.output

    dry = runner.invoke(
        app,
        [
            "analysis",
            "dry-run-adapter",
            str(result_path),
            "--package",
            str(valid_package),
        ],
    )
    assert dry.exit_code == 0, dry.output
    assert "PASS" in dry.output

    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imported.exit_code == 0, imported.output
    assert VISUAL_CHANGE_LANE in imported.output

    validated = runner.invoke(app, ["validate", str(valid_package)])
    assert validated.exit_code == 0, validated.output
    assert "PASS" in validated.output


@pytestmark_e2e
def test_visual_change_adapter_result_to_dict_round_trips(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    data = visual_change_adapter_result_to_dict(result)
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME
    assert data["events_by_lane"].keys() == {VISUAL_CHANGE_LANE}
