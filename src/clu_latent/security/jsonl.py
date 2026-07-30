"""Bounded readers for canonical JSONL tracks and whole-file JSON manifests.

Canonical JSONL and manifest.json are untrusted parser input — see the
project security policy. This module is the only place that reads
them: always streamed line-by-line for JSONL (never loaded fully into
memory), and always size-checked before a whole-file read for
manifest.json.

Hard limits (max line bytes, max records per track, max manifest
bytes) are resource-exhaustion protections and always raise
JsonlLimitError, regardless of `strict` mode. Malformed-JSON /
non-object handling is reported per-record via `JsonlRecord.error`, so
the same reader serves both validate.py (which turns any error into a
hard failure) and reindex.py (which turns it into a skip + warning).

Empty lines are always skipped silently and never yielded — this is a
deliberate Phase 1 behavior (not "reject", not "skip with warning"),
documented here and in docs/SECURITY.md, matching how track files have
always been read in this codebase.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .limits import DEFAULT_LIMITS, Limits


class JsonlLimitError(RuntimeError):
    """Raised when bounded JSONL/manifest reading exceeds a hard resource limit."""


@dataclass
class JsonlRecord:
    lineno: int
    raw: str
    data: Any | None
    error: str | None = None


def iter_jsonl_bounded(
    path: Path,
    *,
    limits: Limits = DEFAULT_LIMITS,
    require_object: bool = True,
) -> Iterator[JsonlRecord]:
    """Stream `path` line by line, enforcing hard size/count limits.

    Yields one JsonlRecord per non-blank line, in order. `data` is the
    parsed JSON value and `error` is None on success; on a malformed
    line, `data` is None and `error` describes the problem — the
    caller decides whether that is a hard failure (validate.py) or a
    skip-with-warning (reindex.py). Hard limits always raise.
    """
    record_count = 0
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            line = raw_line.rstrip("\n").rstrip("\r")
            if line.strip() == "":
                continue

            line_bytes = len(line.encode("utf-8"))
            if line_bytes > limits.max_jsonl_line_bytes:
                raise JsonlLimitError(
                    f"{path}:{lineno}: line exceeds max_jsonl_line_bytes "
                    f"({line_bytes} > {limits.max_jsonl_line_bytes})"
                )

            record_count += 1
            if record_count > limits.max_records_per_track:
                raise JsonlLimitError(
                    f"{path}: exceeds max_records_per_track ({limits.max_records_per_track})"
                )

            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                yield JsonlRecord(lineno=lineno, raw=line, data=None, error=f"invalid JSON ({exc})")
                continue

            if require_object and not isinstance(data, dict):
                yield JsonlRecord(
                    lineno=lineno,
                    raw=line,
                    data=None,
                    error=f"record is not a JSON object (got {type(data).__name__})",
                )
                continue

            yield JsonlRecord(lineno=lineno, raw=line, data=data, error=None)


def read_bytes_bounded(path: Path, *, max_bytes: int, field_name: str = "file") -> bytes:
    """Read a whole file into memory only if it is within `max_bytes`.

    Used for manifest.json (a single JSON document) — never for JSONL
    track files, which must always be streamed via iter_jsonl_bounded.
    """
    size = Path(path).stat().st_size
    if size > max_bytes:
        raise JsonlLimitError(f"{field_name} exceeds max_manifest_bytes ({size} > {max_bytes})")
    return Path(path).read_bytes()
