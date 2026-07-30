"""Import-guard tests for the Phase 1.7 lazy-ML-import patch.

The optional ML dependencies (torch, silero_vad, faster_whisper) must
never be imported as a side effect of importing the CLI or of a plain
`ingest` that does not opt into --vad/--transcribe. These tests prove
that by running a fresh subprocess with a `sys.meta_path` finder that
records any *attempt* to import one of those top-level packages — so the
assertion holds whether or not the extras happen to be installed, and
without requiring torch/silero/faster-whisper to be installed here.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap

import pytest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

_HEAVY_PACKAGES = ("torch", "silero_vad", "faster_whisper")


def _run_import_guard(body: str) -> None:
    """Run `body` in a fresh interpreter, failing if any heavy ML import is attempted."""
    script = textwrap.dedent(
        """
        import sys

        _BLOCKED = {blocked!r}
        _attempted = []

        class _ImportTracker:
            # Returning None defers to the real finders (so nothing is
            # actually blocked); we only record that an import was tried.
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in _BLOCKED:
                    _attempted.append(name)
                return None

        sys.meta_path.insert(0, _ImportTracker())

        {body}

        assert not _attempted, "heavy ML modules were imported: " + repr(_attempted)
        print("IMPORT_GUARD_OK")
        """
    ).format(blocked=_HEAVY_PACKAGES, body=body)

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "IMPORT_GUARD_OK" in result.stdout


def test_importing_cli_does_not_import_heavy_ml():
    _run_import_guard("import clu_latent.cli  # noqa: F401")


@pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)
def test_plain_ingest_does_not_import_heavy_ml(tmp_path):
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
        "testsrc=duration=1:size=160x120:rate=5",
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

    output_path = tmp_path / "out.clulatent"
    body = (
        "from pathlib import Path\n"
        "from clu_latent.ingest import ingest_video\n"
        f"ingest_video(Path({str(video_path)!r}), Path({str(output_path)!r}))"
    )
    _run_import_guard(body)
