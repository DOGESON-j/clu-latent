"""Package integrity locking (Phase 1.7.5).

A `.clulatent` package's integrity lock is a local hash manifest —
`lock/package.lock.json` (the list of tracked files and their
SHA-256 digests) plus `lock/package.lock.sha256` (the hash of that
JSON file itself). It proves whether the package's canonical files
have changed since `clulatent lock` was run.

This is explicitly NOT: encryption, DRM, or a cryptographic signature
of authorship/identity. It does not stop anyone with filesystem access
from editing the package's files or deleting the lock files
themselves — it only lets `clulatent verify-lock` *detect* that a
tracked file no longer matches what was locked. See docs/LOCKING.md.

Which files are hashed: manifest.json, the source media file,
sources/source.sha256, every tracks/*.jsonl file, and every
receipts/*.jsonl file. index/search.sqlite is excluded by default
(it is derived/rebuildable — see reindex.py) and only included when
the caller passes `include_index=True`. media/keyframes/*.jpg is never
hashed by this phase.

The lock JSON is serialized deterministically (`sort_keys=True`) —
unlike `Manifest.to_json_file`, which is not required to be
hash-stable — because `package.lock.sha256` must be a reproducible
hash of `package.lock.json`'s exact bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .constants import (
    LOCK_DIR,
    LOCK_JSON_FILENAME,
    LOCK_SHA256_FILENAME,
    LOCK_VERSION,
    TOOL_NAME,
    TOOL_VERSION,
)
from .hash import sha256_file
from .manifest import Manifest
from .security.jsonl import JsonlLimitError, read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package


class LockError(RuntimeError):
    """Raised when a package cannot be safely locked, read, or verified."""


LockStatus = Literal["unlocked", "locked", "lock-invalid", "lock-partial"]


class LockToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str


class LockPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_index: bool
    strict_extra_files: bool
    hash_algorithm: Literal["sha256"] = "sha256"


class LockFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    size_bytes: int
    modified_time_ns: int


class PackageLock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lock_version: str = LOCK_VERSION
    package_id: str
    created_at: str
    tool: LockToolInfo = LockToolInfo(name=TOOL_NAME, version=TOOL_VERSION)
    policy: LockPolicy
    files: list[LockFileEntry]

    def to_json_bytes(self) -> bytes:
        """Deterministic serialization: sorted keys, fixed indent, trailing newline.

        Required so `package.lock.sha256` is a stable, reproducible
        hash of these exact bytes (unlike `Manifest.to_json_file`,
        which does not need to be hash-stable).
        """
        return (
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> "PackageLock":
        return cls.model_validate(json.loads(raw.decode("utf-8")))


@dataclass
class LockCreateResult:
    package_path: Path
    lock_json_path: Path
    lock_sha256_path: Path
    file_count: int
    chmod_readonly_applied: list[str] = field(default_factory=list)


@dataclass
class LockVerifyReport:
    package_path: Path
    valid: bool
    checked: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    lock_hash_valid: bool = True


def _ensure_safe_lock_dir(package_root: Path, *, create: bool) -> Path:
    """Return `package_root/lock`, refusing a symlinked lock directory.

    Mirrors `security.operation_lock._ensure_safe_lock_dir` — `lock/` is
    a fixed tool-owned path, not manifest-declared, but a malicious
    package could still pre-plant it as a symlink, so the same
    refuse-on-symlink rule applies before any read or write.
    """
    root_resolved = Path(package_root).resolve(strict=True)
    lock_dir = root_resolved / LOCK_DIR
    if lock_dir.is_symlink():
        raise LockError(f"refusing to use a symlinked lock directory: {lock_dir}")
    if lock_dir.exists() and not lock_dir.is_dir():
        raise LockError(f"lock path exists and is not a directory: {lock_dir}")
    if not lock_dir.exists():
        if not create:
            return lock_dir
        lock_dir.mkdir(parents=False)
    return lock_dir


def _lock_paths(package_root: Path, *, create_dir: bool) -> tuple[Path, Path]:
    lock_dir = _ensure_safe_lock_dir(package_root, create=create_dir)
    lock_json_path = lock_dir / LOCK_JSON_FILENAME
    lock_sha256_path = lock_dir / LOCK_SHA256_FILENAME
    for candidate in (lock_json_path, lock_sha256_path):
        if candidate.is_symlink():
            raise LockError(f"refusing to use a symlinked lock file: {candidate}")
    return lock_json_path, lock_sha256_path


def _load_manifest(package_path: Path, *, limits: Limits) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise LockError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path, limits=limits)
    except JsonlLimitError as exc:
        raise LockError(f"manifest.json exceeds size limits: {exc}") from exc
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LockError(f"manifest.json is invalid: {exc}") from exc


def _canonical_relative_paths(manifest: Manifest, package_path: Path, *, include_index: bool) -> list[str]:
    """Every package-relative path this phase locks, in a stable order.

    manifest.json itself, the source media file, sources/source.sha256,
    every tracks/*.jsonl file, every receipts/*.jsonl file, and
    (only if include_index) index/search.sqlite. media/keyframes/*.jpg
    is deliberately never included — see module docstring.
    """
    paths: list[str] = ["manifest.json", manifest.source.stored_path, "sources/source.sha256"]
    for track in manifest.tracks:
        paths.append(track.file)

    receipts_dir = package_path / "receipts"
    if receipts_dir.is_dir() and not receipts_dir.is_symlink():
        for entry in sorted(receipts_dir.iterdir()):
            if entry.is_symlink():
                raise LockError(f"refusing to lock through a symlinked receipts file: {entry}")
            if entry.is_file() and entry.name.endswith(".jsonl"):
                paths.append(f"receipts/{entry.name}")

    if include_index:
        paths.append(manifest.index.file)

    return paths


def _resolve_and_check_exists(package_path: Path, relative: str, *, field_name: str) -> Path:
    try:
        resolved = resolve_in_package(package_path, relative, field_name=field_name)
    except PathSecurityError as exc:
        raise LockError(str(exc)) from exc
    if not resolved.exists():
        raise LockError(f"{field_name}: declared file is missing: {relative}")
    if resolved.is_symlink():
        raise LockError(f"{field_name}: refusing to lock a symlink: {relative}")
    return resolved


def _atomic_write(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically (temp file + fsync + os.replace)."""
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def apply_chmod_readonly(paths: list[Path]) -> list[str]:
    """Best-effort strip of write bits on `paths`. NOT a security boundary.

    Any owner with filesystem permissions can chmod the file back —
    this is a convenience nudge against accidental edits, nothing more.
    Per-file OSError is swallowed; only successfully-chmod'd paths are
    returned.
    """
    applied: list[str] = []
    for path in paths:
        try:
            mode = path.stat().st_mode
            path.chmod(mode & ~0o222)
            applied.append(str(path))
        except OSError:
            continue
    return applied


def create_lock(
    package_path: Path,
    *,
    force: bool = False,
    include_index: bool = False,
    strict_extra_files_default: bool = False,
    chmod_readonly: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> LockCreateResult:
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise LockError(f"Package not found: {package_path}")

    # Fail fast on a symlinked lock/ dir before doing any hashing work.
    _ensure_safe_lock_dir(package_path, create=False)

    # Refuse to silently overwrite an existing integrity lock. A lock is
    # meant to be durable evidence of "this is what the package looked
    # like at time T" (see docs/LOCKING.md), so replacing it requires an
    # explicit --force. This is distinct from --force-stale-lock, which
    # only clears a transient *operation* lock, not this integrity lock.
    existing_json, existing_sha256 = _lock_paths(package_path, create_dir=False)
    if not force and (existing_json.exists() or existing_sha256.exists()):
        raise LockError(
            f"package is already locked ({LOCK_DIR}/{LOCK_JSON_FILENAME} exists); "
            "re-run with --force to replace the existing lock"
        )

    manifest = _load_manifest(package_path, limits=limits)
    relative_paths = _canonical_relative_paths(manifest, package_path, include_index=include_index)

    entries: list[LockFileEntry] = []
    for relative in relative_paths:
        resolved = _resolve_and_check_exists(package_path, relative, field_name=relative)
        stat_result = resolved.stat()
        entries.append(
            LockFileEntry(
                path=relative,
                sha256=sha256_file(resolved),
                size_bytes=stat_result.st_size,
                modified_time_ns=stat_result.st_mtime_ns,
            )
        )

    lock = PackageLock(
        package_id=manifest.package_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        policy=LockPolicy(
            include_index=include_index,
            strict_extra_files=strict_extra_files_default,
        ),
        files=entries,
    )
    lock_json_bytes = lock.to_json_bytes()

    # (Re-)create lock/ now that hashing succeeded, then write both files
    # atomically. If the sha256 sidecar write fails, remove the lock.json
    # we just wrote so we never leave a lock.json without its sidecar.
    lock_json_path, lock_sha256_path = _lock_paths(package_path, create_dir=True)
    _atomic_write(lock_json_path, lock_json_bytes)
    try:
        digest = hashlib.sha256(lock_json_bytes).hexdigest()
        sidecar_bytes = f"{digest}  {LOCK_JSON_FILENAME}\n".encode("utf-8")
        _atomic_write(lock_sha256_path, sidecar_bytes)
    except BaseException:
        if lock_json_path.exists():
            lock_json_path.unlink()
        raise

    chmod_applied: list[str] = []
    if chmod_readonly:
        chmod_applied = apply_chmod_readonly(
            [_resolve_and_check_exists(package_path, p, field_name=p) for p in relative_paths]
            + [lock_json_path, lock_sha256_path]
        )

    return LockCreateResult(
        package_path=package_path,
        lock_json_path=lock_json_path,
        lock_sha256_path=lock_sha256_path,
        file_count=len(entries),
        chmod_readonly_applied=chmod_applied,
    )


def read_lock(package_path: Path, *, limits: Limits = DEFAULT_LIMITS) -> tuple[PackageLock, bool]:
    """Load lock/package.lock.json, returning (lock, lock_hash_valid).

    Raises LockError only if lock.json itself is missing, unreadable, or
    schema-invalid. A mismatch between the recomputed hash of
    lock.json's bytes and lock.sha256's recorded digest is NOT an
    exception — it is returned as `lock_hash_valid=False` so callers
    (verify-lock, lock-status) can report it as a specific failure mode.
    """
    lock_json_path, lock_sha256_path = _lock_paths(package_path, create_dir=False)

    if not lock_json_path.exists():
        raise LockError(f"package is not locked: {lock_json_path} does not exist")

    raw = read_bytes_bounded(
        lock_json_path, max_bytes=limits.max_manifest_bytes, field_name=str(lock_json_path)
    )
    try:
        lock = PackageLock.from_json_bytes(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LockError(f"{lock_json_path} is invalid: {exc}") from exc

    lock_hash_valid = False
    if lock_sha256_path.exists():
        try:
            sidecar_raw = read_bytes_bounded(
                lock_sha256_path, max_bytes=limits.max_sidecar_bytes, field_name=str(lock_sha256_path)
            )
            recorded_digest = sidecar_raw.decode("utf-8").strip().split()[0]
            actual_digest = hashlib.sha256(raw).hexdigest()
            lock_hash_valid = recorded_digest == actual_digest
        except (JsonlLimitError, OSError, IndexError, UnicodeDecodeError):
            lock_hash_valid = False

    return lock, lock_hash_valid


def _scan_candidate_files(package_path: Path, *, include_index: bool) -> set[str]:
    """Enumerate every file under the directories this phase's lock covers.

    Scoped to `sources/`, `tracks/`, `receipts/`, root `manifest.json`,
    and `index/` (only if include_index) — never `media/keyframes/`,
    since keyframe images are not part of this phase's hash set (see
    module docstring); including them here would make `--strict` flag
    every keyframe as a false-positive "extra" file.
    """
    candidates: set[str] = set()
    if (package_path / "manifest.json").is_file() and not (package_path / "manifest.json").is_symlink():
        candidates.add("manifest.json")

    scan_dirs = ["sources", "tracks", "receipts"]
    if include_index:
        scan_dirs.append("index")

    for dir_name in scan_dirs:
        directory = package_path / dir_name
        if not directory.is_dir() or directory.is_symlink():
            continue
        for entry in sorted(directory.rglob("*")):
            if entry.is_dir():
                continue
            if entry.is_symlink():
                continue
            candidates.add(str(entry.relative_to(package_path).as_posix()))

    return candidates


def verify_lock(
    package_path: Path, *, strict: bool = False, limits: Limits = DEFAULT_LIMITS
) -> LockVerifyReport:
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise LockError(f"Package not found: {package_path}")

    lock, lock_hash_valid = read_lock(package_path, limits=limits)

    report = LockVerifyReport(package_path=package_path, valid=True, lock_hash_valid=lock_hash_valid)
    if not lock_hash_valid:
        report.valid = False
        report.errors.append(
            f"{LOCK_SHA256_FILENAME} does not match the current contents of {LOCK_JSON_FILENAME}"
        )

    locked_paths = {entry.path for entry in lock.files}
    for entry in lock.files:
        try:
            resolved = resolve_in_package(package_path, entry.path, field_name=entry.path)
        except PathSecurityError as exc:
            report.errors.append(str(exc))
            report.valid = False
            continue
        if not resolved.exists():
            report.missing.append(entry.path)
            report.valid = False
            continue
        if resolved.is_symlink():
            report.errors.append(f"{entry.path}: refusing to verify through a symlink")
            report.valid = False
            continue
        actual_digest = sha256_file(resolved)
        if actual_digest != entry.sha256:
            report.changed.append(entry.path)
            report.valid = False
            continue
        report.checked.append(entry.path)

    if strict:
        candidates = _scan_candidate_files(package_path, include_index=lock.policy.include_index)
        extras = sorted(candidates - locked_paths)
        if extras:
            report.extra = extras
            report.valid = False

    return report


def lock_status(
    package_path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[LockStatus, LockVerifyReport | None]:
    """Read-only status check: unlocked / locked / lock-invalid / lock-partial."""
    package_path = Path(package_path)
    lock_json_path, lock_sha256_path = _lock_paths(package_path, create_dir=False)

    json_exists = lock_json_path.exists()
    sha_exists = lock_sha256_path.exists()

    if not json_exists and not sha_exists:
        return "unlocked", None
    if json_exists != sha_exists:
        return "lock-partial", None

    try:
        report = verify_lock(package_path, strict=False, limits=limits)
    except LockError:
        return "lock-invalid", None

    return ("locked" if report.valid else "lock-invalid"), report
