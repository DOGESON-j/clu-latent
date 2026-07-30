"""Tests for Phase 3.0: the static, local-first HTML package report.

Two groups:

- Pure / no-media tests: module import, CLI help, error handling. These
  need no ffmpeg and no real package.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package
  (via the same fixture pattern as `tests/test_validate_reindex.py`),
  optionally add analysis lanes, analyze receipts, and review events,
  then generate a report and check its content and safety properties.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.report import ReportError, generate_report

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Pure tests (no ffmpeg, no package) --------------------------------------


def test_report_module_imports_cleanly():
    import clu_latent.report  # noqa: F401


def test_report_command_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["report", "--help"])
    assert result.exit_code == 0, result.output
    assert "report" in result.output.lower()


def test_report_on_nonexistent_package_raises_report_error(tmp_path):
    with pytest.raises(ReportError):
        generate_report(tmp_path / "does-not-exist.clulatent")


def test_report_cli_on_nonexistent_package_gives_clean_error(tmp_path):
    runner = CliRunner()
    output = tmp_path / "report.html"
    result = runner.invoke(
        app, ["report", str(tmp_path / "does-not-exist.clulatent"), "--output", str(output)]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert not output.exists()


def test_report_cli_missing_output_parent_gives_clean_error(tmp_path):
    runner = CliRunner()
    (tmp_path / "pkg.clulatent").mkdir()
    output = tmp_path / "missing_dir" / "report.html"
    result = runner.invoke(app, ["report", str(tmp_path / "pkg.clulatent"), "--output", str(output)])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "does not exist" in result.output


def test_report_cli_existing_output_requires_force(tmp_path):
    runner = CliRunner()
    (tmp_path / "pkg.clulatent").mkdir()
    output = tmp_path / "report.html"
    output.write_text("existing", encoding="utf-8")
    result = runner.invoke(app, ["report", str(tmp_path / "pkg.clulatent"), "--output", str(output)])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert output.read_text(encoding="utf-8") == "existing"


# --- ffmpeg-gated end-to-end tests --------------------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_report_fixture")
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


@pytestmark_e2e
def test_report_generation_succeeds_for_minimal_package(valid_package):
    result = generate_report(valid_package)
    assert "<html" in result.html


@pytestmark_e2e
def test_report_cli_generation_succeeds_for_minimal_package(valid_package, tmp_path):
    runner = CliRunner()
    output = tmp_path / "report.html"
    result = runner.invoke(app, ["report", str(valid_package), "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.exists()
    assert "<html" in output.read_text(encoding="utf-8")


@pytestmark_e2e
def test_report_html_includes_package_summary(valid_package):
    result = generate_report(valid_package)
    assert "Package summary" in result.html
    assert "Package path" in result.html


@pytestmark_e2e
def test_report_html_includes_trust_language(valid_package):
    result = generate_report(valid_package)
    assert "evidence" in result.html.lower()
    assert "not truth" in result.html.lower()
    assert "Trust status" in result.html


@pytestmark_e2e
def test_report_html_includes_timeline_table(valid_package):
    result = generate_report(valid_package)
    assert "Timeline overview" in result.html
    assert "<table" in result.html


@pytestmark_e2e
def test_report_html_includes_receipt_summary(valid_package):
    result = generate_report(valid_package)
    assert "Receipts" in result.html
    assert "Ingest receipts" in result.html


@pytestmark_e2e
def test_report_html_avoids_external_network_resources(valid_package):
    result = generate_report(valid_package)
    lowered = result.html.lower()
    assert "<script" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "cdn." not in lowered


@pytestmark_e2e
def test_report_generation_does_not_mutate_package(valid_package):
    before_files = sorted(p.relative_to(valid_package) for p in valid_package.rglob("*") if p.is_file())
    before_bytes = {
        str(p): p.read_bytes()
        for p in valid_package.rglob("*")
        if p.is_file() and p.name != "search.sqlite"
    }

    generate_report(valid_package)

    after_files = sorted(p.relative_to(valid_package) for p in valid_package.rglob("*") if p.is_file())
    assert after_files == before_files
    after_bytes = {
        str(p): p.read_bytes()
        for p in valid_package.rglob("*")
        if p.is_file() and p.name != "search.sqlite"
    }
    assert after_bytes == before_bytes


@pytestmark_e2e
def test_report_generation_does_not_create_index_receipts_or_lock_files(valid_package, tmp_path):
    lock_path = valid_package / "lock"
    assert not lock_path.exists()

    output = tmp_path / "report.html"
    runner = CliRunner()
    result = runner.invoke(app, ["report", str(valid_package), "--output", str(output)])
    assert result.exit_code == 0, result.output

    assert not lock_path.exists()
    status, _report = lock_mod.lock_status(valid_package)
    assert status == "unlocked"
    receipts_dir = valid_package / "receipts"
    assert sorted(p.name for p in receipts_dir.iterdir()) == ["ingest.jsonl"]


@pytestmark_e2e
def test_report_html_escapes_unsafe_payload_html(valid_package, tmp_path):
    review_track = valid_package / "tracks" / "review_events.jsonl"
    keyframes = json.loads(
        (valid_package / "tracks" / "keyframes.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    source_event_id = keyframes["id"]

    runner = CliRunner()
    note_result = runner.invoke(
        app,
        [
            "review",
            "note",
            str(valid_package),
            "--note",
            "<script>alert(1)</script>",
            "--event-id",
            source_event_id,
        ],
    )
    assert note_result.exit_code == 0, note_result.output
    assert review_track.exists()

    result = generate_report(valid_package)
    assert "<script>alert(1)</script>" not in result.html
    assert "&lt;script&gt;" in result.html


@pytestmark_e2e
def test_report_html_includes_review_section_after_note(valid_package):
    keyframes = json.loads(
        (valid_package / "tracks" / "keyframes.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    source_event_id = keyframes["id"]

    runner = CliRunner()
    note_result = runner.invoke(
        app,
        ["review", "note", str(valid_package), "--note", "looks fine", "--event-id", source_event_id],
    )
    assert note_result.exit_code == 0, note_result.output

    result = generate_report(valid_package)
    assert "Review" in result.html
    assert "Notes" in result.html
    assert "No review events found." not in result.html


@pytestmark_e2e
def test_report_html_states_no_review_events_when_none(valid_package):
    result = generate_report(valid_package)
    assert "No review events found." in result.html


@pytestmark_e2e
def test_report_generation_succeeds_with_analysis_lanes_and_receipts(valid_package, tmp_path):
    runner = CliRunner()
    out_path = tmp_path / "fixture_adapter_result.json"
    gen = runner.invoke(app, ["analysis", "generate-fixture-adapter-result", "--output", str(out_path)])
    assert gen.exit_code == 0, gen.output

    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(out_path)]
    )
    assert imported.exit_code == 0, imported.output

    result = generate_report(valid_package)
    assert "Adapter evidence" in result.html
    assert "No analysis lanes present in this package." not in result.html
    assert "understood the media" in result.html

    receipt_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipt_path.exists()

    output = tmp_path / "report.html"
    cli_result = runner.invoke(app, ["report", str(valid_package), "--output", str(output)])
    assert cli_result.exit_code == 0, cli_result.output
    html_text = output.read_text(encoding="utf-8")
    assert "Adapter evidence" in html_text


@pytestmark_e2e
def test_report_validation_section_reflects_broken_package(valid_package):
    (valid_package / "manifest.json").unlink()
    with pytest.raises(ReportError):
        generate_report(valid_package)


@pytestmark_e2e
def test_report_validation_section_shows_fail_for_readable_but_invalid_package(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    result = generate_report(valid_package)
    assert "Validation" in result.html
    assert "FAIL" in result.html
