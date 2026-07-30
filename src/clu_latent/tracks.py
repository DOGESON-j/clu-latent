"""Read/write helpers for tracks/*.jsonl files.

Every track file is a sequence of `EventEnvelope` records, one JSON
object per line, sorted by `t_start_ms`. Track files are canonical —
index/search.sqlite is rebuilt from them, never the other way around.
"""

from __future__ import annotations

import json
from pathlib import Path

from .event import EventEnvelope, Producer
from .security.jsonl import iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits


class TrackReadError(ValueError):
    """Raised when a track file contains a malformed or invalid record."""


def write_track_file(path: Path, events: list[EventEnvelope]) -> int:
    """Write `events` to `path` as JSONL, sorted by t_start_ms.

    Returns the number of records written. An empty list produces a
    valid, empty (zero-byte) file — empty track files are allowed.
    """
    ordered = sorted(events, key=lambda e: e.t_start_ms)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in ordered:
            handle.write(json.dumps(event.model_dump(mode="json")) + "\n")
    return len(ordered)


def read_track_file(path: Path, *, limits: Limits = DEFAULT_LIMITS) -> list[EventEnvelope]:
    """Read a JSONL track file into a list of validated EventEnvelope records.

    Reads via `security.jsonl.iter_jsonl_bounded`, so oversized lines or
    an oversized track raise `JsonlLimitError` instead of exhausting
    memory. A malformed line or a record that fails the shared event
    envelope schema raises `TrackReadError` — this is a strict reader
    (like validate.py), not the tolerant one used by reindex.py.
    """
    if not path.exists():
        return []
    events: list[EventEnvelope] = []
    for record in iter_jsonl_bounded(path, limits=limits):
        if record.error is not None:
            raise TrackReadError(f"{path}:{record.lineno}: {record.error}")
        try:
            events.append(EventEnvelope.model_validate(record.data))
        except Exception as exc:  # pydantic ValidationError
            raise TrackReadError(
                f"{path}:{record.lineno}: does not match the shared event envelope ({exc})"
            ) from exc
    return events


def make_keyframe_event(
    *,
    frame_index: int,
    t_start_ms: int,
    t_end_ms: int,
    image_relpath: str,
    width: int,
    height: int,
    producer_version: str,
) -> EventEnvelope:
    return EventEnvelope(
        id=f"kf_{frame_index:06d}",
        type="keyframe",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=Producer(name="ffmpeg", version=producer_version),
        confidence=None,
        payload={
            "path": image_relpath,
            "frame_index": frame_index,
            "width": width,
            "height": height,
        },
    )


def make_audio_present_event(
    *,
    t_end_ms: int,
    producer_version: str,
) -> EventEnvelope:
    return EventEnvelope(
        id="aud_000000",
        type="audio_track_present",
        t_start_ms=0,
        t_end_ms=t_end_ms,
        producer=Producer(name="ffprobe", version=producer_version),
        confidence=None,
        payload={"message": "Audio stream detected during ingest"},
    )


def make_silence_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    producer_version: str,
) -> EventEnvelope:
    """A span where ffmpeg's `silencedetect` filter found no audio signal.

    Phase 1.7A — non-ML, always computed for any source with audio.
    """
    return EventEnvelope(
        id=f"sil_{index:06d}",
        type="silence",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=Producer(name="ffmpeg_silencedetect", version=producer_version),
        confidence=None,
        payload={},
    )


def make_non_silent_audio_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    producer_version: str,
) -> EventEnvelope:
    """A span with audio signal (the complement of `silence` spans).

    Phase 1.7A — non-ML, always computed for any source with audio.
    """
    return EventEnvelope(
        id=f"nsil_{index:06d}",
        type="non_silent_audio",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=Producer(name="ffmpeg_silencedetect", version=producer_version),
        confidence=None,
        payload={},
    )


def make_speech_activity_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    producer_version: str,
    confidence: float | None = None,
) -> EventEnvelope:
    """A span where Silero VAD detected speech activity (no transcription).

    Phase 1.7B — ML, only computed when the user opts in via `--vad`.
    """
    return EventEnvelope(
        id=f"va_{index:06d}",
        type="speech_activity",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=Producer(name="silero-vad", version=producer_version),
        confidence=confidence,
        payload={},
    )


def make_speech_segment_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    text: str,
    language: str,
    producer_version: str,
    confidence: float | None = None,
) -> EventEnvelope:
    """A transcribed speech segment with text and detected language.

    Phase 1.7C — ML, only computed when the user opts in via
    `--transcribe`.
    """
    return EventEnvelope(
        id=f"ts_{index:06d}",
        type="speech_segment",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=Producer(name="faster-whisper", version=producer_version),
        confidence=confidence,
        payload={"text": text, "language": language},
    )
