"""Tests for Phase 2.17: real adapter CLI polish.

Exercises the Phase 2.17 polish to `clulatent analysis
generate-ffmpeg-visual-change-adapter-result` (Phase 2.15): the richer
`--output`-mode success summary, the clean directory-path error, and
the unchanged pure-JSON stdout mode / downstream dry-run / import /
validate pipeline. Adds no new module, adapter, or CLI command —
mirrors the ffmpeg-gated fixture pattern already used in
`tests/test_analysis_ffmpeg_visual_change_adapter.py` and
`tests/test_real_adapter_demo_package.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent.analysis_adapter_dry_run import load_adapter_result_json
from clu_latent.analysis_adapters import validate_adapter_result
from clu_latent.analysis_ffmpeg_visual_change_adapter import ADAPTER_NAME, VISUAL_CHANGE_LANE
from clu_latent.cli import app
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

COMMAND = "generate-ffmpeg-visual-change-adapter-result"


# --- Help text (no media, no ffmpeg needed) ---------------------------------


def test_cli_help_mentions_workflow_and_evidence_language():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", COMMAND, "--help"])
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    assert "dry-run-adapter" in lowered
    assert "import-adapter-result" in lowered
    assert "candidate" in lowered
    assert "not confirmed" in lowered or "not truth" in lowered or "never" in lowered


def test_cli_help_has_no_control_sequences():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", COMMAND, "--help"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


# --- ffmpeg-gated end-to-end polish checks ----------------------------------


pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


def _run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


@pytest.fixture(scope="module")
def scene_change_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_cli_polish")
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


@pytest.fixture
def valid_package(tmp_path, scene_change_video):
    from clu_latent.ingest import ingest_video

    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(scene_change_video, output_path)
    return result.package_path


@pytestmark_e2e
def test_cli_generation_succeeds(scene_change_video, tmp_path):
    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME


@pytestmark_e2e
def test_output_mode_summary_includes_adapter_name_and_lane_counts(scene_change_video, tmp_path):
    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert result.exit_code == 0, result.output
    assert ADAPTER_NAME in result.output
    assert "Lanes generated" in result.output
    assert VISUAL_CHANGE_LANE in result.output
    assert "total events" in result.output


@pytestmark_e2e
def test_output_mode_summary_reminds_evidence_not_truth(scene_change_video, tmp_path):
    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert result.exit_code == 0, result.output
    lowered = result.output.lower()
    assert "candidate evidence" in lowered
    assert "not confirmed truth" in lowered
    assert "dry-run-adapter" in lowered
    assert "import-adapter-result" in lowered


@pytestmark_e2e
def test_output_mode_summary_has_no_control_sequences(scene_change_video, tmp_path):
    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


@pytestmark_e2e
def test_stdout_mode_stays_pure_json(scene_change_video):
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", COMMAND, str(scene_change_video), "--threshold", "5"])
    assert result.exit_code == 0, result.output
    # Must parse cleanly -- no summary text, no reminder, nothing but JSON.
    data = json.loads(result.output)
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME
    assert "\x1b" not in result.output


@pytestmark_e2e
def test_generated_result_validates_dry_runs_and_imports(scene_change_video, valid_package, tmp_path):
    out = tmp_path / "result.json"
    runner = CliRunner()

    gen = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert gen.exit_code == 0, gen.output

    result = load_adapter_result_json(out)
    errors, _warnings = validate_adapter_result(result)
    assert errors == []

    dry = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(out), "--package", str(valid_package)]
    )
    assert dry.exit_code == 0, dry.output
    assert "PASS" in dry.output

    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(out)]
    )
    assert imported.exit_code == 0, imported.output

    report = validate_package(valid_package)
    assert report.valid is True, report.errors

    validated = runner.invoke(app, ["validate", str(valid_package)])
    assert validated.exit_code == 0, validated.output
    assert "PASS" in validated.output


@pytestmark_e2e
def test_cli_generation_does_not_mutate_package(scene_change_video, valid_package, tmp_path):
    before_tracks = {p.name for p in (valid_package / "tracks").iterdir()}
    before_manifest = (valid_package / "manifest.json").read_bytes()
    before_receipt_exists = (valid_package / "receipts" / "analyze.jsonl").exists()

    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert result.exit_code == 0, result.output

    after_tracks = {p.name for p in (valid_package / "tracks").iterdir()}
    after_manifest = (valid_package / "manifest.json").read_bytes()
    assert after_tracks == before_tracks
    assert after_manifest == before_manifest
    assert (valid_package / "receipts" / "analyze.jsonl").exists() == before_receipt_exists


@pytestmark_e2e
def test_cli_generation_does_not_touch_index(scene_change_video, valid_package, tmp_path):
    index_path = valid_package / "index" / "search.sqlite"
    before = index_path.read_bytes()

    out = tmp_path / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out), "--threshold", "5"]
    )
    assert result.exit_code == 0, result.output

    after = index_path.read_bytes()
    assert after == before


# --- Error handling ----------------------------------------------------------


@pytestmark_e2e
def test_cli_output_directory_path_fails_cleanly(scene_change_video, tmp_path):
    outdir = tmp_path / "somedir"
    outdir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["analysis", COMMAND, str(scene_change_video), "-o", str(outdir), "--force"],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "directory" in result.output.lower()


@pytestmark_e2e
def test_cli_output_directory_path_fails_cleanly_without_force(scene_change_video, tmp_path):
    outdir = tmp_path / "somedir"
    outdir.mkdir()
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(scene_change_video), "-o", str(outdir)]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_cli_missing_output_parent_directory_fails_cleanly(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"placeholder")
    missing_parent_out = tmp_path / "does" / "not" / "exist" / "result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(media), "-o", str(missing_parent_out)]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_cli_missing_media_path_fails_cleanly(tmp_path):
    missing = tmp_path / "nonexistent.mp4"
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", COMMAND, str(missing)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_cli_non_numeric_threshold_rejected_cleanly(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"placeholder")
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", COMMAND, str(media), "--threshold", "not-a-number"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output


@pytestmark_e2e
def test_cli_refuses_overwrite_without_force_still_clean(scene_change_video, tmp_path):
    out = tmp_path / "result.json"
    out.write_text("existing", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", COMMAND, str(scene_change_video), "-o", str(out)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert out.read_text(encoding="utf-8") == "existing"
