"""End-to-end integration test: generate a tiny video, ingest it, verify
the resulting .clulatent package structure and CLI commands.

Skips cleanly if ffmpeg/ffprobe are not available in the test environment.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.index import query_search_index
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.manifest import Manifest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    """Generate a ~2 second, 320x240 test video with audio using ffmpeg."""
    directory = tmp_path_factory.mktemp("clulatent_fixture")
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


def test_ingest_creates_expected_package_structure(tmp_path, tiny_video):
    output_path = tmp_path / "tiny.clulatent"

    result = ingest_video(tiny_video, output_path)

    assert result.package_path == output_path
    assert output_path.exists()

    assert (output_path / "manifest.json").exists()
    assert (output_path / "sources" / "source.mp4").exists()
    assert (output_path / "sources" / "source.sha256").exists()
    assert (output_path / "media" / "keyframes").is_dir()
    assert list((output_path / "media" / "keyframes").glob("*.jpg"))
    assert (output_path / "tracks" / "keyframes.jsonl").exists()
    assert (output_path / "tracks" / "audio_events.jsonl").exists()
    assert (output_path / "tracks" / "speech_events.jsonl").exists()
    assert (output_path / "tracks" / "semantic_events.jsonl").exists()
    assert (output_path / "index" / "search.sqlite").exists()
    assert (output_path / "receipts" / "ingest.jsonl").exists()

    # No leftover temp directories.
    leftovers = list(tmp_path.glob(".tiny.clulatent.tmp-*"))
    assert leftovers == []

    manifest = Manifest.from_json_file(output_path / "manifest.json")
    assert manifest.status == "complete"
    assert manifest.source.has_audio is True
    assert manifest.media.keyframes.count >= 1
    assert manifest.index.canonical is False


def test_ingest_without_force_fails_on_existing_output(tmp_path, tiny_video):
    output_path = tmp_path / "dup.clulatent"
    ingest_video(tiny_video, output_path)

    with pytest.raises(IngestError):
        ingest_video(tiny_video, output_path, force=False)

    # Original package must be untouched.
    assert (output_path / "manifest.json").exists()


def test_ingest_with_force_overwrites_existing_output(tmp_path, tiny_video):
    output_path = tmp_path / "overwrite.clulatent"
    ingest_video(tiny_video, output_path)
    first_manifest = Manifest.from_json_file(output_path / "manifest.json")

    ingest_video(tiny_video, output_path, force=True)
    second_manifest = Manifest.from_json_file(output_path / "manifest.json")

    assert first_manifest.package_id != second_manifest.package_id


def test_ingest_missing_input_raises_clean_error(tmp_path):
    with pytest.raises(IngestError):
        ingest_video(tmp_path / "does_not_exist.mp4", tmp_path / "out.clulatent")


def test_search_index_is_queryable_after_ingest(tmp_path, tiny_video):
    output_path = tmp_path / "queryable.clulatent"
    ingest_video(tiny_video, output_path)

    sqlite_path = output_path / "index" / "search.sqlite"
    keyframe_matches = query_search_index(sqlite_path, "keyframe")
    audio_matches = query_search_index(sqlite_path, "audio")
    ffmpeg_matches = query_search_index(sqlite_path, "ffmpeg")

    assert len(keyframe_matches) >= 1
    assert len(audio_matches) >= 1
    assert len(ffmpeg_matches) >= 1


def test_cli_end_to_end(tmp_path, tiny_video):
    runner = CliRunner()
    output_path = tmp_path / "cli.clulatent"

    ingest_result = runner.invoke(
        app, ["ingest", str(tiny_video), "-o", str(output_path)]
    )
    assert ingest_result.exit_code == 0, ingest_result.output

    inspect_result = runner.invoke(app, ["inspect", str(output_path)])
    assert inspect_result.exit_code == 0, inspect_result.output
    assert "package_id" in inspect_result.output

    timeline_result = runner.invoke(app, ["timeline", str(output_path)])
    assert timeline_result.exit_code == 0, timeline_result.output
    assert "keyframe" in timeline_result.output

    query_result = runner.invoke(app, ["query", str(output_path), "keyframe"])
    assert query_result.exit_code == 0, query_result.output
