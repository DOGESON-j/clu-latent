"""Optional Silero VAD integration (Phase 1.7B): speech/no-speech detection.

Silero VAD is an OPTIONAL ML dependency — the base clulatent package and
`ingest` command work fully without it installed, and importing this
module never imports torch/silero-vad. The heavy imports happen lazily,
only inside `detect_speech_activity()` (i.e. only when the user passes
`--vad`). Silero VAD ships its own model weights inside the PyPI
package, so using it never triggers a network download. No cloud APIs.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

# Placeholders for the optional silero-vad/torch symbols. They stay None
# until `_ensure_engine_loaded()` binds them on first real use (or a test
# replaces them via monkeypatch). Importing this module never imports
# torch/silero-vad — that only happens inside detect_speech_activity().
torch = None
get_speech_timestamps = None
load_silero_vad = None
read_audio = None


class VadUnavailableError(RuntimeError):
    """Raised when Silero VAD is requested via --vad but not installed."""


@dataclass
class SpeechActivitySegment:
    t_start_ms: int
    t_end_ms: int


def is_available() -> bool:
    """True if the optional `silero-vad` (and `torch`) dependency is installed.

    Uses `importlib.util.find_spec` so merely asking whether the engine
    is present never imports torch/silero-vad as a side effect.
    """
    return (
        importlib.util.find_spec("silero_vad") is not None
        and importlib.util.find_spec("torch") is not None
    )


def get_engine_version() -> str:
    """Return the installed `silero-vad` package version, or "unknown"."""
    try:
        return version("silero-vad")
    except PackageNotFoundError:
        return "unknown"


def _ensure_engine_loaded() -> None:
    """Import silero-vad/torch on first use, binding the module-level names.

    A no-op if the names are already bound (loaded earlier, or replaced
    by a test monkeypatch). Raises VadUnavailableError — never a raw
    ImportError — when the optional dependency is not installed.
    """
    global torch, get_speech_timestamps, load_silero_vad, read_audio
    if load_silero_vad is not None:
        return
    try:
        import torch as _torch
        from silero_vad import (
            get_speech_timestamps as _get_speech_timestamps,
            load_silero_vad as _load_silero_vad,
            read_audio as _read_audio,
        )
    except ImportError as exc:
        raise VadUnavailableError(
            "Silero VAD is not installed. Install the optional 'vad' extra "
            "(`pip install clu-latent[vad]`) to use --vad."
        ) from exc
    torch = _torch
    get_speech_timestamps = _get_speech_timestamps
    load_silero_vad = _load_silero_vad
    read_audio = _read_audio


def detect_speech_activity(wav_path: Path) -> list[SpeechActivitySegment]:
    """Run Silero VAD over a mono WAV file and return speech spans.

    Imports silero-vad/torch lazily on first call — never at module
    import time — so a plain ingest that does not pass --vad never loads
    them. Raises VadUnavailableError if the optional dependency is not
    installed. Loads the model bundled inside the `silero-vad` PyPI
    package — no network access, no automatic download; this function
    only ever runs as a direct consequence of the user explicitly
    passing --vad.
    """
    _ensure_engine_loaded()
    model = load_silero_vad()
    wav = read_audio(str(wav_path))
    timestamps = get_speech_timestamps(wav, model, return_seconds=True)

    segments: list[SpeechActivitySegment] = []
    for ts in timestamps:
        t_start_ms = round(float(ts["start"]) * 1000)
        t_end_ms = round(float(ts["end"]) * 1000)
        segments.append(SpeechActivitySegment(t_start_ms=t_start_ms, t_end_ms=t_end_ms))
    return segments
