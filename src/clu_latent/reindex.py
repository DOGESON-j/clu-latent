"""Rebuild index/search.sqlite from the canonical tracks/*.jsonl files.

index/search.sqlite is derived and disposable. Reindexing is read-only
with respect to everything canonical: it never writes to manifest.json,
tracks/*.jsonl, or sources/. It only deletes and recreates the sqlite
file itself.

Reindex is the *tolerant* reader: malformed individual JSONL lines are
skipped with a warning rather than aborting the whole run (validate.py
is the strict reader used to actually certify a package). Hard
resource limits (oversized lines, too many records) are never
tolerated by either path — those abort with ReindexError, since they
are resource-exhaustion protections, not data-quality checks. Every
manifest-declared path (track files, the derived index file itself) is
untrusted and is resolved via `security.paths.resolve_in_package`
before being opened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .event import EventEnvelope
from .index import build_search_index
from .manifest import Manifest
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package


class ReindexError(RuntimeError):
    """Raised when a package cannot be safely reindexed."""


@dataclass
class ReindexResult:
    package_path: Path
    sqlite_path: Path
    indexed_count: int
    tracks_indexed: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _read_track_file_tolerant(
    path: Path, *, limits: Limits
) -> tuple[list[EventEnvelope], list[str]]:
    """Read a JSONL track file, skipping malformed lines instead of aborting.

    Returns (valid_events, warnings). A line that is not valid JSON, is
    not a JSON object, or does not match the shared event envelope is
    skipped and recorded as a warning string (naming the file, line
    number, and error) rather than raising. This file is never
    rewritten — reading is read-only with respect to the track file
    itself. Exceeding a hard resource limit (line size, record count)
    still raises JsonlLimitError — that is a DoS protection, not a
    data-quality issue reindex should silently tolerate.
    """
    events: list[EventEnvelope] = []
    warnings: list[str] = []
    for record in iter_jsonl_bounded(path, limits=limits):
        if record.error is not None:
            warnings.append(f"{path}:{record.lineno}: {record.error}, line skipped")
            continue
        try:
            events.append(EventEnvelope.model_validate(record.data))
        except ValidationError as exc:
            warnings.append(
                f"{path}:{record.lineno}: does not match the shared event envelope, "
                f"line skipped ({exc})"
            )
            continue
    return events, warnings


def reindex_package(package_path: Path, *, limits: Limits = DEFAULT_LIMITS) -> ReindexResult:
    package_path = Path(package_path)

    if not package_path.exists() or not package_path.is_dir():
        raise ReindexError(f"Package not found: {package_path}")

    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise ReindexError(f"manifest.json not found in package: {package_path}")

    try:
        manifest = Manifest.from_json_file(manifest_path, limits=limits)
    except JsonlLimitError as exc:
        raise ReindexError(f"manifest.json exceeds size limits: {exc}") from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ReindexError(f"manifest.json is invalid: {exc}") from exc

    tracks: dict[str, list[EventEnvelope]] = {}
    tracks_indexed: dict[str, int] = {}
    warnings: list[str] = []
    for track in manifest.tracks:
        try:
            track_path = resolve_in_package(
                package_path, track.file, field_name=f"tracks[{track.name}].file"
            )
        except PathSecurityError as exc:
            raise ReindexError(str(exc)) from exc
        if not track_path.exists():
            raise ReindexError(f"track file listed in manifest is missing: {track.file}")
        try:
            events, track_warnings = _read_track_file_tolerant(track_path, limits=limits)
        except JsonlLimitError as exc:
            raise ReindexError(f"{track.file}: exceeds size limits: {exc}") from exc
        tracks[track.name] = events
        tracks_indexed[track.name] = len(events)
        warnings.extend(track_warnings)

    try:
        sqlite_path = resolve_in_package(
            package_path, manifest.index.file, field_name="index.file", for_write=True
        )
    except PathSecurityError as exc:
        raise ReindexError(str(exc)) from exc

    total = build_search_index(sqlite_path, tracks)

    return ReindexResult(
        package_path=package_path,
        sqlite_path=sqlite_path,
        indexed_count=total,
        tracks_indexed=tracks_indexed,
        warnings=warnings,
    )
