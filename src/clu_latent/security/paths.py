"""Package path containment.

Every path stored inside a `.clulatent` package — manifest.json fields
(`source.stored_path`, `tracks[].file`, `media.keyframes.dir`,
`index.file`, `receipts.file`) and per-record `payload.path` values in
tracks/*.jsonl — is untrusted. It may come from a hand-edited or
maliciously crafted package. This module is the only place that turns
one of those strings into a filesystem path that is actually opened.

Two-stage validation:
  1. Lexical: `validate_relative_posix` rejects anything that isn't a
     clean, relative, POSIX-style path before touching the filesystem.
  2. Filesystem: `resolve_in_package` resolves the real path (following
     any symlinked *directories* on the way) and verifies it is still
     contained under the resolved package root, then rejects the final
     component if it is itself a symlink.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

_DRIVE_LETTER_RE = re.compile(r"^[A-Za-z]:")


class PathSecurityError(ValueError):
    """Raised when an untrusted package-relative path fails containment."""


def validate_relative_posix(value: str, *, field_name: str = "path") -> str:
    """Strictly validate an untrusted package-relative path string.

    Returns `value` unchanged if it passes every rule. Never silently
    rewrites the input (e.g. never converts backslashes) — anything that
    would require rewriting is rejected instead, so a package's on-disk
    string is exactly what gets checked.

    Rejects:
      - non-string / empty values
      - NUL bytes
      - backslashes
      - Windows drive-letter prefixes (`C:\\...`, `C:/...`)
      - absolute paths (leading `/`)
      - repeated slashes (also catches UNC-style `//server/share`)
      - empty path segments
      - `.` segments
      - `..` segments
    """
    if not isinstance(value, str) or value == "":
        raise PathSecurityError(f"{field_name} must be a non-empty string, got {value!r}")
    if "\x00" in value:
        raise PathSecurityError(f"{field_name} must not contain NUL bytes: {value!r}")
    if "\\" in value:
        raise PathSecurityError(f"{field_name} must not contain backslashes: {value!r}")
    if _DRIVE_LETTER_RE.match(value):
        raise PathSecurityError(f"{field_name} looks like a Windows drive path: {value!r}")
    if value.startswith("/"):
        raise PathSecurityError(f"{field_name} must be relative, got absolute path: {value!r}")
    if "//" in value:
        raise PathSecurityError(f"{field_name} must not contain repeated slashes: {value!r}")

    for segment in value.split("/"):
        if segment == "":
            raise PathSecurityError(f"{field_name} must not contain empty segments: {value!r}")
        if segment == "..":
            raise PathSecurityError(f"{field_name} must not contain '..' segments: {value!r}")
        if segment == ".":
            raise PathSecurityError(f"{field_name} must not contain '.' segments: {value!r}")

    return value


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_in_package(
    package_root: Path,
    relative_value: str,
    *,
    field_name: str = "path",
    for_write: bool = False,
) -> Path:
    """Resolve `relative_value` against `package_root`, contained and symlink-safe.

    For reads: if the target (or its parent directory) does not yet
    exist on disk, this returns a plain joined path so the caller's
    normal "not found" handling still applies — that is not a security
    failure, just a missing file. If the final path component exists
    and is a symlink, this always raises (regardless of `for_write`):
    reads must not follow a symlink out of the package, and writes must
    never overwrite one.

    For writes: the parent directory must already exist and resolve
    inside the package root (CLULatent always pre-creates package
    subdirectories before writing into them; a missing parent for a
    write is treated as a security failure, not a normal miss).

    Raises PathSecurityError on any lexical or containment violation.
    """
    validate_relative_posix(relative_value, field_name=field_name)

    root_resolved = Path(package_root).resolve(strict=True)
    raw_candidate = root_resolved / PurePosixPath(relative_value).as_posix()
    parent_dir = raw_candidate.parent

    if not parent_dir.exists():
        if for_write:
            raise PathSecurityError(
                f"{field_name}: parent directory does not exist for write: {relative_value!r}"
            )
        if not _is_within(raw_candidate, root_resolved):
            raise PathSecurityError(
                f"{field_name}: resolves outside package root: {relative_value!r}"
            )
        return raw_candidate

    parent_resolved = parent_dir.resolve(strict=True)
    if parent_resolved != root_resolved and root_resolved not in parent_resolved.parents:
        raise PathSecurityError(
            f"{field_name}: resolves outside package root: {relative_value!r}"
        )

    result = parent_resolved / raw_candidate.name

    if result.is_symlink():
        action = "write through" if for_write else "read through"
        raise PathSecurityError(f"{field_name}: refusing to {action} a symlink: {relative_value!r}")

    return result
