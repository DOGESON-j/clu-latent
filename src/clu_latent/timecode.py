"""Millisecond <-> human-readable timecode formatting.

CLULatent's timebase is fixed to integer milliseconds (see manifest.py's
`timebase` field). This module only formats for display; it never becomes
a source of truth for timing.
"""

from __future__ import annotations


def ms_to_timecode(ms: int) -> str:
    """Format integer milliseconds as "MM:SS.mmm".

    Minutes are not capped at 60 (e.g. a 90-minute video renders as
    "90:00.000", not "1:30:00.000") to keep the formatter simple and
    unambiguous for Phase 1.
    """
    if not isinstance(ms, int):
        raise TypeError(f"ms must be an int, got {type(ms).__name__}")
    if ms < 0:
        raise ValueError(f"ms must be non-negative, got {ms}")

    total_seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"
