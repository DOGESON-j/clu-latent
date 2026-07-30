"""SHA-256 hashing helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .security.jsonl import read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of a file's contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256_sidecar(sidecar_path: Path, digest: str, referenced_filename: str) -> None:
    """Write a `shasum -c`-compatible sidecar file.

    Format: "<digest>  <filename>\n" (two spaces, matching `sha256sum`).
    """
    Path(sidecar_path).write_text(f"{digest}  {referenced_filename}\n", encoding="utf-8")


def read_sha256_sidecar(sidecar_path: Path, *, limits: Limits = DEFAULT_LIMITS) -> str:
    """Read back the digest written by `write_sha256_sidecar`.

    sources/source.sha256 is package-controlled (untrusted) input, so it
    is read via `security.jsonl.read_bytes_bounded` (max_sidecar_bytes)
    instead of an unbounded `read_text` — the size is checked before any
    file content is loaded into memory. Raises `JsonlLimitError` (via
    `read_bytes_bounded`) if the sidecar exceeds `max_sidecar_bytes`.
    """
    raw = read_bytes_bounded(
        sidecar_path, max_bytes=limits.max_sidecar_bytes, field_name="sources/source.sha256"
    )
    line = raw.decode("utf-8").strip()
    digest = line.split()[0]
    return digest
