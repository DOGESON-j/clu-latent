"""Tests for Phase 1.7A: ffmpeg silencedetect parsing and audio extraction.

The pure parsing/interval-math functions (`parse_silence_intervals`,
`complement_intervals`) are tested without ffmpeg. `detect_silence` and
`extract_audio_wav` are tested against a real ffmpeg invocation, skipped
cleanly if ffmpeg/ffprobe are not available.
"""

from __future__ import annotations

import shutil
import subprocess
import wave

import pytest

from clu_latent.ffmpeg_tools import (
    complement_intervals,
    detect_silence,
    extract_audio_wav,
    parse_silence_intervals,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- parse_silence_intervals (pure) ----------------------------------------


def test_parse_silence_intervals_basic():
    stderr_text = (
        "[silencedetect @ 0x1] silence_start: 1.5\n"
        "[silencedetect @ 0x1] silence_end: 3.25 | silence_duration: 1.75\n"
    )
    assert parse_silence_intervals(stderr_text, total_duration_ms=5000) == [(1500, 3250)]


def test_parse_silence_intervals_unclosed_at_eof_closes_at_duration():
    stderr_text = "[silencedetect @ 0x1] silence_start: 4.0\n"
    assert parse_silence_intervals(stderr_text, total_duration_ms=5000) == [(4000, 5000)]


def test_parse_silence_intervals_no_matches_returns_empty():
    assert parse_silence_intervals("nothing here", total_duration_ms=5000) == []


def test_parse_silence_intervals_multiple_spans():
    stderr_text = (
        "silence_start: 0.0\n"
        "silence_end: 1.0 | silence_duration: 1.0\n"
        "silence_start: 3.0\n"
        "silence_end: 4.5 | silence_duration: 1.5\n"
    )
    assert parse_silence_intervals(stderr_text, total_duration_ms=6000) == [
        (0, 1000),
        (3000, 4500),
    ]


def test_parse_silence_intervals_clips_to_duration():
    stderr_text = "silence_start: 1.0\nsilence_end: 100.0 | silence_duration: 99.0\n"
    assert parse_silence_intervals(stderr_text, total_duration_ms=5000) == [(1000, 5000)]


# --- complement_intervals (pure) --------------------------------------------


def test_complement_intervals_basic():
    assert complement_intervals([(1000, 2000)], 5000) == [(0, 1000), (2000, 5000)]


def test_complement_intervals_empty_silence_returns_full_span():
    assert complement_intervals([], 5000) == [(0, 5000)]


def test_complement_intervals_covers_entire_duration_returns_empty():
    assert complement_intervals([(0, 5000)], 5000) == []


def test_complement_intervals_merges_overlaps():
    assert complement_intervals([(0, 2000), (1000, 3000)], 5000) == [(3000, 5000)]


def test_complement_intervals_unsorted_input():
    assert complement_intervals([(3000, 4000), (0, 1000)], 5000) == [
        (1000, 3000),
        (4000, 5000),
    ]


# --- real-ffmpeg tests -------------------------------------------------------


@pytest.fixture(scope="module")
def silence_then_tone_wav(tmp_path_factory):
    """1.5s of true silence followed by 1.5s of a 440Hz tone."""
    directory = tmp_path_factory.mktemp("silence_fixture")
    wav_path = directory / "silence_then_tone.wav"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=8000:cl=mono:d=1.5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=8000:duration=1.5",
        "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[out]",
        "-map",
        "[out]",
        str(wav_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return wav_path


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment")
def test_detect_silence_finds_leading_silence(silence_then_tone_wav):
    intervals, result = detect_silence(silence_then_tone_wav, duration_ms=3000)

    assert result.success
    assert len(intervals) == 1
    start_ms, end_ms = intervals[0]
    assert start_ms == 0
    assert 1300 <= end_ms <= 1700


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment")
def test_extract_audio_wav_produces_mono_16k_wav(tmp_path, silence_then_tone_wav):
    wav_path = tmp_path / "extracted.wav"
    result = extract_audio_wav(silence_then_tone_wav, wav_path)

    assert result.success
    assert wav_path.exists()
    with wave.open(str(wav_path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16000
