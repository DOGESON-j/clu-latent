"""Tests for the optional faster-whisper integration (Phase 1.7C).

faster-whisper is not installed in the base test environment, so
`is_available()`/`get_engine_version()`/`transcribe_audio()` are
exercised against their real "not installed" code path. The success
path is exercised via monkeypatching `WhisperModel` (bound to None on
ImportError), which is only possible because transcribe.py explicitly
binds that name rather than leaving it undefined.
"""

from __future__ import annotations

import pytest

from clu_latent import transcribe


def test_is_available_false_when_not_installed():
    assert transcribe.is_available() is False


def test_get_engine_version_unknown_when_not_installed():
    assert transcribe.get_engine_version() == "unknown"


def test_transcribe_audio_raises_when_unavailable(tmp_path):
    fake_wav = tmp_path / "audio.wav"
    fake_wav.write_bytes(b"")

    with pytest.raises(transcribe.WhisperUnavailableError):
        transcribe.transcribe_audio(fake_wav)


def test_transcribe_audio_uses_monkeypatched_engine(tmp_path, monkeypatch):
    fake_wav = tmp_path / "audio.wav"
    fake_wav.write_bytes(b"")

    class _FakeSegment:
        def __init__(self, start, end, text, avg_logprob):
            self.start = start
            self.end = end
            self.text = text
            self.avg_logprob = avg_logprob

    class _FakeInfo:
        language = "en"

    class _FakeWhisperModel:
        def __init__(self, model_path_or_name, device, compute_type):
            self.model_path_or_name = model_path_or_name
            self.device = device
            self.compute_type = compute_type

        def transcribe(self, path):
            assert path == str(fake_wav)
            return (
                [
                    _FakeSegment(0.0, 1.2, " hello world ", -0.1),
                    _FakeSegment(1.2, 2.5, " goodbye ", -0.2),
                ],
                _FakeInfo(),
            )

    monkeypatch.setattr(transcribe, "WhisperModel", _FakeWhisperModel)

    segments = transcribe.transcribe_audio(fake_wav, model_name="base")

    assert [(s.t_start_ms, s.t_end_ms, s.text, s.language) for s in segments] == [
        (0, 1200, "hello world", "en"),
        (1200, 2500, "goodbye", "en"),
    ]
    assert all(0.0 <= s.confidence <= 1.0 for s in segments)


def test_transcribe_audio_prefers_local_model_path(tmp_path, monkeypatch):
    fake_wav = tmp_path / "audio.wav"
    fake_wav.write_bytes(b"")
    seen: dict[str, str] = {}

    class _FakeInfo:
        language = "fr"

    class _FakeWhisperModel:
        def __init__(self, model_path_or_name, device, compute_type):
            seen["model_path_or_name"] = model_path_or_name

        def transcribe(self, path):
            return [], _FakeInfo()

    monkeypatch.setattr(transcribe, "WhisperModel", _FakeWhisperModel)

    transcribe.transcribe_audio(fake_wav, model_name="base", model_path="/local/model/dir")

    assert seen["model_path_or_name"] == "/local/model/dir"
