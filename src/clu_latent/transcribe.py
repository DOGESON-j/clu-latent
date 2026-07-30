"""Optional faster-whisper integration (Phase 1.7C): multilingual transcription.

faster-whisper is an OPTIONAL ML dependency — the base clulatent package
and `ingest` command work fully without it installed, and importing this
module never imports faster-whisper. The heavy import happens lazily,
only inside `transcribe_audio()` (i.e. only when the user passes
`--transcribe`). By default faster-whisper resolves a named model via
Hugging Face Hub, which may download weights on first use; passing
`model_path` (a local model directory) avoids any network access
entirely. Either way this only ever runs as a direct, traceable
consequence of the user explicitly opting in — never automatically
during a plain `ingest`. No cloud APIs: transcription itself always
runs locally, whichever way the model was obtained.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .constants import EVENT_DURATION_TOLERANCE_MS

# Placeholder for the optional faster-whisper symbol. It stays None until
# `_ensure_engine_loaded()` binds it on first real use (or a test
# replaces it via monkeypatch). Importing this module never imports
# faster-whisper — that only happens inside transcribe_audio().
WhisperModel = None


class WhisperUnavailableError(RuntimeError):
    """Raised when faster-whisper is requested via --transcribe but not installed."""


@dataclass
class SpeechSegment:
    t_start_ms: int
    t_end_ms: int
    text: str
    language: str
    confidence: float | None = None


def is_available() -> bool:
    """True if the optional `faster-whisper` dependency is installed.

    Uses `importlib.util.find_spec` so merely asking whether the engine
    is present never imports faster-whisper as a side effect.
    """
    return importlib.util.find_spec("faster_whisper") is not None


def get_engine_version() -> str:
    """Return the installed `faster-whisper` package version, or "unknown"."""
    try:
        return version("faster-whisper")
    except PackageNotFoundError:
        return "unknown"


def _ensure_engine_loaded() -> None:
    """Import faster-whisper on first use, binding the module-level name.

    A no-op if `WhisperModel` is already bound (loaded earlier, or
    replaced by a test monkeypatch). Raises WhisperUnavailableError —
    never a raw ImportError — when the optional dependency is not
    installed.
    """
    global WhisperModel
    if WhisperModel is not None:
        return
    try:
        from faster_whisper import WhisperModel as _WhisperModel
    except ImportError as exc:
        raise WhisperUnavailableError(
            "faster-whisper is not installed. Install the optional 'whisper' "
            "extra (`pip install clu-latent[whisper]`) to use --transcribe."
        ) from exc
    WhisperModel = _WhisperModel


def transcribe_audio(
    wav_path: Path,
    *,
    model_name: str = "base",
    model_path: str | None = None,
) -> list[SpeechSegment]:
    """Transcribe a mono WAV file with faster-whisper.

    Imports faster-whisper lazily on first call — never at module import
    time — so a plain ingest that does not pass --transcribe never loads
    it. Raises WhisperUnavailableError if the optional dependency is not
    installed. `model_path`, when given, loads a local model directory
    (no network access); without it, `model_name` is resolved via
    Hugging Face Hub (which may download weights on first use). This
    function only ever runs as a direct consequence of the user
    explicitly passing --transcribe.
    """
    _ensure_engine_loaded()
    model = WhisperModel(model_path or model_name, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(str(wav_path))
    language = info.language or "unknown"

    segments: list[SpeechSegment] = []
    for seg in segments_iter:
        confidence = None
        if seg.avg_logprob is not None:
            confidence = max(0.0, min(1.0, math.exp(seg.avg_logprob)))
        segments.append(
            SpeechSegment(
                t_start_ms=round(float(seg.start) * 1000),
                t_end_ms=round(float(seg.end) * 1000),
                text=seg.text.strip(),
                language=language,
                confidence=confidence,
            )
        )
    return segments


def clamp_segments_to_duration(
    segments: list[SpeechSegment],
    duration_ms: int,
    *,
    tolerance_ms: int = EVENT_DURATION_TOLERANCE_MS,
) -> tuple[list[SpeechSegment], list[str]]:
    """Normalize faster-whisper output against the source's known duration (Phase 1.7.1).

    A model's own decode/timestamp output is untrusted the same way any
    other producer-generated data is: faster-whisper can (and, in
    practice, sometimes does) return a segment ending well past the
    ffprobe-reported duration of the source audio/video. Segments are
    never allowed to become canonical track data unmodified — this is
    the normalization step, called by ingest.py before any
    `speech_segment` event is constructed:

      - `t_start_ms` beyond `duration_ms + tolerance_ms`: the whole
        segment is dropped (a segment that starts after the source
        media even nominally ends carries no usable information).
      - `t_end_ms` beyond `duration_ms + tolerance_ms` (but `t_start_ms`
        is within bounds): clamped down to `duration_ms` exactly.
      - if clamping would leave `t_end_ms <= t_start_ms` (a segment
        entirely inside the tolerance-only slack past the true
        duration): the segment is dropped instead of kept as a
        zero/negative-length span.

    Returns `(accepted_segments, warnings)` — never raises; every
    dropped/clamped segment produces one human-readable warning string
    for the caller to fold into an ingest receipt.
    """
    limit_ms = duration_ms + tolerance_ms
    accepted: list[SpeechSegment] = []
    warnings: list[str] = []

    for index, segment in enumerate(segments):
        if segment.t_start_ms > limit_ms:
            warnings.append(
                f"speech_segment[{index}] skipped: t_start_ms ({segment.t_start_ms}) "
                f"exceeds source duration_ms ({duration_ms}) + tolerance_ms ({tolerance_ms})"
            )
            continue

        t_end_ms = segment.t_end_ms
        if t_end_ms > limit_ms:
            clamped_end_ms = duration_ms
            if clamped_end_ms <= segment.t_start_ms:
                warnings.append(
                    f"speech_segment[{index}] skipped: t_end_ms ({segment.t_end_ms}) exceeds "
                    f"source duration_ms ({duration_ms}) + tolerance_ms ({tolerance_ms}), and "
                    f"clamping to duration_ms would leave t_end_ms <= t_start_ms "
                    f"({segment.t_start_ms})"
                )
                continue
            warnings.append(
                f"speech_segment[{index}] clamped: t_end_ms ({segment.t_end_ms}) exceeds source "
                f"duration_ms ({duration_ms}) + tolerance_ms ({tolerance_ms}), clamped to "
                f"{clamped_end_ms}"
            )
            t_end_ms = clamped_end_ms

        accepted.append(replace(segment, t_end_ms=t_end_ms))

    return accepted, warnings
