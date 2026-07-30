"""Tests for Phase 1.7.1: duration-bound event validation and transcription clamping.

Covers two halves of the same guarantee — no event in any canonical
track may exceed the source media's known duration (plus a small
tolerance for codec/stream metadata rounding):

  - `validate.py` must fail a package that already contains an
    out-of-bounds event (regardless of how it got there).
  - `transcribe.clamp_segments_to_duration` (wired into `ingest.py`)
    must normalize faster-whisper's own untrusted timestamps before
    they ever become a canonical `speech_segment` event, and must
    record a receipt warning whenever it clamps or skips a segment.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from clu_latent import transcribe as transcribe_mod
from clu_latent.constants import EVENT_DURATION_TOLERANCE_MS
from clu_latent.ingest import ingest_video
from clu_latent.transcribe import SpeechSegment
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("duration_bounds_fixture")
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
    """Ingest a fresh, valid package (no transcription) for each test."""
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _load_manifest_dict(package_path) -> dict:
    return json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest_dict(package_path, manifest_dict: dict) -> None:
    (package_path / "manifest.json").write_text(
        json.dumps(manifest_dict, indent=2), encoding="utf-8"
    )


def _speech_track(manifest_dict: dict) -> dict:
    for track in manifest_dict["tracks"]:
        if track["name"] == "speech_events":
            return track
    raise AssertionError("speech_events track not found in manifest")


def _append_speech_event(package_path, *, t_start_ms: int, t_end_ms: int) -> None:
    """Hand-craft one speech_segment record and register it in the manifest.

    Isolates the duration-bound check being tested from an unrelated
    record_count mismatch error, mirroring the tamper pattern used in
    tests/test_validate_reindex.py.
    """
    record = {
        "id": "ts_000000",
        "type": "speech_segment",
        "t_start_ms": t_start_ms,
        "t_end_ms": t_end_ms,
        "producer": {"name": "faster-whisper", "version": "test-whisper-1.0"},
        "confidence": None,
        "payload": {"text": "hello", "language": "en"},
    }
    speech_path = package_path / "tracks" / "speech_events.jsonl"
    with speech_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    manifest_dict = _load_manifest_dict(package_path)
    _speech_track(manifest_dict)["record_count"] = 1
    _write_manifest_dict(package_path, manifest_dict)


# --- validate.py: duration-bound checks -------------------------------------


def test_validate_fails_when_event_end_beyond_duration(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    duration_ms = manifest_dict["source"]["duration_ms"]
    beyond = duration_ms + EVENT_DURATION_TOLERANCE_MS + 5000

    _append_speech_event(valid_package, t_start_ms=0, t_end_ms=beyond)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any(
        "speech_events" in err and "ts_000000" in err and "t_end_ms" in err and str(beyond) in err
        for err in report.errors
    )


def test_validate_fails_when_event_start_beyond_duration(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    duration_ms = manifest_dict["source"]["duration_ms"]
    beyond = duration_ms + EVENT_DURATION_TOLERANCE_MS + 5000

    _append_speech_event(valid_package, t_start_ms=beyond, t_end_ms=beyond + 100)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any(
        "speech_events" in err and "ts_000000" in err and "t_start_ms" in err and str(beyond) in err
        for err in report.errors
    )


# --- transcribe.py / ingest.py: clamp/skip normalization --------------------


def test_ingest_clamps_segment_ending_beyond_duration(tmp_path, tiny_video, monkeypatch):
    def _fake_transcribe_audio(wav_path, *, model_name, model_path):
        return [
            SpeechSegment(
                t_start_ms=0, t_end_ms=999_999, text="hello", language="en", confidence=0.9
            ),
        ]

    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(transcribe_mod, "get_engine_version", lambda: "test-whisper-1.0")

    output_path = tmp_path / "clamp.clulatent"
    result = ingest_video(tiny_video, output_path, enable_transcription=True)

    events = _read_jsonl(output_path / "tracks" / "speech_events.jsonl")
    assert len(events) == 1
    assert events[0]["t_end_ms"] == result.manifest.source.duration_ms

    report = validate_package(output_path)
    assert report.valid is True


def test_ingest_skips_segment_starting_beyond_duration(tmp_path, tiny_video, monkeypatch):
    def _fake_transcribe_audio(wav_path, *, model_name, model_path):
        return [
            SpeechSegment(
                t_start_ms=999_999,
                t_end_ms=1_000_500,
                text="unreachable",
                language="en",
                confidence=0.9,
            ),
        ]

    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(transcribe_mod, "get_engine_version", lambda: "test-whisper-1.0")

    output_path = tmp_path / "skip.clulatent"
    ingest_video(tiny_video, output_path, enable_transcription=True)

    events = _read_jsonl(output_path / "tracks" / "speech_events.jsonl")
    assert events == []


def test_ingest_writes_receipt_warning_for_clamped_segment(tmp_path, tiny_video, monkeypatch):
    def _fake_transcribe_audio(wav_path, *, model_name, model_path):
        return [
            SpeechSegment(
                t_start_ms=0, t_end_ms=999_999, text="hello", language="en", confidence=0.9
            ),
        ]

    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(transcribe_mod, "get_engine_version", lambda: "test-whisper-1.0")

    output_path = tmp_path / "clamp_receipt.clulatent"
    ingest_video(tiny_video, output_path, enable_transcription=True)

    receipts = _read_jsonl(output_path / "receipts" / "ingest.jsonl")
    transcribe_receipts = [r for r in receipts if r["operation"] == "transcribe_audio"]
    assert transcribe_receipts
    assert any(
        any("clamped" in warning for warning in receipt["warnings"])
        for receipt in transcribe_receipts
    )


def test_ingest_writes_receipt_warning_for_skipped_segment(tmp_path, tiny_video, monkeypatch):
    def _fake_transcribe_audio(wav_path, *, model_name, model_path):
        return [
            SpeechSegment(
                t_start_ms=999_999,
                t_end_ms=1_000_500,
                text="unreachable",
                language="en",
                confidence=0.9,
            ),
        ]

    monkeypatch.setattr(transcribe_mod, "transcribe_audio", _fake_transcribe_audio)
    monkeypatch.setattr(transcribe_mod, "get_engine_version", lambda: "test-whisper-1.0")

    output_path = tmp_path / "skip_receipt.clulatent"
    ingest_video(tiny_video, output_path, enable_transcription=True)

    receipts = _read_jsonl(output_path / "receipts" / "ingest.jsonl")
    transcribe_receipts = [r for r in receipts if r["operation"] == "transcribe_audio"]
    assert transcribe_receipts
    assert any(
        any("skipped" in warning for warning in receipt["warnings"])
        for receipt in transcribe_receipts
    )
