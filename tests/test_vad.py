"""Tests for the optional Silero VAD integration (Phase 1.7B).

silero-vad/torch are not installed in the base test environment, so
`is_available()`/`get_engine_version()`/`detect_speech_activity()` are
exercised against their real "not installed" code path. The success
path is exercised via monkeypatching the module's placeholder names
(bound to None on ImportError), which is only possible because vad.py
explicitly binds those names rather than leaving them undefined.
"""

from __future__ import annotations

import pytest

from clu_latent import vad


def test_is_available_false_when_not_installed():
    assert vad.is_available() is False


def test_get_engine_version_unknown_when_not_installed():
    assert vad.get_engine_version() == "unknown"


def test_detect_speech_activity_raises_when_unavailable(tmp_path):
    fake_wav = tmp_path / "audio.wav"
    fake_wav.write_bytes(b"")

    with pytest.raises(vad.VadUnavailableError):
        vad.detect_speech_activity(fake_wav)


def test_detect_speech_activity_uses_monkeypatched_engine(tmp_path, monkeypatch):
    fake_wav = tmp_path / "audio.wav"
    fake_wav.write_bytes(b"")

    class _FakeModel:
        pass

    def _fake_load_silero_vad():
        return _FakeModel()

    def _fake_read_audio(path):
        assert path == str(fake_wav)
        return "wav-tensor"

    def _fake_get_speech_timestamps(wav, model, return_seconds=True):
        assert wav == "wav-tensor"
        assert isinstance(model, _FakeModel)
        assert return_seconds is True
        return [{"start": 0.5, "end": 1.25}, {"start": 2.0, "end": 2.75}]

    monkeypatch.setattr(vad, "torch", object())
    monkeypatch.setattr(vad, "load_silero_vad", _fake_load_silero_vad)
    monkeypatch.setattr(vad, "read_audio", _fake_read_audio)
    monkeypatch.setattr(vad, "get_speech_timestamps", _fake_get_speech_timestamps)

    segments = vad.detect_speech_activity(fake_wav)

    assert [(seg.t_start_ms, seg.t_end_ms) for seg in segments] == [(500, 1250), (2000, 2750)]
