"""Tests for Phase 2.8: the `clulatent analysis <subcommand>` CLI commands
built on the Phase 2.6 schema primitives and the Phase 2.7 writer.

ffmpeg-gated (for every test that needs a real ingested package to
write into): mirrors test_review_writer.py / test_analysis_writer.py.
The `lanes` and `validate-file` commands never need a package, so a
couple of tests below run unconditionally.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import analysis_lanes as analysis_lanes_mod
from clu_latent import lock as lock_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.tracks import read_track_file

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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


# --- lanes (never needs ffmpeg or a package) ---------------------------------


def test_cli_analysis_lanes_lists_all_supported_lanes():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "lanes"])
    assert result.exit_code == 0, result.output
    for name in analysis_lanes_mod.ANALYSIS_LANE_NAMES:
        assert name in result.output


# --- validate-file (never needs ffmpeg or a package) -------------------------


def test_cli_analysis_validate_file_valid_events_passes(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_scene_event()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_analysis_validate_file_invalid_events_fails(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_scene_event(t_start_ms=-1)) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_analysis_validate_file_does_not_write_anything(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_scene_event()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 0, result.output
    # Nothing package-shaped exists anywhere near events_file/tmp_path.
    assert not any(tmp_path.rglob("manifest.json"))
    assert not any(tmp_path.rglob("*.jsonl.tmp"))
    assert not (tmp_path / "receipts").exists()


def test_cli_analysis_validate_file_unsupported_lane_fails(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_scene_event()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "not_a_real_lane", str(events_file)])
    assert result.exit_code == 1


def test_cli_analysis_validate_file_missing_file_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.jsonl"
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_cli_analysis_validate_file_empty_file_clean_error(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 1
    assert "empty" in result.output


def test_cli_analysis_validate_file_invalid_json_clean_error(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("not json at all\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "validate-file", "scene_events", str(events_file)])
    assert result.exit_code == 1


# --- append / append-file (need a real ingested package) --------------------


pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    directory = tmp_path_factory.mktemp("clulatent_cli_analysis_fixture")
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


def test_cli_analysis_append_single_valid_event(valid_package):
    runner = CliRunner()
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
    assert "Analysis events written" in result.output

    events = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert len(events) == 1
    assert events[0].id == "scn_000000"


def test_cli_analysis_append_rejects_malformed_json(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", "append", str(valid_package), "scene_events", "--event-json", "not-json"],
    )
    assert result.exit_code == 1
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_rejects_non_object_json(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", "append", str(valid_package), "scene_events", "--event-json", "[1, 2, 3]"],
    )
    assert result.exit_code == 1
    assert "must be a JSON object" in result.output
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_rejects_unsupported_lane(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "append",
            str(valid_package),
            "not_a_real_lane",
            "--event-json",
            json.dumps(_scene_event()),
        ],
    )
    assert result.exit_code == 1
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_rejects_invalid_event(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "append",
            str(valid_package),
            "scene_events",
            "--event-json",
            json.dumps(_scene_event(t_start_ms=-1)),
        ],
    )
    assert result.exit_code == 1
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_file_valid_jsonl(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            json.dumps(_scene_event(id=f"scn_00000{i}", t_start_ms=i * 100, t_end_ms=i * 100 + 50))
            for i in range(3)
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "append-file", str(valid_package), "scene_events", str(events_file)])
    assert result.exit_code == 0, result.output

    events = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert len(events) == 3


def test_cli_analysis_append_file_valid_json_array(valid_package, tmp_path):
    events_file = tmp_path / "events.json"
    events_file.write_text(
        json.dumps(
            [
                _scene_event(id=f"scn_00000{i}", t_start_ms=i * 100, t_end_ms=i * 100 + 50)
                for i in range(2)
            ]
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "append-file", str(valid_package), "scene_events", str(events_file)])
    assert result.exit_code == 0, result.output

    events = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert len(events) == 2


def test_cli_analysis_append_file_invalid_jsonl_rejected_no_partial_write(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        json.dumps(_scene_event(id="scn_000000")) + "\nnot valid json\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "append-file", str(valid_package), "scene_events", str(events_file)])
    assert result.exit_code == 1
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_file_empty_file_rejected(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "append-file", str(valid_package), "scene_events", str(events_file)])
    assert result.exit_code == 1
    assert "empty" in result.output
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_on_locked_package_fails(valid_package):
    lock_mod.create_lock(valid_package)
    runner = CliRunner()
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
    assert result.exit_code == 1
    assert "integrity lock" in result.output
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_cli_analysis_append_on_nonexistent_package_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", "append", str(missing), "scene_events", "--event-json", json.dumps(_scene_event())],
    )
    assert result.exit_code == 1
    assert "Package not found" in result.output


def test_cli_analysis_append_receipt_metadata_flags_passed_through(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "append",
            str(valid_package),
            "scene_events",
            "--event-json",
            json.dumps(_scene_event()),
            "--adapter-name",
            "my-adapter",
            "--tool-name",
            "my-tool",
            "--tool-version",
            "9.9.9",
            "--model-name",
            "my-model",
            "--model-version",
            "1.0",
            "--parameter-json",
            json.dumps({"threshold": 0.5}),
            "--input-source",
            "sources/source.mp4",
        ],
    )
    assert result.exit_code == 0, result.output

    receipt_lines = (valid_package / "receipts/analyze.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(receipt_lines) == 1
    receipt = json.loads(receipt_lines[0])
    assert receipt["adapter_name"] == "my-adapter"
    assert receipt["tool_name"] == "my-tool"
    assert receipt["tool_version"] == "9.9.9"
    assert receipt["model_name"] == "my-model"
    assert receipt["model_version"] == "1.0"
    assert receipt["parameters"] == {"threshold": 0.5}
    assert receipt["input_sources"] == ["sources/source.mp4"]


def test_cli_analysis_append_no_receipt_flag_skips_receipt(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "append",
            str(valid_package),
            "scene_events",
            "--event-json",
            json.dumps(_scene_event()),
            "--no-receipt",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (valid_package / "receipts/analyze.jsonl").exists()
