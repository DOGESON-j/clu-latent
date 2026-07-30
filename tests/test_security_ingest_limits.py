"""Tests for ingest-time resource limits (Phase 1.6).

Uses a real ffmpeg-generated tiny video, then overrides `Limits` with
deliberately tiny thresholds so the size/duration checks trip without
needing to actually generate gigabyte- or hour-scale fixtures. Skips
cleanly if ffmpeg/ffprobe are not available.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace

import pytest

from clu_latent.ingest import IngestError, ingest_video
from clu_latent.security.limits import DEFAULT_LIMITS

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clulatent_ingest_limits_fixture")
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


def test_ingest_rejects_source_exceeding_max_bytes(tmp_path, tiny_video):
    actual_size = tiny_video.stat().st_size
    tight_limits = replace(DEFAULT_LIMITS, max_source_file_bytes=actual_size - 1)

    with pytest.raises(IngestError, match="max_source_file_bytes"):
        ingest_video(tiny_video, tmp_path / "pkg.clulatent", limits=tight_limits)


def test_ingest_rejects_source_exceeding_max_bytes_leaves_no_partial_package(tmp_path, tiny_video):
    actual_size = tiny_video.stat().st_size
    tight_limits = replace(DEFAULT_LIMITS, max_source_file_bytes=actual_size - 1)
    output_path = tmp_path / "pkg.clulatent"

    with pytest.raises(IngestError):
        ingest_video(tiny_video, output_path, limits=tight_limits)

    assert not output_path.exists()
    assert not any(p.name.startswith(f".{output_path.name}.tmp-") for p in tmp_path.iterdir())


def test_ingest_rejects_duration_exceeding_max_media_duration_ms(tmp_path, tiny_video):
    tight_limits = replace(DEFAULT_LIMITS, max_media_duration_ms=500)

    with pytest.raises(IngestError, match="max_media_duration_ms"):
        ingest_video(tiny_video, tmp_path / "pkg.clulatent", limits=tight_limits)


def test_ingest_duration_rejection_leaves_no_partial_package(tmp_path, tiny_video):
    tight_limits = replace(DEFAULT_LIMITS, max_media_duration_ms=500)
    output_path = tmp_path / "pkg.clulatent"

    with pytest.raises(IngestError):
        ingest_video(tiny_video, output_path, limits=tight_limits)

    assert not output_path.exists()
    assert not any(p.name.startswith(f".{output_path.name}.tmp-") for p in tmp_path.iterdir())


def test_ingest_succeeds_within_default_limits(tmp_path, tiny_video):
    result = ingest_video(tiny_video, tmp_path / "pkg.clulatent")
    assert result.package_path.exists()
