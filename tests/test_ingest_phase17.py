"""Wiring tests for Phase 1.7A/B/C in ingest.py.

Covers: silence/non_silent_audio always computed for audio sources
(non-ML, no opt-in needed); --vad and --transcribe are strictly
opt-in (never imported/used unless explicitly requested); a clean
IngestError when a user opts into --vad/--transcribe without the
corresponding optional ML dependency installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from clu_latent import transcribe as transcribe_mod
from clu_latent import vad as vad_mod
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.manifest import Manifest
from clu_latent.transcribe import SpeechSegment
from clu_latent.vad import SpeechActivitySegment

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("phase17_fixture")
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


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_ingest_audio_events_include_non_silent_audio(tmp_path, tiny_video):
    output_path = tmp_path / "silence.clulatent"
    ingest_video(tiny_video, output_path)

    events = _read_jsonl(output_path / "tracks" / "audio_events.jsonl")
    types = {e["type"] for e in events}
    assert "audio_track_present" in types
    assert "non_silent_audio" in types


def test_ingest_default_never_touches_vad_or_transcribe(tmp_path, tiny_video, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("vad/transcribe must not be called without opting in")

    monkeypatch.setattr(vad_mod, "detect_speech_activity", _boom)
    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _boom)

    output_path = tmp_path / "default.clulatent"
    result = ingest_video(tiny_video, output_path)

    assert result.manifest.status == "complete"
    speech_events = _read_jsonl(output_path / "tracks" / "speech_events.jsonl")
    assert speech_events == []


def test_ingest_with_vad_enabled_writes_speech_activity_events(tmp_path, tiny_video, monkeypatch):
    def _fake_detect_speech_activity(wav_path):
        return [
            SpeechActivitySegment(t_start_ms=100, t_end_ms=500),
            SpeechActivitySegment(t_start_ms=800, t_end_ms=1500),
        ]

    monkeypatch.setattr(vad_mod, "detect_speech_activity", _fake_detect_speech_activity)
    monkeypatch.setattr(vad_mod, "get_engine_version", lambda: "test-vad-1.0")

    output_path = tmp_path / "vad.clulatent"
    result = ingest_video(tiny_video, output_path, enable_vad=True)

    events = _read_jsonl(output_path / "tracks" / "speech_events.jsonl")
    assert len(events) == 2
    assert all(e["type"] == "speech_activity" for e in events)
    assert all(e["producer"]["name"] == "silero-vad" for e in events)
    assert all(e["producer"]["version"] == "test-vad-1.0" for e in events)

    manifest = Manifest.from_json_file(output_path / "manifest.json")
    speech_track = next(t for t in manifest.tracks if t.name == "speech_events")
    assert speech_track.record_count == 2


def test_ingest_with_transcription_enabled_writes_speech_segment_events(
    tmp_path, tiny_video, monkeypatch
):
    def _fake_transcribe_audio(wav_path, *, model_name, model_path):
        return [
            SpeechSegment(
                t_start_ms=0, t_end_ms=900, text="hello", language="en", confidence=0.9
            ),
        ]

    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(transcribe_mod, "get_engine_version", lambda: "test-whisper-1.0")

    output_path = tmp_path / "transcribe.clulatent"
    ingest_video(tiny_video, output_path, enable_transcription=True)

    events = _read_jsonl(output_path / "tracks" / "speech_events.jsonl")
    assert len(events) == 1
    assert events[0]["type"] == "speech_segment"
    assert events[0]["producer"]["name"] == "faster-whisper"
    assert events[0]["payload"]["text"] == "hello"
    assert events[0]["payload"]["language"] == "en"


def test_ingest_vad_enabled_but_not_installed_raises_clean_error(tmp_path, tiny_video):
    output_path = tmp_path / "vad_missing.clulatent"

    with pytest.raises(IngestError, match="Silero VAD"):
        ingest_video(tiny_video, output_path, enable_vad=True)

    assert not output_path.exists()
    assert list(tmp_path.glob(".vad_missing.clulatent.tmp-*")) == []


def test_ingest_transcribe_enabled_but_not_installed_raises_clean_error(tmp_path, tiny_video):
    output_path = tmp_path / "whisper_missing.clulatent"

    with pytest.raises(IngestError, match="faster-whisper"):
        ingest_video(tiny_video, output_path, enable_transcription=True)

    assert not output_path.exists()
    assert list(tmp_path.glob(".whisper_missing.clulatent.tmp-*")) == []
