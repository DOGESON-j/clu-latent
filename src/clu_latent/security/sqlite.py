"""SQLite hardening for the derived, disposable search index.

index/search.sqlite is derived and never canonical (see index.py's
module docstring). It is still untrusted input on the *read* path: a
package's sqlite file may be missing, corrupt, hand-edited, or
maliciously crafted. Every query-time open goes through
`open_readonly` here; the writer (build path, index.py) stays
read-write since it always creates the file fresh from in-memory
canonical data, but still refuses to write through a symlink.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SqliteOpenError(RuntimeError):
    """Raised when the derived search index cannot be safely opened."""


def open_readonly(path: Path) -> sqlite3.Connection:
    """Open `path` read-only with defensive settings.

    Raises SqliteOpenError (never a raw sqlite3 exception) if the file
    is missing, is a symlink, or is not a valid, readable SQLite
    database — always recommending `clulatent reindex` to recover.
    """
    resolved = Path(path)

    if resolved.is_symlink():
        raise SqliteOpenError(f"refusing to open search index through a symlink: {resolved}")
    if not resolved.exists():
        raise SqliteOpenError(
            f"search index not found: {resolved}. Run `clulatent reindex` to rebuild it."
        )

    uri = f"file:{resolved.resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:
        raise SqliteOpenError(
            f"search index could not be opened read-only: {exc}. "
            "Run `clulatent reindex` to rebuild it."
        ) from exc

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        try:
            conn.execute("PRAGMA trusted_schema = OFF")
        except sqlite3.OperationalError:
            pass
        if hasattr(conn, "setlimit") and hasattr(sqlite3, "SQLITE_LIMIT_LENGTH"):
            try:
                conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
            except sqlite3.OperationalError:
                pass
        # Sanity probe: fails cleanly (DatabaseError) for a corrupt or
        # non-SQLite file instead of surfacing a confusing error later.
        conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise SqliteOpenError(
            f"search index is corrupt or not a valid SQLite database: {exc}. "
            "Run `clulatent reindex` to rebuild it."
        ) from exc

    return conn


def refuse_symlink_overwrite(path: Path) -> None:
    """Raise SqliteOpenError if `path` is a symlink (write-path guard)."""
    if Path(path).is_symlink():
        raise SqliteOpenError(f"refusing to overwrite search index through a symlink: {path}")
