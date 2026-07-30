"""Write-operation locking (Phase 1.7.5).

This is a *cooperative* guard against two `clulatent` write operations
(e.g. two `reindex` runs, or a `lock` racing a `reindex`) stepping on
the same package concurrently. It is not a security boundary and does
not stop a determined or malicious actor — it exists purely to avoid
accidental corruption from two well-behaved local invocations
overlapping.

`lock/package.operation.lock.json` is created atomically
(`O_CREAT | O_EXCL`) before a guarded operation starts and removed
when it finishes, successfully or not. A lock left behind by a crashed
or killed process is "stale" and can be cleared with
`--force-stale-lock`, but only if its recorded pid is confirmed gone or
its age exceeds a safe threshold — never unconditionally.

The lock *directory* itself (`lock/`) is treated the same as any other
package-relative location a malicious package could pre-plant as a
symlink: creating or writing into it always refuses if it is (or would
resolve through) a symlink, mirroring `security.paths.resolve_in_package`.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..constants import (
    INGEST_TARGET_LOCK_SUFFIX,
    LOCK_DIR,
    LOCK_OPERATION_FILENAME,
    TOOL_VERSION,
)
from .console import safe_console_text
from .jsonl import JsonlLimitError, read_bytes_bounded
from .limits import DEFAULT_LIMITS, Limits
from .paths import PathSecurityError

DEFAULT_STALE_AGE_S = 6 * 60 * 60  # 6 hours


class OperationLockError(RuntimeError):
    """Raised when a write operation cannot acquire (or clear) the operation lock."""


@dataclass
class OperationLockInfo:
    pid: int
    hostname: str
    operation: str
    created_at: str
    package_path: str
    tool_version: str


def _ensure_safe_lock_dir(package_root: Path) -> Path:
    """Return `package_root/lock`, refusing a symlinked lock directory.

    Creates the directory if it does not yet exist (older packages
    predating Phase 1.7.5 have no `lock/` directory at all). Mirrors
    the symlink-refusal behavior of `security.paths.resolve_in_package`
    even though `lock/` is a fixed tool-owned path, not a manifest-
    declared one, since a malicious package could still pre-plant it.
    """
    root_resolved = Path(package_root).resolve(strict=True)
    lock_dir = root_resolved / LOCK_DIR
    if lock_dir.is_symlink():
        raise PathSecurityError(f"refusing to use a symlinked lock directory: {lock_dir}")
    if lock_dir.exists() and not lock_dir.is_dir():
        raise PathSecurityError(f"lock path exists and is not a directory: {lock_dir}")
    if not lock_dir.exists():
        lock_dir.mkdir(parents=False)
    return lock_dir


def _lock_path(package_root: Path) -> Path:
    lock_dir = _ensure_safe_lock_dir(package_root)
    lock_path = lock_dir / LOCK_OPERATION_FILENAME
    if lock_path.is_symlink():
        raise PathSecurityError(f"refusing to use a symlinked operation lock file: {lock_path}")
    return lock_path


def _pid_running(pid: int) -> bool | None:
    """True/False if we can determine liveness, None if unknown on this platform.

    POSIX-only (`os.kill(pid, 0)`); on any platform/error where liveness
    can't be determined, returns None so callers fall back to the age
    threshold alone rather than guessing.
    """
    if os.name != "posix":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else: still "running".
        return True
    except OSError:
        return None
    return True


def _read_lock_at(lock_path: Path, *, limits: Limits) -> OperationLockInfo | None:
    """Read an operation lock file at `lock_path`, if any. Returns None if unlocked.

    Bounded read via `read_bytes_bounded` (package-derived JSON is
    untrusted input, same as any other package file). A lock file that
    exists but is unreadable/corrupt is reported via OperationLockError
    rather than silently treated as "unlocked".
    """
    if not lock_path.exists():
        return None

    try:
        raw = read_bytes_bounded(
            lock_path, max_bytes=limits.max_sidecar_bytes, field_name=str(lock_path)
        )
        data = json.loads(raw.decode("utf-8"))
        return OperationLockInfo(
            pid=int(data["pid"]),
            hostname=str(data["hostname"]),
            operation=str(data["operation"]),
            created_at=str(data["created_at"]),
            package_path=str(data["package_path"]),
            tool_version=str(data["tool_version"]),
        )
    except (JsonlLimitError, OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise OperationLockError(
            f"operation lock file is present but unreadable/corrupt: {exc}"
        ) from exc


def read_operation_lock(package_root: Path, *, limits: Limits = DEFAULT_LIMITS) -> OperationLockInfo | None:
    """Read the current package-root operation lock, if any. Returns None if unlocked."""
    try:
        lock_path = _lock_path(package_root)
    except PathSecurityError as exc:
        raise OperationLockError(str(exc)) from exc
    return _read_lock_at(lock_path, limits=limits)


def read_ingest_target_lock(
    output_path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> OperationLockInfo | None:
    """Read the current ingest-target operation lock, if any. Returns None if unlocked."""
    try:
        lock_path = _ingest_target_lock_path(output_path)
    except PathSecurityError as exc:
        raise OperationLockError(str(exc)) from exc
    return _read_lock_at(lock_path, limits=limits)


def _lock_age_s(lock_path: Path) -> float:
    return max(0.0, time.time() - lock_path.stat().st_mtime)


def _check_stale_at(lock_path: Path, *, stale_age_s: float, limits: Limits) -> tuple[bool, str]:
    """Return (is_stale, description-of-current-holder).

    Stale if the recorded pid is confirmed not running, OR the lock's
    age exceeds `stale_age_s` — age alone is sufficient even if the pid
    happens to still be alive (e.g. pid reuse by an unrelated process).
    If the lock file itself can't be parsed, staleness falls back to
    file age alone.
    """
    age_s = _lock_age_s(lock_path)
    try:
        info = _read_lock_at(lock_path, limits=limits)
    except OperationLockError:
        return age_s > stale_age_s, f"<unreadable lock, age={age_s:.0f}s>"

    if info is None:
        return True, "<no lock>"

    holder = (
        f"pid={info.pid} host={safe_console_text(info.hostname)} "
        f"operation={safe_console_text(info.operation)} created_at={info.created_at}"
    )
    pid_alive = _pid_running(info.pid)
    is_stale = (pid_alive is False) or (age_s > stale_age_s)
    return is_stale, holder


@contextlib.contextmanager
def _guarded_lock(
    lock_path: Path,
    *,
    operation: str,
    record_path: str,
    force_stale: bool,
    stale_age_s: float,
    limits: Limits,
) -> Iterator[None]:
    """Shared acquire/release core for `operation_lock` and `ingest_target_lock`.

    Raises OperationLockError immediately (before doing any work) if
    `lock_path` is already held by another live/recent operation.
    `force_stale=True` clears an existing lock first, but only if it is
    confirmed stale (see `_check_stale_at`) — otherwise it still refuses.
    Always removes the lock it created on the way out, whether the
    guarded block succeeded or raised.
    """
    if lock_path.exists():
        is_stale, holder = _check_stale_at(lock_path, stale_age_s=stale_age_s, limits=limits)
        if not is_stale:
            raise OperationLockError(
                f"package is locked by another operation ({holder}); "
                "wait for it to finish or, if you are sure it is stale, retry with --force-stale-lock"
            )
        if not force_stale:
            raise OperationLockError(
                f"package has a stale operation lock ({holder}); retry with --force-stale-lock to clear it"
            )
        try:
            lock_path.unlink()
        except OSError as exc:
            raise OperationLockError(f"failed to clear stale operation lock: {exc}") from exc

    payload = OperationLockInfo(
        pid=os.getpid(),
        hostname=socket.gethostname(),
        operation=operation,
        created_at=datetime.now(timezone.utc).isoformat(),
        package_path=record_path,
        tool_version=TOOL_VERSION,
    )
    body = (
        json.dumps(
            {
                "pid": payload.pid,
                "hostname": payload.hostname,
                "operation": payload.operation,
                "created_at": payload.created_at,
                "package_path": payload.package_path,
                "tool_version": payload.tool_version,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise OperationLockError(
            "package is locked by another operation that started concurrently"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
    except OSError:
        with contextlib.suppress(OSError):
            lock_path.unlink()
        raise

    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


def _ingest_target_lock_path(output_path: Path) -> Path:
    output_path = Path(output_path)
    lock_path = output_path.parent / f"{output_path.name}{INGEST_TARGET_LOCK_SUFFIX}"
    if lock_path.is_symlink():
        raise PathSecurityError(f"refusing to use a symlinked ingest lock file: {lock_path}")
    return lock_path


@contextlib.contextmanager
def operation_lock(
    package_root: Path,
    *,
    operation: str,
    force_stale: bool = False,
    stale_age_s: float = DEFAULT_STALE_AGE_S,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[None]:
    """Acquire an exclusive write-operation lock on an existing package for the duration of the `with` block."""
    try:
        lock_path = _lock_path(package_root)
    except PathSecurityError as exc:
        raise OperationLockError(str(exc)) from exc

    record_path = str(Path(package_root).resolve(strict=True))
    with _guarded_lock(
        lock_path,
        operation=operation,
        record_path=record_path,
        force_stale=force_stale,
        stale_age_s=stale_age_s,
        limits=limits,
    ):
        yield


@contextlib.contextmanager
def ingest_target_lock(
    output_path: Path,
    *,
    operation: str = "ingest",
    force_stale: bool = False,
    stale_age_s: float = DEFAULT_STALE_AGE_S,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[None]:
    """Acquire an exclusive write-operation lock on a not-yet-existing ingest target.

    `clulatent ingest` builds its package inside a temp directory and
    only moves it into place at `output_path` on success (see
    `security.temp.staged_package_dir`), so `output_path` itself — and
    any `lock/` directory inside it — does not exist for most of the
    run. The lock file therefore lives as a sibling of `output_path`
    (`<output_path>.oplock.json`) instead, the same "sibling of a
    not-yet-existing target" pattern `ingest.py` already uses for its
    own failure-receipt file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_path = _ingest_target_lock_path(output_path)
    except PathSecurityError as exc:
        raise OperationLockError(str(exc)) from exc

    with _guarded_lock(
        lock_path,
        operation=operation,
        record_path=str(output_path),
        force_stale=force_stale,
        stale_age_s=stale_age_s,
        limits=limits,
    ):
        yield
