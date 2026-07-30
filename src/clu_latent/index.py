"""index/search.sqlite: a derived, rebuildable search index.

This module is the ONLY place that touches search.sqlite. The database
is always built fresh from tracks/*.jsonl and is never read back as a
source of truth by anything else in this package — if it's ever lost
or corrupted, it can be regenerated from the canonical JSONL tracks.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .event import EventEnvelope
from .security.sqlite import SqliteOpenError, open_readonly, refuse_symlink_overwrite

__all__ = ["SqliteOpenError", "build_search_index", "query_search_index"]

_SCHEMA = """
CREATE TABLE records (
    record_id TEXT PRIMARY KEY,
    track_name TEXT NOT NULL,
    record_type TEXT NOT NULL,
    t_start_ms INTEGER NOT NULL,
    t_end_ms INTEGER NOT NULL,
    producer_name TEXT,
    producer_version TEXT,
    confidence REAL,
    payload_text TEXT NOT NULL
);
CREATE INDEX idx_records_t_start_ms ON records (t_start_ms);
CREATE INDEX idx_records_track_name ON records (track_name);
"""


def build_search_index(sqlite_path: Path, tracks: dict[str, list[EventEnvelope]]) -> int:
    """Rebuild search.sqlite from in-memory track records.

    Always overwrites any existing file at `sqlite_path`. Returns the
    total number of indexed records.
    """
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    refuse_symlink_overwrite(sqlite_path)
    if sqlite_path.exists():
        sqlite_path.unlink()

    conn = sqlite3.connect(sqlite_path)
    try:
        conn.executescript(_SCHEMA)
        total = 0
        for track_name, events in tracks.items():
            for event in events:
                payload_text = json.dumps(event.payload, sort_keys=True)
                conn.execute(
                    """
                    INSERT INTO records (
                        record_id, track_name, record_type, t_start_ms, t_end_ms,
                        producer_name, producer_version, confidence, payload_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        track_name,
                        event.type,
                        event.t_start_ms,
                        event.t_end_ms,
                        event.producer.name,
                        event.producer.version,
                        event.confidence,
                        payload_text,
                    ),
                )
                total += 1
        conn.commit()
        return total
    finally:
        conn.close()


def query_search_index(sqlite_path: Path, search_text: str) -> list[dict[str, Any]]:
    """Case-insensitive substring search across type/track/producer/payload.

    Opens the derived index read-only via `security.sqlite.open_readonly`
    — a missing, corrupt, or symlinked index raises `SqliteOpenError`
    (never a raw sqlite3 exception) recommending `clulatent reindex`.
    All search text is passed as a bound parameter; it is never
    interpolated into the SQL string.
    """
    needle = f"%{search_text.lower()}%"
    conn = open_readonly(sqlite_path)
    try:
        rows = conn.execute(
            """
            SELECT record_id, track_name, record_type, t_start_ms, t_end_ms,
                   producer_name, producer_version, confidence, payload_text
            FROM records
            WHERE LOWER(record_type) LIKE ?
               OR LOWER(track_name) LIKE ?
               OR LOWER(producer_name) LIKE ?
               OR LOWER(payload_text) LIKE ?
            ORDER BY t_start_ms ASC
            """,
            (needle, needle, needle, needle),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
