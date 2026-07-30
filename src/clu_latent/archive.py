"""Deterministic, bounded portable ``.clulatent`` ZIP containers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterator

ARCHIVE_MEDIA_TYPE = "application/vnd.clulatent+zip"
ARCHIVE_MARKER = "clulatent-archive.json"
MAX_MEMBERS = 10_000
MAX_FILE_SIZE = 512 * 1024 * 1024
MAX_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_NESTED_ARCHIVES = 0
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_STORED_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".m4a", ".aac", ".flac", ".jpg", ".jpeg", ".png", ".webp"}
_DRIVE = re.compile(r"^[A-Za-z]:")


class ArchiveError(ValueError):
    """Raised when a portable archive is unsafe, malformed, or over limits."""


@dataclass(frozen=True)
class ArchiveVerification:
    path: Path
    members: int
    uncompressed_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArchiveLimits:
    max_members: int = MAX_MEMBERS
    max_file_size: int = MAX_FILE_SIZE
    max_total_size: int = MAX_TOTAL_SIZE
    max_compression_ratio: int = MAX_COMPRESSION_RATIO
    max_nested_archives: int = MAX_NESTED_ARCHIVES


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


def _safe_name(raw: str) -> PurePosixPath:
    if not raw or "\x00" in raw or raw.startswith(("/", "\\")) or _DRIVE.match(raw):
        raise ArchiveError(f"unsafe archive member path: {raw!r}")
    normalized = raw.replace("\\", "/")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveError(f"unsafe archive member path: {raw!r}")
    return path


def _inspect(
    archive: Path, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS
) -> tuple[list[zipfile.ZipInfo], int]:
    try:
        handle = zipfile.ZipFile(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError(f"not a readable CLULatent archive: {exc}") from exc
    with handle:
        infos = handle.infolist()
        if len(infos) > limits.max_members:
            raise ArchiveError(f"archive member limit exceeded ({limits.max_members})")
        seen: set[str] = set()
        file_names: set[str] = set()
        total = 0
        nested = 0
        for info in infos:
            name = _safe_name(info.filename).as_posix()
            folded = name.casefold()
            if folded in seen:
                raise ArchiveError(f"duplicate or conflicting archive member: {name}")
            seen.add(folded)
            parts = PurePosixPath(name).parts
            for index in range(1, len(parts)):
                if "/".join(parts[:index]).casefold() in file_names:
                    raise ArchiveError(f"file/directory member conflict: {name}")
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (
                file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
            ):
                raise ArchiveError(f"non-regular archive member rejected: {name}")
            if info.flag_bits & 0x1:
                raise ArchiveError(f"encrypted archive member rejected: {name}")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ArchiveError(f"unsupported compression method: {name}")
            if info.file_size > limits.max_file_size:
                raise ArchiveError(f"archive member too large: {name}")
            total += info.file_size
            if total > limits.max_total_size:
                raise ArchiveError(f"archive total size limit exceeded ({limits.max_total_size})")
            if info.compress_size == 0 and info.file_size:
                raise ArchiveError(f"suspicious compression ratio: {name}")
            if (
                info.compress_size
                and info.file_size / info.compress_size > limits.max_compression_ratio
            ):
                raise ArchiveError(f"suspicious compression ratio: {name}")
            if name != ARCHIVE_MARKER and name.lower().endswith((".zip", ".clulatent")):
                nested += 1
                if nested > limits.max_nested_archives:
                    raise ArchiveError(f"nested archive member rejected: {name}")
            if not info.is_dir():
                if any(existing.startswith(f"{folded}/") for existing in file_names):
                    raise ArchiveError(f"file/directory member conflict: {name}")
                file_names.add(folded)
        if ARCHIVE_MARKER not in {i.filename for i in infos}:
            raise ArchiveError(f"missing archive marker {ARCHIVE_MARKER}")
        if "manifest.json" not in {i.filename for i in infos}:
            raise ArchiveError("missing manifest.json")
        try:
            marker = json.loads(handle.read(ARCHIVE_MARKER))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ArchiveError(f"invalid archive marker: {exc}") from exc
        if marker != {"format": "1.0", "media_type": ARCHIVE_MEDIA_TYPE}:
            raise ArchiveError("archive marker identity is not supported")
        return infos, total


def verify_archive(
    path: Path | str, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS
) -> ArchiveVerification:
    archive = Path(path)
    infos, total = _inspect(archive, limits=limits)
    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return ArchiveVerification(archive, len(infos), total, digest.hexdigest())


def pack_package(package: Path | str, output: Path | str) -> ArchiveVerification:
    source, destination = Path(package), Path(output)
    if not source.is_dir() or not (source / "manifest.json").is_file():
        raise ArchiveError("pack input must be a package directory containing manifest.json")
    if destination.exists():
        raise ArchiveError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in source.rglob("*") if p.is_file())
    unsafe_sources = [p for p in source.rglob("*") if p.is_symlink() or (p.exists() and not (p.is_file() or p.is_dir()))]
    if unsafe_sources:
        raise ArchiveError(
            f"package contains symlink or non-regular member: {unsafe_sources[0].relative_to(source)}"
        )
    names = [p.relative_to(source).as_posix() for p in files]
    if ARCHIVE_MARKER in names:
        raise ArchiveError(f"package must not contain reserved {ARCHIVE_MARKER}")
    marker = json.dumps(
        {"media_type": ARCHIVE_MEDIA_TYPE, "format": "1.0"}, sort_keys=True, separators=(",", ":")
    ).encode() + b"\n"
    temp_handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False
    )
    temporary = Path(temp_handle.name)
    temp_handle.close()
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as handle:
            entries: list[tuple[str, Path | None, bytes | None]] = [
                (ARCHIVE_MARKER, None, marker)
            ] + [(n, p, None) for n, p in zip(names, files)]
            for name, path, data in entries:
                info = zipfile.ZipInfo(name, _ZIP_EPOCH)
                info.create_system = 3
                info.external_attr = (0o100644 & 0xFFFF) << 16
                info.compress_type = zipfile.ZIP_STORED if Path(name).suffix.lower() in _STORED_SUFFIXES else zipfile.ZIP_DEFLATED
                if data is not None:
                    handle.writestr(info, data, compresslevel=9)
                else:
                    assert path is not None
                    with path.open("rb") as src, handle.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return verify_archive(destination)


def unpack_archive(
    archive: Path | str,
    output: Path | str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> Path:
    source, destination = Path(archive), Path(output)
    infos, _ = _inspect(source, limits=limits)
    if destination.exists():
        raise ArchiveError(f"output already exists: {destination}")
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(source) as handle:
            for info in infos:
                name = _safe_name(info.filename)
                if name.as_posix() == ARCHIVE_MARKER:
                    continue
                target = destination.joinpath(*name.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if info.is_dir():
                    target.mkdir(exist_ok=True)
                else:
                    with handle.open(info) as src, target.open("xb") as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
        if not (destination / "manifest.json").is_file():
            raise ArchiveError("extracted package has no manifest.json")
        return destination
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


@contextmanager
def resolved_package(
    path: Path | str, *, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS
) -> Iterator[Path]:
    """Yield a directory for either package form, removing temporary data afterward."""
    candidate = Path(path)
    if candidate.is_dir():
        yield candidate
        return
    with tempfile.TemporaryDirectory(prefix="clulatent-read-") as tmp:
        root = Path(tmp) / "package.clulatent"
        unpack_archive(candidate, root, limits=limits)
        yield root
