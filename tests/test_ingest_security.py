"""Security-hardening tests for ingest.py.

Covers behavior that doesn't require real ffmpeg (source-size limits,
atomic temp staging via security.temp, failure receipts) plus one
ffmpeg-dependent test for the media-duration limit.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from clu_latent import ingest as ingest_mod
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.security.limits import Limits

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def test_ingest_rejects_oversized_source(tmp_path):
    video_path = tmp_path / "big.mp4"
    video_path.write_bytes(b"x" * 2048)
    output_path = tmp_path / "out.clulatent"

    tiny_limits = Limits(max_source_file_bytes=1024)

    with pytest.raises(IngestError, match="max_source_file_bytes"):
        ingest_video(video_path, output_path, limits=tiny_limits)

    assert not output_path.exists()
    # No package was ever attempted, so no temp dir should exist either.
    assert list(tmp_path.glob(".out.clulatent.tmp-*")) == []


def test_ingest_cleans_up_temp_dir_and_writes_failure_receipt_on_build_failure(
    tmp_path, monkeypatch
):
    video_path = tmp_path / "small.mp4"
    video_path.write_bytes(b"x" * 16)
    output_path = tmp_path / "out.clulatent"

    monkeypatch.setattr(ingest_mod.ffmpeg_tools, "check_tools_available", lambda: None)
    monkeypatch.setattr(ingest_mod, "resolve_tool", lambda _name: "ffprobe")

    def _boom(*args, **kwargs):
        raise ingest_mod.ffmpeg_tools.FfmpegExecutionError(
            "simulated probe failure",
            ingest_mod.ffmpeg_tools.ToolResult(
                command=["ffprobe"],
                returncode=1,
                stdout="",
                stderr="boom",
                stderr_tail="boom",
                timed_out=False,
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        )

    monkeypatch.setattr(ingest_mod.ffmpeg_tools, "probe_video", _boom)

    with pytest.raises(IngestError):
        ingest_video(video_path, output_path)

    assert not output_path.exists()
    # staged_package_dir must always clean up its temp dir on failure.
    assert list(tmp_path.glob(".out.clulatent.tmp-*")) == []

    failure_receipt = tmp_path / "out.clulatent.failure-receipt.jsonl"
    assert failure_receipt.exists()
    content = failure_receipt.read_text()
    assert "simulated probe failure" in content


def test_ingest_without_force_leaves_existing_output_untouched(tmp_path, monkeypatch):
    video_path = tmp_path / "small.mp4"
    video_path.write_bytes(b"x" * 16)
    output_path = tmp_path / "out.clulatent"
    output_path.mkdir()
    (output_path / "marker.txt").write_text("original")

    with pytest.raises(IngestError, match="already exists"):
        ingest_video(video_path, output_path, force=False)

    assert (output_path / "marker.txt").read_text() == "original"


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment")
def test_ingest_rejects_oversized_duration(tmp_path):
    video_path = tmp_path / "tiny.mp4"
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
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    subprocess.run(command, check=True, capture_output=True)

    output_path = tmp_path / "out.clulatent"
    tiny_limits = Limits(max_media_duration_ms=100)

    with pytest.raises(IngestError, match="max_media_duration_ms"):
        ingest_video(video_path, output_path, limits=tiny_limits)

    assert not output_path.exists()
    assert list(tmp_path.glob(".out.clulatent.tmp-*")) == []
    failure_receipt = tmp_path / "out.clulatent.failure-receipt.jsonl"
    assert failure_receipt.exists()
