"""Hardened subprocess execution for ffmpeg/ffprobe (and only those).

Every external tool invocation in CLULatent must go through `run_tool`.
No `shell=True`, ever. Arguments are always passed as a list. Every
call has a timeout; on timeout (or on exceeding the output capture
cap) the whole process group/session is killed so a hung or malicious
child cannot outlive the parent. stdout/stderr are drained in bounded
background threads so a spamming process cannot exhaust memory.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .limits import DEFAULT_LIMITS, Limits


class ToolNotFoundError(RuntimeError):
    """Raised when a required external tool is not on PATH."""


@dataclass
class ToolResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    stderr_tail: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class ToolExecutionError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails, times out, or is killed."""

    def __init__(self, message: str, result: ToolResult) -> None:
        super().__init__(message)
        self.result = result


_TOOL_PATH_CACHE: dict[str, str] = {}


def reset_tool_cache() -> None:
    """Clear the resolved-tool-path cache. Test-only helper."""
    _TOOL_PATH_CACHE.clear()


def resolve_tool(name: str) -> str:
    """Resolve `name` (e.g. "ffmpeg") to an absolute path on PATH, once.

    Raises ToolNotFoundError with a clear, user-facing message if the
    tool is missing. The resolved absolute path is cached for the
    lifetime of the process.
    """
    if name in _TOOL_PATH_CACHE:
        return _TOOL_PATH_CACHE[name]
    found = shutil.which(name)
    if found is None:
        raise ToolNotFoundError(
            f"{name} must be installed and on your PATH. Install it from "
            "https://ffmpeg.org/download.html or via your package manager "
            "(e.g. `brew install ffmpeg`)."
        )
    absolute = str(Path(found).resolve())
    _TOOL_PATH_CACHE[name] = absolute
    return absolute


def _drain(pipe, chunks: list[bytes], cap: int, over_event: threading.Event) -> None:
    total = 0
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                return
            total += len(chunk)
            if total > cap:
                over_event.set()
                return
            chunks.append(chunk)
    except (OSError, ValueError):
        return


def _kill_process_group(proc: "subprocess.Popen") -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def run_tool(
    executable: str,
    args: list[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ToolResult:
    """Run `[executable, *args]` with no shell, bounded I/O, and a hard timeout.

    - stdin is always DEVNULL (the process can never block waiting on
      interactive input).
    - stdout/stderr are drained by background threads into bounded
      buffers; if either exceeds its cap, the process is killed early
      (this is what protects against a process that spams output).
    - On POSIX, the child runs in its own session (`start_new_session`)
      so a timeout kill (`killpg`) also takes down any grandchildren.
    - `stderr_tail` is only ever the last `limits.stderr_tail_bytes`
      bytes captured, so callers can safely log/store it in receipts.
    - `stderr` is the full capture, bounded only by
      `limits.max_stderr_capture_bytes` (much larger than
      `stderr_tail_bytes`) — for callers (e.g. silencedetect parsing)
      that need every line of stderr output, not just the tail.
    """
    command = [executable, *args]
    popen_kwargs: dict = {}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        **popen_kwargs,
    )

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_over = threading.Event()
    stderr_over = threading.Event()

    t_out = threading.Thread(
        target=_drain,
        args=(proc.stdout, stdout_chunks, limits.max_stdout_capture_bytes, stdout_over),
        daemon=True,
    )
    t_err = threading.Thread(
        target=_drain,
        args=(proc.stderr, stderr_chunks, limits.max_stderr_capture_bytes, stderr_over),
        daemon=True,
    )
    t_out.start()
    t_err.start()

    timed_out = False
    deadline = time.monotonic() + timeout_s
    while True:
        if proc.poll() is not None:
            break
        if stdout_over.is_set() or stderr_over.is_set():
            _kill_process_group(proc)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _kill_process_group(proc)
            break
        time.sleep(0.05)

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass

    t_out.join(timeout=5)
    t_err.join(timeout=5)

    stdout_bytes = b"".join(stdout_chunks)
    stderr_bytes = b"".join(stderr_chunks)
    stderr_tail = stderr_bytes[-limits.stderr_tail_bytes :]

    return ToolResult(
        command=command,
        returncode=proc.returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        stderr_tail=stderr_tail.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        stdout_truncated=stdout_over.is_set(),
        stderr_truncated=stderr_over.is_set(),
    )
