"""CLI-level integration tests: untrusted package strings must never let
Rich markup or raw ANSI reach the rendered terminal output unescaped.

Uses a real ffmpeg-generated tiny video to build a genuine package, then
tampers with manifest/track fields to inject Rich markup and ANSI escape
sequences before running each read-only CLI command against it. Skips
cleanly if ffmpeg/ffprobe are not available.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.ingest import ingest_video

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

MARKUP_PAYLOAD = "[bold red]INJECTED[/bold red]"
ANSI_PAYLOAD = "\x1b[31mred\x1b[0m"


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clulatent_cli_injection_fixture")
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
        "testsrc=duration=1:size=320x240:rate=5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
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
def tampered_package(tmp_path, tiny_video):
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    package_path = result.package_path

    manifest_path = package_path / "manifest.json"
    manifest_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_dict["source"]["filename"] = MARKUP_PAYLOAD + ANSI_PAYLOAD
    manifest_dict["package_id"] = MARKUP_PAYLOAD
    manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")

    keyframes_path = package_path / "tracks" / "keyframes.jsonl"
    lines = [line for line in keyframes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    first = json.loads(lines[0])
    first["payload"]["path"] = MARKUP_PAYLOAD + ANSI_PAYLOAD
    lines[0] = json.dumps(first)
    keyframes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return package_path


def _assert_no_raw_injection(output: str) -> None:
    # The literal bracketed markup text is expected to survive as inert
    # text -- what must never happen is the injected ANSI/markup actually
    # taking effect (raw ANSI bytes reaching the terminal, or Rich
    # interpreting the markup and emitting its own style codes for it).
    assert "\x1b[31m" not in output
    assert "\x1b[1;31m" not in output


def test_inspect_escapes_untrusted_markup(tampered_package):
    runner = CliRunner()
    result = runner.invoke(app, ["inspect", str(tampered_package)])
    assert result.exit_code == 0
    _assert_no_raw_injection(result.output)
    assert "INJECTED" in result.output


def test_timeline_escapes_untrusted_markup(tampered_package):
    runner = CliRunner()
    result = runner.invoke(app, ["timeline", str(tampered_package)])
    assert result.exit_code == 0
    _assert_no_raw_injection(result.output)
    assert "INJECTED" in result.output
