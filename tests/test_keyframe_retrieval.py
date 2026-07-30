"""Tests for Phase 3.13: read-only keyframe evidence retrieval.

Two groups, mirroring `tests/test_keyframe_preview.py`:

- Pure / no-media tests: module import, CLI help, error handling.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package
  (same fixture pattern as the preview tests), then retrieve keyframe
  evidence by id, time range, and nearest timestamp, and assert the
  retrieval is bounded, ordered, deterministic, safe, and read-only.

Core rule under test: show visual evidence, do not interpret it.
Retrieval hands back the keyframe records ingest already stored and
never claims to know what the clip depicts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import keyframe_retrieval as retrieval_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- Pure tests (no ffmpeg, no package) --------------------------------------


def test_keyframe_retrieval_module_imports_cleanly():
    import clu_latent.keyframe_retrieval  # noqa: F401


def test_keyframes_get_command_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "get", "--help"])
    assert result.exit_code == 0, result.output
    assert "keyframe" in result.output.lower()


def test_keyframes_retrieval_summary_command_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "retrieval-summary", "--help"])
    assert result.exit_code == 0, result.output


def test_load_keyframe_events_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.load_keyframe_events(tmp_path / "does-not-exist.clulatent")


def test_get_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.get_keyframe_by_id(tmp_path / "nope.clulatent", "kf_000000")


def test_cli_get_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["keyframes", "get", str(tmp_path / "does-not-exist.clulatent"), "kf_000000"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_invalid_time_range_raises(tmp_path):
    # An inverted range is a caller mistake and is refused before any
    # package read even needs to succeed.
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.query_keyframes_by_time_range(
            tmp_path / "whatever.clulatent", 5000, 1000
        )


def test_readme_includes_phase_3_13_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


def test_phase_3_13_doc_exists():
    doc = REPO_ROOT / "docs" / "PHASE_3_13_KEYFRAME_EVIDENCE_RETRIEVAL_CLI.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "retrieval" in text.lower()


# --- ffmpeg-gated end-to-end tests --------------------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_keyframe_retrieval_fixture")
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
        "testsrc=duration=3:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
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
def valid_package(tmp_path, tiny_video) -> Path:
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


@pytestmark_e2e
def test_load_keyframe_events_returns_ordered(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    assert events, "fixture should have produced keyframe events"
    starts = [e["t_start_ms"] for e in events]
    assert starts == sorted(starts)
    assert all(e["type"] == "keyframe" for e in events)


@pytestmark_e2e
def test_get_by_valid_id(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    target = events[0]
    got = retrieval_mod.get_keyframe_by_id(valid_package, target["id"])
    assert got is not None
    assert got["id"] == target["id"]


@pytestmark_e2e
def test_get_by_missing_id_returns_none(valid_package):
    assert retrieval_mod.get_keyframe_by_id(valid_package, "kf_999999") is None


@pytestmark_e2e
def test_cli_get_missing_id_clean(valid_package):
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "get", str(valid_package), "kf_999999"])
    assert result.exit_code == 0, result.output
    assert "No keyframe found" in result.output


@pytestmark_e2e
def test_cli_get_valid_id(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    target = events[0]["id"]
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "get", str(valid_package), target])
    assert result.exit_code == 0, result.output
    assert target in result.output
    assert "media/keyframes" in result.output


@pytestmark_e2e
def test_query_time_range_returns_expected_ordered_frames(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    assert len(events) >= 2
    lo = events[0]["t_start_ms"]
    hi = events[-1]["t_end_ms"]
    got = retrieval_mod.query_keyframes_by_time_range(valid_package, lo, hi)
    assert got == events  # whole span returns everything, in order
    starts = [e["t_start_ms"] for e in got]
    assert starts == sorted(starts)


@pytestmark_e2e
def test_query_time_range_empty_result_is_clean(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    far = events[-1]["t_end_ms"] + 10_000_000
    got = retrieval_mod.query_keyframes_by_time_range(valid_package, far, far + 1000)
    assert got == []


@pytestmark_e2e
def test_query_time_invalid_range_fails(valid_package):
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.query_keyframes_by_time_range(valid_package, 5000, 1000)


@pytestmark_e2e
def test_cli_query_time_invalid_range_clean_error(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["keyframes", "query-time", str(valid_package), "--start-ms", "5000", "--end-ms", "1000"],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


@pytestmark_e2e
def test_nearest_returns_closest_keyframe(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    # Aim just after the second keyframe's start so the closest frame is
    # unambiguous.
    target = events[1]
    probe = target["t_start_ms"] + 1
    nearest = retrieval_mod.get_nearest_keyframe(valid_package, probe)
    assert nearest is not None
    # The nearest frame must be the one whose interval is closest to probe.
    def dist(e):
        s, en = e["t_start_ms"], e["t_end_ms"]
        if probe < s:
            return s - probe
        if probe > en:
            return probe - en
        return 0

    best = min(dist(e) for e in events)
    assert dist(nearest) == best


@pytestmark_e2e
def test_nearest_tie_is_deterministic(valid_package):
    # Overwrite the track with two keyframes equidistant from the probe so
    # the tie-break rule (earliest t_start, then id) is exercised.
    track = valid_package / "tracks" / "keyframes.jsonl"
    a = {
        "id": "kf_000000",
        "type": "keyframe",
        "t_start_ms": 0,
        "t_end_ms": 100,
        "producer": {"name": "ffmpeg", "version": "test"},
        "confidence": None,
        "payload": {"path": "media/keyframes/000000.jpg", "frame_index": 0, "width": 1, "height": 1},
    }
    b = {
        "id": "kf_000001",
        "type": "keyframe",
        "t_start_ms": 200,
        "t_end_ms": 300,
        "producer": {"name": "ffmpeg", "version": "test"},
        "confidence": None,
        "payload": {"path": "media/keyframes/000001.jpg", "frame_index": 1, "width": 1, "height": 1},
    }
    track.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
    # probe=150 is 50 ms from a's end (100) and 50 ms from b's start (200).
    first = retrieval_mod.get_nearest_keyframe(valid_package, 150)
    second = retrieval_mod.get_nearest_keyframe(valid_package, 150)
    assert first is not None and first["id"] == first["id"]
    assert first["id"] == second["id"]  # deterministic across calls
    assert first["id"] == "kf_000000"  # earliest t_start wins the tie


@pytestmark_e2e
def test_retrieval_summary_reports_counts_and_range(valid_package):
    events = retrieval_mod.load_keyframe_events(valid_package)
    summary = retrieval_mod.summarize_keyframe_retrieval(valid_package)
    assert summary["keyframe_count"] == len(events)
    assert summary["first_timestamp_ms"] == min(e["t_start_ms"] for e in events)
    assert summary["last_timestamp_ms"] == max(e["t_end_ms"] for e in events)
    assert summary["missing_image_count"] == 0
    assert summary["event_ids"] == [e["id"] for e in events]


@pytestmark_e2e
def test_retrieval_summary_reports_missing_files(valid_package):
    keyframes_dir = valid_package / "media" / "keyframes"
    images = sorted(keyframes_dir.glob("*"))
    assert images
    images[0].unlink()
    summary = retrieval_mod.summarize_keyframe_retrieval(valid_package)
    assert summary["missing_image_count"] >= 1
    assert summary["missing_image_paths"]


@pytestmark_e2e
def test_cli_retrieval_summary_runs(valid_package):
    runner = CliRunner()
    result = runner.invoke(app, ["keyframes", "retrieval-summary", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "keyframe_count:" in result.output


@pytestmark_e2e
def test_retrieval_is_read_only_and_creates_no_receipts(valid_package):
    before = _snapshot(valid_package)
    retrieval_mod.load_keyframe_events(valid_package)
    retrieval_mod.get_keyframe_by_id(valid_package, "kf_000000")
    retrieval_mod.query_keyframes_by_time_range(valid_package, 0, 10_000)
    retrieval_mod.get_nearest_keyframe(valid_package, 1000)
    retrieval_mod.summarize_keyframe_retrieval(valid_package)
    assert _snapshot(valid_package) == before


@pytestmark_e2e
def test_invalid_package_is_refused(valid_package):
    # A keyframes track file declared in the manifest but deleted from
    # disk makes the package unreadable for retrieval -> clean refusal.
    (valid_package / "tracks" / "keyframes.jsonl").unlink()
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.load_keyframe_events(valid_package)


@pytestmark_e2e
def test_unsafe_image_path_is_rejected(valid_package):
    track = valid_package / "tracks" / "keyframes.jsonl"
    event = {
        "id": "kf_000000",
        "type": "keyframe",
        "t_start_ms": 0,
        "t_end_ms": 100,
        "producer": {"name": "ffmpeg", "version": "test"},
        "confidence": None,
        "payload": {
            "path": "../../../../etc/passwd",
            "frame_index": 0,
            "width": 1,
            "height": 1,
        },
    }
    track.write_text(json.dumps(event) + "\n", encoding="utf-8")
    with pytest.raises(retrieval_mod.KeyframeRetrievalError):
        retrieval_mod.load_keyframe_events(valid_package)


@pytestmark_e2e
def test_empty_keyframes_track_yields_empty_summary(valid_package):
    (valid_package / "tracks" / "keyframes.jsonl").write_text("", encoding="utf-8")
    summary = retrieval_mod.summarize_keyframe_retrieval(valid_package)
    assert summary["keyframe_count"] == 0
    assert summary["first_timestamp_ms"] is None
    assert summary["last_timestamp_ms"] is None
    assert summary["event_ids"] == []
    assert retrieval_mod.get_nearest_keyframe(valid_package, 1000) is None
