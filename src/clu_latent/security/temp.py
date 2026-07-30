"""Secure temp staging and atomic package finalization.

Package creation must never leave a half-written directory at the
final output path. Every package is built inside a temp directory
that is a sibling of the final output path (same parent, and
therefore always the same filesystem, so the final commit can use an
atomic rename) and is only renamed into place after every ingest step
succeeds. On any failure, the temp directory is removed and nothing is
left at the final output path.
"""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class PackageFinalizeError(RuntimeError):
    """Raised when a package cannot be safely staged or committed."""


@contextmanager
def staged_package_dir(output_path: Path) -> Iterator[Path]:
    """Create and yield a temp staging directory next to `output_path`.

    The temp directory is always removed when the `with` block exits —
    whether by exception, or normally without a caller-initiated
    `commit_package` having already moved it away. This guarantees a
    failed or aborted ingest leaves no temp directory behind.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = output_path.parent / f".{output_path.name}.tmp-{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        yield temp_dir
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def commit_package(temp_dir: Path, output_path: Path, *, force: bool = False) -> None:
    """Atomically move `temp_dir` into place at `output_path`.

    - Refuses to overwrite a symlink at `output_path`, even with
      force=True (that would silently write through the link to
      whatever it points at).
    - With force=False, refuses if `output_path` already exists at all
      (matches the existing `--force` CLI contract).
    - `temp_dir` and `output_path` are always siblings (same parent
      directory, by construction of `staged_package_dir`), so the
      final `os.rename` is a same-filesystem atomic rename, not a
      cross-filesystem copy.
    """
    temp_dir = Path(temp_dir)
    output_path = Path(output_path)

    if output_path.is_symlink():
        raise PackageFinalizeError(
            f"refusing to overwrite a symlink at output path: {output_path}"
        )
    if output_path.exists():
        if not force:
            raise PackageFinalizeError(
                f"output path already exists: {output_path}. Use --force to overwrite it."
            )
        if output_path.is_dir():
            shutil.rmtree(output_path)
        else:
            output_path.unlink()

    os.rename(temp_dir, output_path)
