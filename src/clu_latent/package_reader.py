"""Phase 3.18: the official, read-only reader/parser for a `.clulatent` package.

Until this module, opening a `.clulatent` package meant knowing its
internal folder convention (`manifest.json`, `tracks/*.jsonl`,
`receipts/`, `lock/`) and calling a scattering of per-lane retrieval
modules (`keyframe_retrieval.py`, `visual_change_retrieval.py`,
`evidence_bundle_retrieval.py`, ...), each of which independently
re-derived "manifest -> track descriptor -> safe file resolve -> bounded
JSONL read".

`PackageReader` wraps that existing structure in one small, stable,
read-only spine so a caller can open a package by path and inspect its
manifest, tracks, events, receipts, lock state, and validation status
**without knowing any filename**. Track lookup is keyed off the track
*names* declared in `manifest.json`, never off a hardcoded path.

Boundaries (see docs/PHASE_3_18_PACKAGE_READER_API_V0.md):

- This is a **reader**, not the **validator**. `reader.validate()`
  delegates to `validate.validate_package`; the reader never
  re-implements a validity rule and never judges validity itself.
- Every opened package is untrusted input. Track/receipt files are
  resolved through `security.paths.resolve_in_package` (containment- and
  symlink-checked) and read through the existing bounded readers.
- The reader is strictly read-only: it never writes, never creates
  receipts, never touches lock state, never rebuilds the index, and
  never runs FFmpeg, Pillow, ML, network, or model calls.
- The reader exposes records ingest/writers already stored. It never
  interprets what a keyframe depicts or what audio means. Detected is
  not trusted; generated is not canonical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from . import tracks as tracks_mod
from .analysis_lanes import ANALYSIS_LANE_NAMES
from .constants import (
    AGENT_REVIEW_TRACK_FILE,
    AUDIO_DIGEST_TRACK_FILE,
    AUDIO_EVENTS_TRACK_FILE,
    CHANGED_REGION_TRACK_FILE,
    EVIDENCE_BUNDLE_TRACK_FILE,
    KEYFRAMES_TRACK_FILE,
    RECEIPTS_DIR,
    REVIEW_EVENTS_TRACK_NAME,
    SEMANTIC_EVENTS_TRACK_FILE,
    SPEECH_EVENTS_TRACK_FILE,
    VISUAL_CHANGE_TRACK_FILE,
)
from .manifest import Manifest, TrackDescriptor
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

__all__ = [
    "PackageReaderError",
    "MalformedPackageError",
    "EventRecord",
    "TrackHandle",
    "ReceiptHandle",
    "PackageReader",
    "open_package",
    "KNOWN_TRACK_NAMES",
]


class PackageReaderError(ValueError):
    """Base error for any package-reader failure (bad path, bad manifest, ...)."""


class MalformedPackageError(PackageReaderError):
    """Raised when a package is structurally broken: unsafe path, corrupt JSONL, etc."""


def _track_name_from_file(track_file: str) -> str:
    """`"tracks/keyframes.jsonl"` -> `"keyframes"` (the manifest track name)."""
    return PurePosixPath(track_file).stem


# The known CLULatent canonical track catalog. A track whose name is in
# this set is a recognized lane of the format; a manifest may still
# declare a track outside it (hand-authored, or a lane this reader
# version predates), and the reader will list/read it but flag it
# `known=False` so a caller never mistakes an arbitrary declared track
# for a trusted canonical lane. Trust is the validator's call, not the
# reader's. Derived from constants (single source of truth) so it stays
# in sync with the format without a second hardcoded list.
KNOWN_TRACK_NAMES: frozenset[str] = frozenset(
    {
        _track_name_from_file(KEYFRAMES_TRACK_FILE),
        _track_name_from_file(AUDIO_EVENTS_TRACK_FILE),
        _track_name_from_file(SPEECH_EVENTS_TRACK_FILE),
        _track_name_from_file(SEMANTIC_EVENTS_TRACK_FILE),
        REVIEW_EVENTS_TRACK_NAME,
        _track_name_from_file(AUDIO_DIGEST_TRACK_FILE),
        _track_name_from_file(VISUAL_CHANGE_TRACK_FILE),
        _track_name_from_file(CHANGED_REGION_TRACK_FILE),
        _track_name_from_file(EVIDENCE_BUNDLE_TRACK_FILE),
        _track_name_from_file(AGENT_REVIEW_TRACK_FILE),
    }
    | set(ANALYSIS_LANE_NAMES)
)


@dataclass(frozen=True)
class EventRecord:
    """One track event, exposed as read-only structured data.

    `raw` is the full stored record dict; the named fields are lifted
    from it for convenience. `timestamp_ms` is an alias for
    `t_start_ms` (the event's start on the package timebase) so callers
    can query/sort by a single "when" field.
    """

    id: str
    type: str
    track_name: str
    t_start_ms: int
    t_end_ms: int
    producer: dict[str, Any]
    confidence: float | None
    payload: dict[str, Any]
    raw: dict[str, Any]

    @property
    def timestamp_ms(self) -> int:
        return self.t_start_ms


@dataclass(frozen=True)
class TrackHandle:
    """A manifest-declared track, described without the caller reading the manifest."""

    name: str
    file: str
    schema_id: str
    schema_version: str
    record_count: int
    sorted_by: str
    known: bool


@dataclass
class ReceiptHandle:
    """A receipt JSONL file present in the package, read-only.

    `records()` performs a bounded read on demand — receipts are not
    loaded until a caller asks for them.
    """

    name: str
    file: str
    exists: bool
    _package_path: Path = field(repr=False, default_factory=Path)
    _limits: Limits = field(repr=False, default=DEFAULT_LIMITS)

    def records(self) -> list[dict[str, Any]]:
        """Return this receipt file's records as plain dicts (bounded read).

        Raises `MalformedPackageError` on a corrupt/oversized receipt
        line rather than silently skipping it. Returns `[]` if the file
        does not exist (an absent optional receipt is not an error).
        """
        if not self.exists:
            return []
        try:
            resolved = resolve_in_package(
                self._package_path, self.file, field_name=f"receipts[{self.name}]"
            )
        except PathSecurityError as exc:
            raise MalformedPackageError(str(exc)) from exc
        if not resolved.exists():
            return []
        out: list[dict[str, Any]] = []
        try:
            for record in iter_jsonl_bounded(resolved, limits=self._limits):
                if record.error is not None:
                    raise MalformedPackageError(
                        f"{self.file}:{record.lineno}: {record.error}"
                    )
                out.append(record.data)
        except JsonlLimitError as exc:
            raise MalformedPackageError(f"{self.file}: {exc}") from exc
        return out


class PackageReader:
    """Read-only, filename-agnostic access to a `.clulatent` package.

    Open with `PackageReader.open(path)` or the module-level
    `open_package(path)`. Opening loads and schema-validates
    `manifest.json` (bounded); it does not run the full validator, probe
    media, or read every track. The reader never mutates the package.
    """

    def __init__(
        self,
        package_path: Path,
        manifest: Manifest,
        *,
        strict: bool = True,
        limits: Limits = DEFAULT_LIMITS,
    ) -> None:
        self._path = package_path
        self._manifest = manifest
        self._strict = strict
        self._limits = limits
        # Non-strict reads never silently swallow corruption: skipped
        # lines are appended here and exposed via `read_warnings`.
        self._read_warnings: list[str] = []
        self._archive_cleanup: Any | None = None
        self._source_path = package_path

    # -- construction ---------------------------------------------------

    @classmethod
    def open(
        cls,
        path: Path | str,
        *,
        strict: bool = True,
        limits: Limits = DEFAULT_LIMITS,
    ) -> "PackageReader":
        package_path = Path(path)
        cleanup = None
        source_path = package_path
        if package_path.is_file():
            import tempfile

            from .archive import ArchiveError, unpack_archive

            cleanup = tempfile.TemporaryDirectory(prefix="clulatent-reader-")
            extracted = Path(cleanup.name) / "package.clulatent"
            try:
                unpack_archive(package_path, extracted)
            except (ArchiveError, OSError) as exc:
                cleanup.cleanup()
                raise MalformedPackageError(f"portable archive could not be opened: {exc}") from exc
            package_path = extracted
        if not package_path.exists() or not package_path.is_dir():
            if cleanup is not None:
                cleanup.cleanup()
            raise PackageReaderError(
                f"Package not found or not a directory: {package_path}"
            )
        manifest_path = package_path / "manifest.json"
        if not manifest_path.exists():
            if cleanup is not None:
                cleanup.cleanup()
            raise PackageReaderError(f"manifest.json not found in package: {package_path}")
        try:
            manifest = Manifest.from_json_file(manifest_path, limits=limits)
        except Exception as exc:  # noqa: BLE001 - re-raised as a clean reader error
            if cleanup is not None:
                cleanup.cleanup()
            raise MalformedPackageError(
                f"manifest.json could not be read/validated: {exc}"
            ) from exc
        reader = cls(package_path, manifest, strict=strict, limits=limits)
        reader._archive_cleanup = cleanup
        reader._source_path = source_path
        return reader

    def close(self) -> None:
        """Release temporary archive material, if any."""
        if self._archive_cleanup is not None:
            self._archive_cleanup.cleanup()
            self._archive_cleanup = None

    def __enter__(self) -> "PackageReader":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    # -- manifest-level properties -------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    @property
    def package_id(self) -> str:
        return self._manifest.package_id

    @property
    def duration_ms(self) -> int:
        return self._manifest.source.duration_ms

    @property
    def created_at(self) -> str:
        return self._manifest.created_at

    @property
    def status(self) -> str:
        return self._manifest.status

    @property
    def has_audio(self) -> bool:
        return self._manifest.source.has_audio

    @property
    def strict(self) -> bool:
        return self._strict

    @property
    def read_warnings(self) -> list[str]:
        """Corrupt/skipped lines recorded during non-strict track reads."""
        return list(self._read_warnings)

    # -- tracks ---------------------------------------------------------

    def _descriptor(self, name: str) -> TrackDescriptor | None:
        return next((t for t in self._manifest.tracks if t.name == name), None)

    def list_tracks(self) -> list[TrackHandle]:
        """One handle per manifest-declared track, in manifest order."""
        return [
            TrackHandle(
                name=t.name,
                file=t.file,
                schema_id=t.schema_id,
                schema_version=t.schema_version,
                record_count=t.record_count,
                sorted_by=t.sorted_by,
                known=t.name in KNOWN_TRACK_NAMES,
            )
            for t in self._manifest.tracks
        ]

    def track_names(self) -> list[str]:
        return [t.name for t in self._manifest.tracks]

    def has_track(self, name: str) -> bool:
        return self._descriptor(name) is not None

    def load_track(self, name: str) -> list[EventRecord]:
        """Load a track's events by name, ordered by `t_start_ms` then id.

        Returns `[]` for a track the manifest does not declare (an absent
        optional track is not an error). Raises `MalformedPackageError`
        if a declared track's file is missing, resolves outside the
        package, or (in strict mode) contains a corrupt line.
        """
        descriptor = self._descriptor(name)
        if descriptor is None:
            return []

        try:
            track_path = resolve_in_package(
                self._path, descriptor.file, field_name=f"tracks[{name}].file"
            )
        except PathSecurityError as exc:
            raise MalformedPackageError(str(exc)) from exc

        if not track_path.exists():
            raise MalformedPackageError(
                f"track {name!r} file declared in manifest is missing: {descriptor.file}"
            )

        if self._strict:
            try:
                envelopes = tracks_mod.read_track_file(track_path, limits=self._limits)
            except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
                raise MalformedPackageError(
                    f"track {name!r} could not be read cleanly: {exc}"
                ) from exc
            records = [
                self._to_record(name, env.model_dump(mode="json")) for env in envelopes
            ]
        else:
            records = self._load_track_tolerant(name, track_path)

        records.sort(key=lambda r: (r.t_start_ms, r.id))
        return records

    def _load_track_tolerant(self, name: str, track_path: Path) -> list[EventRecord]:
        """Non-strict read: skip corrupt lines but record every skip."""
        from .event import EventEnvelope

        records: list[EventRecord] = []
        try:
            for record in iter_jsonl_bounded(track_path, limits=self._limits):
                if record.error is not None:
                    self._read_warnings.append(
                        f"{track_path.name}:{record.lineno}: {record.error}"
                    )
                    continue
                try:
                    envelope = EventEnvelope.model_validate(record.data)
                except Exception as exc:  # pydantic ValidationError
                    self._read_warnings.append(
                        f"{track_path.name}:{record.lineno}: does not match event envelope ({exc})"
                    )
                    continue
                records.append(self._to_record(name, envelope.model_dump(mode="json")))
        except JsonlLimitError as exc:
            # A hard resource limit is never swallowed, even in non-strict mode.
            raise MalformedPackageError(f"track {name!r}: {exc}") from exc
        return records

    def _to_record(self, track_name: str, data: dict[str, Any]) -> EventRecord:
        return EventRecord(
            id=str(data.get("id", "")),
            type=str(data.get("type", "")),
            track_name=track_name,
            t_start_ms=int(data.get("t_start_ms", 0)),
            t_end_ms=int(data.get("t_end_ms", 0)),
            producer=data.get("producer") if isinstance(data.get("producer"), dict) else {},
            confidence=data.get("confidence"),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            raw=data,
        )

    def iter_events(self, track_name: str) -> Iterator[EventRecord]:
        """Iterate a track's events (ordered). Convenience over `load_track`."""
        yield from self.load_track(track_name)

    # -- events ---------------------------------------------------------

    def _resolve_track_names(self, track_names: list[str] | None) -> list[str]:
        if track_names is None:
            return self.track_names()
        return [n for n in track_names if self.has_track(n)]

    def get_event(
        self, event_id: str, *, track_names: list[str] | None = None
    ) -> EventRecord | None:
        """Return the first event with `id == event_id`, across the given
        tracks (or all declared tracks). `None` if no track holds it.
        """
        for name in self._resolve_track_names(track_names):
            for event in self.load_track(name):
                if event.id == event_id:
                    return event
        return None

    def query_time(
        self,
        start_ms: int,
        end_ms: int | None = None,
        *,
        track_names: list[str] | None = None,
    ) -> list[EventRecord]:
        """Every event whose `[t_start_ms, t_end_ms]` overlaps `[start_ms, end_ms]`.

        `end_ms=None` means "to the end of the package" (the manifest's
        source duration). Results are ordered by `t_start_ms`, then track
        name, then id, so the same query is deterministic. An inverted
        range (`end_ms < start_ms`) is a caller mistake and raises
        `PackageReaderError`.
        """
        if end_ms is None:
            end_ms = self.duration_ms
        if end_ms < start_ms:
            raise PackageReaderError(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")

        matches: list[EventRecord] = []
        for name in self._resolve_track_names(track_names):
            for event in self.load_track(name):
                if event.t_start_ms <= end_ms and event.t_end_ms >= start_ms:
                    matches.append(event)
        matches.sort(key=lambda r: (r.t_start_ms, r.track_name, r.id))
        return matches

    def resolve_event_ref(self, ref_or_id: Any) -> EventRecord | None:
        """Resolve a reference to a single event.

        Accepts a bare event id (`"kf_000003"`), a `"track:id"` string
        (`"evidence_bundles:eb_000000"`), or a mapping with an `"id"`
        key and optional `"track"`/`"track_name"` key. Returns the
        matching `EventRecord` or `None`.
        """
        track_hint: str | None = None
        event_id: str | None = None

        if isinstance(ref_or_id, dict):
            raw_id = ref_or_id.get("id")
            event_id = str(raw_id) if raw_id is not None else None
            hint = ref_or_id.get("track", ref_or_id.get("track_name"))
            track_hint = str(hint) if hint is not None else None
        elif isinstance(ref_or_id, str):
            if ":" in ref_or_id:
                maybe_track, maybe_id = ref_or_id.split(":", 1)
                # Only treat the prefix as a track hint if it is actually a
                # declared track; otherwise the whole string is the id.
                if self.has_track(maybe_track):
                    track_hint, event_id = maybe_track, maybe_id
                else:
                    event_id = ref_or_id
            else:
                event_id = ref_or_id
        else:
            return None

        if not event_id:
            return None

        track_names = [track_hint] if track_hint else None
        return self.get_event(event_id, track_names=track_names)

    # -- receipts / lock / validation / summary -------------------------

    def receipts(self) -> list[ReceiptHandle]:
        """List receipt JSONL files present under `receipts/` (read-only).

        Returns one `ReceiptHandle` per `.jsonl` file actually on disk in
        the package's `receipts/` directory. The manifest-declared
        primary receipt (`manifest.receipts.file`) is always included as
        a handle (with `exists` reflecting disk state) even if absent, so
        a caller can see it was expected. Each handle reads its records
        lazily and bounded.
        """
        handles: dict[str, ReceiptHandle] = {}

        declared = self._manifest.receipts.file
        declared_path = self._path / declared
        handles[declared] = ReceiptHandle(
            name=PurePosixPath(declared).stem,
            file=declared,
            exists=declared_path.is_file() and not declared_path.is_symlink(),
            _package_path=self._path,
            _limits=self._limits,
        )

        receipts_dir = self._path / RECEIPTS_DIR
        if receipts_dir.is_dir() and not receipts_dir.is_symlink():
            for entry in sorted(receipts_dir.glob("*.jsonl")):
                if entry.is_symlink() or not entry.is_file():
                    continue
                rel = f"{RECEIPTS_DIR}/{entry.name}"
                if rel in handles:
                    continue
                handles[rel] = ReceiptHandle(
                    name=entry.stem,
                    file=rel,
                    exists=True,
                    _package_path=self._path,
                    _limits=self._limits,
                )

        return list(handles.values())

    def lock_status(self) -> dict[str, Any]:
        """Read-only lock inspection. Never modifies lock state.

        Returns `{"status": ...}` where status is one of `unlocked`,
        `locked`, `lock-invalid`, `lock-partial`, plus `hash_valid`
        (bool | None) and, when a lock verify report is available, its
        `checked`/`missing`/`changed` counts.
        """
        from . import lock as lock_mod

        status, report = lock_mod.lock_status(self._path, limits=self._limits)
        out: dict[str, Any] = {"status": status}
        if report is not None:
            out["hash_valid"] = report.lock_hash_valid
            out["valid"] = report.valid
            out["checked"] = len(report.checked)
            out["missing"] = len(report.missing)
            out["changed"] = len(report.changed)
        else:
            out["hash_valid"] = None
        return out

    def validate(self) -> Any:
        """Delegate to the existing validator; return its `ValidationReport`.

        The reader does not duplicate or reinterpret any validity rule —
        judging package validity is the validator's job, not the
        reader's.
        """
        from .validate import validate_package

        return validate_package(self._path, limits=self._limits)

    def summary(self) -> dict[str, Any]:
        """A bounded, structured overview of the package. No prose, no meaning."""
        tracks = self.list_tracks()
        lock = self.lock_status()
        return {
            "package_id": self.package_id,
            "path": str(self._source_path),
            "status": self.status,
            "created_at": self.created_at,
            "duration_ms": self.duration_ms,
            "has_audio": self.has_audio,
            "clulatent_version": self._manifest.clulatent_version,
            "tool": self._manifest.tool.model_dump(mode="json"),
            "track_count": len(tracks),
            "tracks": [
                {
                    "name": t.name,
                    "record_count": t.record_count,
                    "known": t.known,
                }
                for t in tracks
            ],
            "unknown_tracks": [t.name for t in tracks if not t.known],
            "receipts": [r.file for r in self.receipts() if r.exists],
            "lock_status": lock["status"],
        }


def open_package(
    path: Path | str,
    *,
    strict: bool = True,
    limits: Limits = DEFAULT_LIMITS,
) -> PackageReader:
    """Open a `.clulatent` package for read-only inspection.

    Thin module-level alias for `PackageReader.open`, matching the
    documented Phase 3.18 entry point.
    """
    return PackageReader.open(path, strict=strict, limits=limits)
