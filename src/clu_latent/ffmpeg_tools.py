"""ffmpeg/ffprobe wrappers, hardened via security.subprocess.run_tool.

Every ffmpeg/ffprobe invocation in this module goes through the shared
`run_tool` wrapper: resolved absolute executable paths, no `shell=True`,
`stdin=DEVNULL`, `-nostdin`, a `-protocol_whitelist file,pipe` for local
media, a hard timeout with process-group kill, and bounded stdout/stderr
capture. Callers never see a raw `subprocess` exception — failures are
always a structured `FfmpegExecutionError` carrying a `ToolResult` with a
bounded stderr tail.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .constants import SILENCE_MIN_DURATION_S, SILENCE_NOISE_DB
from .security.limits import DEFAULT_LIMITS, Limits
from .security.subprocess import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolResult,
    resolve_tool,
    run_tool,
)

# Backward-compatible aliases: existing callers (ingest.py, cli.py) catch
# these names directly.
FfmpegNotFoundError = ToolNotFoundError
CommandResult = ToolResult


class FfmpegExecutionError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails, times out, or is killed."""

    def __init__(self, message: str, result: ToolResult) -> None:
        super().__init__(message)
        self.result = result


def check_tools_available() -> None:
    """Verify ffmpeg and ffprobe are installed and on PATH.

    Raises FfmpegNotFoundError with a clear, user-facing message if
    either is missing.
    """
    missing: list[str] = []
    for name in ("ffmpeg", "ffprobe"):
        try:
            resolve_tool(name)
        except ToolNotFoundError:
            missing.append(name)
    if missing:
        raise FfmpegNotFoundError(
            "FFmpeg must be installed and on your PATH. Missing: "
            + ", ".join(missing)
            + ". Install it from https://ffmpeg.org/download.html "
            "or via your package manager (e.g. `brew install ffmpeg`)."
        )


def get_tool_version(name: str) -> str:
    """Return the first line of `<name> -version` output, or "unknown"."""
    try:
        executable = resolve_tool(name)
    except ToolNotFoundError:
        return "unknown"
    result = run_tool(executable, ["-version"], timeout_s=10.0)
    if not result.success or not result.stdout:
        return "unknown"
    first_line = result.stdout.splitlines()[0]
    # e.g. "ffmpeg version 6.1.1 Copyright (c) ..." -> "6.1.1"
    parts = first_line.split()
    if len(parts) >= 3 and parts[0] in ("ffmpeg", "ffprobe") and parts[1] == "version":
        return parts[2]
    return first_line


@dataclass
class ProbedMetadata:
    duration_ms: int
    width: int
    height: int
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    bitrate: int | None
    has_audio: bool
    raw_streams: list[dict] = field(default_factory=list)


def probe_video(
    source_path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[ProbedMetadata, ToolResult]:
    """Run ffprobe against `source_path` and return parsed metadata."""
    executable = resolve_tool("ffprobe")
    args = [
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-protocol_whitelist",
        "file,pipe",
        str(source_path),
    ]
    result = run_tool(executable, args, timeout_s=limits.ffprobe_timeout_s, limits=limits)
    if result.timed_out:
        raise FfmpegExecutionError(
            f"ffprobe timed out after {limits.ffprobe_timeout_s}s on {source_path}", result
        )
    if not result.success:
        raise FfmpegExecutionError(
            f"ffprobe failed on {source_path}: {result.stderr_tail.strip()}", result
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FfmpegExecutionError(
            f"ffprobe produced invalid JSON for {source_path}: {exc}", result
        ) from exc

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_s = fmt.get("duration")
    if duration_s is None and video_stream is not None:
        duration_s = video_stream.get("duration")
    duration_ms = int(round(float(duration_s) * 1000)) if duration_s is not None else 0

    width = int(video_stream.get("width", 0)) if video_stream else 0
    height = int(video_stream.get("height", 0)) if video_stream else 0

    fps = None
    if video_stream is not None:
        rate_str = video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate")
        if rate_str and rate_str != "0/0":
            num, _, den = rate_str.partition("/")
            try:
                num_f, den_f = float(num), float(den) if den else 1.0
                fps = round(num_f / den_f, 3) if den_f else None
            except ValueError:
                fps = None

    bitrate_raw = fmt.get("bit_rate")
    bitrate = int(bitrate_raw) if bitrate_raw is not None else None

    metadata = ProbedMetadata(
        duration_ms=duration_ms,
        width=width,
        height=height,
        fps=fps,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        bitrate=bitrate,
        has_audio=audio_stream is not None,
        raw_streams=streams,
    )
    return metadata, result


def extract_keyframes(
    source_path: Path, output_dir: Path, interval_ms: int, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[Path], ToolResult]:
    """Extract one JPG per `interval_ms` into `output_dir`, numbered from 0.

    Returns the sorted list of extracted frame paths and the raw ffmpeg
    tool result (for receipts logging).
    """
    executable = resolve_tool("ffmpeg")
    output_dir.mkdir(parents=True, exist_ok=True)
    interval_s = interval_ms / 1000.0
    pattern = str(output_dir / "%06d.jpg")

    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source_path),
        "-vf",
        f"fps=1/{interval_s}",
        "-start_number",
        "0",
        "-qscale:v",
        "2",
        pattern,
    ]
    result = run_tool(executable, args, timeout_s=limits.ffmpeg_timeout_s, limits=limits)
    if result.timed_out:
        raise FfmpegExecutionError(
            f"ffmpeg keyframe extraction timed out after {limits.ffmpeg_timeout_s}s "
            f"for {source_path}",
            result,
        )
    if not result.success:
        raise FfmpegExecutionError(
            f"ffmpeg keyframe extraction failed for {source_path}: {result.stderr_tail.strip()}",
            result,
        )

    frames = sorted(output_dir.glob("*.jpg"))
    return frames, result


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def parse_silence_intervals(stderr_text: str, *, total_duration_ms: int) -> list[tuple[int, int]]:
    """Parse ffmpeg `silencedetect` log lines into (start_ms, end_ms) pairs.

    Pure function — no I/O. `silencedetect` logs `silence_start:`/
    `silence_end:` lines to stderr as the filter runs. If the media ends
    while still inside a silent stretch, ffmpeg never logs a matching
    `silence_end` line, so any still-open interval is closed at
    `total_duration_ms`. Intervals are clipped to `[0, total_duration_ms]`
    and zero/negative-length results are dropped.
    """
    intervals: list[tuple[int, int]] = []
    pending_start_ms: int | None = None

    for line in stderr_text.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match is not None:
            pending_start_ms = round(float(start_match.group(1)) * 1000)
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match is not None and pending_start_ms is not None:
            end_ms = round(float(end_match.group(1)) * 1000)
            intervals.append((pending_start_ms, end_ms))
            pending_start_ms = None

    if pending_start_ms is not None:
        intervals.append((pending_start_ms, total_duration_ms))

    clipped: list[tuple[int, int]] = []
    for start_ms, end_ms in intervals:
        start_ms = max(0, min(start_ms, total_duration_ms))
        end_ms = max(0, min(end_ms, total_duration_ms))
        if end_ms > start_ms:
            clipped.append((start_ms, end_ms))
    return clipped


def complement_intervals(
    intervals: list[tuple[int, int]], total_duration_ms: int
) -> list[tuple[int, int]]:
    """Return the gaps between `intervals` across `[0, total_duration_ms]`.

    Pure function — no I/O. Used to derive `non_silent_audio` spans from
    `silence` spans (or vice versa). Overlapping/unsorted input intervals
    are merged defensively before computing gaps.
    """
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in ordered:
        if merged and start_ms <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
        else:
            merged.append((start_ms, end_ms))

    gaps: list[tuple[int, int]] = []
    cursor = 0
    for start_ms, end_ms in merged:
        if start_ms > cursor:
            gaps.append((cursor, start_ms))
        cursor = max(cursor, end_ms)
    if cursor < total_duration_ms:
        gaps.append((cursor, total_duration_ms))
    return gaps


def detect_silence(
    source_path: Path,
    duration_ms: int,
    *,
    noise_db: float = SILENCE_NOISE_DB,
    min_duration_s: float = SILENCE_MIN_DURATION_S,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[tuple[int, int]], ToolResult]:
    """Run ffmpeg's `silencedetect` audio filter and return silence spans.

    Non-ML, plain signal-level analysis — always run during ingest for
    any source with an audio stream (no opt-in flag needed). Output is
    discarded (`-f null -`); only the filter's stderr log lines matter.
    Requires the full (non-tail) `ToolResult.stderr` to reliably parse
    every silence interval on long videos.
    """
    executable = resolve_tool("ffmpeg")
    args = [
        "-hide_banner",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source_path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_duration_s}",
        "-f",
        "null",
        "-",
    ]
    result = run_tool(executable, args, timeout_s=limits.ffmpeg_timeout_s, limits=limits)
    if result.timed_out:
        raise FfmpegExecutionError(
            f"ffmpeg silencedetect timed out after {limits.ffmpeg_timeout_s}s for {source_path}",
            result,
        )
    if not result.success:
        raise FfmpegExecutionError(
            f"ffmpeg silencedetect failed for {source_path}: {result.stderr_tail.strip()}", result
        )

    intervals = parse_silence_intervals(result.stderr, total_duration_ms=duration_ms)
    return intervals, result


def extract_audio_wav(
    source_path: Path,
    output_wav_path: Path,
    *,
    sample_rate: int = 16000,
    limits: Limits = DEFAULT_LIMITS,
) -> ToolResult:
    """Extract mono PCM WAV audio at `sample_rate` Hz for VAD/transcription.

    Used only when a user opts into `--vad` or `--transcribe` — never
    called during a plain `ingest`. Writes to `output_wav_path`, which
    callers must place outside the staged package directory (it is
    scratch input for ML models, not a canonical package artifact).
    """
    executable = resolve_tool("ffmpeg")
    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        str(output_wav_path),
    ]
    result = run_tool(executable, args, timeout_s=limits.ffmpeg_timeout_s, limits=limits)
    if result.timed_out:
        raise FfmpegExecutionError(
            f"ffmpeg audio extraction timed out after {limits.ffmpeg_timeout_s}s for {source_path}",
            result,
        )
    if not result.success:
        raise FfmpegExecutionError(
            f"ffmpeg audio extraction failed for {source_path}: {result.stderr_tail.strip()}", result
        )
    return result
