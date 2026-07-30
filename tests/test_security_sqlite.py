from __future__ import annotations

import sqlite3

import pytest

from clu_latent.security.sqlite import SqliteOpenError, open_readonly, refuse_symlink_overwrite


def _make_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, payload_text TEXT)")
    conn.execute(
        "INSERT INTO records VALUES (?, ?)", ("1", "hello 'world' -- % _ wildcard")
    )
    conn.commit()
    conn.close()


def test_open_readonly_opens_valid_db(tmp_path):
    db_path = tmp_path / "search.sqlite"
    _make_db(db_path)

    conn = open_readonly(db_path)
    try:
        rows = conn.execute("SELECT id, payload_text FROM records").fetchall()
        assert len(rows) == 1
    finally:
        conn.close()


def test_open_readonly_rejects_missing_file(tmp_path):
    with pytest.raises(SqliteOpenError, match="not found"):
        open_readonly(tmp_path / "missing.sqlite")


def test_open_readonly_rejects_corrupt_file(tmp_path):
    db_path = tmp_path / "corrupt.sqlite"
    db_path.write_bytes(b"not a real sqlite file at all, just garbage bytes" * 10)

    with pytest.raises(SqliteOpenError, match="corrupt|not a valid"):
        open_readonly(db_path)


def test_open_readonly_rejects_symlink(tmp_path):
    real_db = tmp_path / "real.sqlite"
    _make_db(real_db)
    link = tmp_path / "link.sqlite"
    link.symlink_to(real_db)

    with pytest.raises(SqliteOpenError, match="symlink"):
        open_readonly(link)


def test_open_readonly_connection_cannot_write(tmp_path):
    db_path = tmp_path / "search.sqlite"
    _make_db(db_path)

    conn = open_readonly(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO records VALUES ('2', 'nope')")
    finally:
        conn.close()


@pytest.mark.parametrize(
    "needle",
    [
        "hello",
        "'; DROP TABLE records; --",
        "100% wild_card",
        '"quoted"',
    ],
)
def test_open_readonly_supports_parameterized_search_terms(tmp_path, needle):
    db_path = tmp_path / "search.sqlite"
    _make_db(db_path)

    conn = open_readonly(db_path)
    try:
        pattern = f"%{needle.lower()}%"
        rows = conn.execute(
            "SELECT id FROM records WHERE LOWER(payload_text) LIKE ?", (pattern,)
        ).fetchall()
        assert isinstance(rows, list)
    finally:
        conn.close()


def test_refuse_symlink_overwrite_raises_on_symlink(tmp_path):
    real = tmp_path / "real.sqlite"
    real.write_text("x")
    link = tmp_path / "link.sqlite"
    link.symlink_to(real)

    with pytest.raises(SqliteOpenError):
        refuse_symlink_overwrite(link)


def test_refuse_symlink_overwrite_allows_plain_file(tmp_path):
    real = tmp_path / "real.sqlite"
    real.write_text("x")
    refuse_symlink_overwrite(real)  # should not raise
