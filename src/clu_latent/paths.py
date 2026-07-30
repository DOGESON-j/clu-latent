"""Path helpers.

All paths stored inside a .clulatent package (manifest.json, track
payloads, etc.) must be relative, POSIX-style ("/" separated) strings,
regardless of the host OS. These helpers centralize that conversion so
no module accidentally writes an OS-specific or absolute path into the
package.

`normalize_posix` is a thin, backward-compatible wrapper around the
strict lexical validator in `security.paths` — it no longer silently
rewrites backslashes or collapses repeated slashes; anything that
would need rewriting is rejected instead (see security/paths.py for
the full rule set and rationale). Filesystem-level containment and
symlink safety for *untrusted* package-relative paths (manifest
fields, JSONL payload paths) is handled by
`security.paths.resolve_in_package`, not here — this module only
covers lexical normalization and building paths from already-trusted,
freshly-created filesystem Path objects.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .security.paths import validate_relative_posix


def to_posix_relative(path: Path, base: Path) -> str:
    """Return `path` relative to `base` as a POSIX-style string.

    Example: to_posix_relative(pkg/"media/keyframes/000000.jpg", pkg)
             -> "media/keyframes/000000.jpg"
    """
    relative = Path(path).resolve().relative_to(Path(base).resolve())
    return PurePosixPath(*relative.parts).as_posix()


def normalize_posix(relative_str: str) -> str:
    """Validate an already-relative path string is clean POSIX form.

    Rejects absolute paths, `..` traversal, backslashes, empty
    segments, repeated slashes, `.` segments, and Windows drive/UNC
    paths. Raises ValueError (PathSecurityError, a ValueError
    subclass) on any violation; returns the value unchanged otherwise.
    """
    return validate_relative_posix(relative_str, field_name="path")
