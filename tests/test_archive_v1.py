import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent.archive import (
    ARCHIVE_MARKER,
    ARCHIVE_MEDIA_TYPE,
    ArchiveError,
    ArchiveLimits,
    pack_package,
    unpack_archive,
    verify_archive,
)
from clu_latent.cli import app
from clu_latent.conformance_fixtures import build_fixture
from clu_latent.package_reader import open_package
from clu_latent.v1_package_index import write_v1_package_index

runner = CliRunner()


def _package(tmp_path: Path) -> Path:
    package = build_fixture("valid_keyframes_audio", tmp_path / "source.clulatent")
    write_v1_package_index(package)
    return package


def _marker() -> str:
    return json.dumps(
        {"format": "1.0", "media_type": ARCHIVE_MEDIA_TYPE},
        sort_keys=True,
        separators=(",", ":"),
    )


def _raw_archive(path: Path, members: list[tuple[zipfile.ZipInfo | str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(ARCHIVE_MARKER, _marker())
        archive.writestr("manifest.json", "{}")
        for name, data in members:
            archive.writestr(name, data)
    return path


def test_pack_is_byte_deterministic_and_uses_normalized_metadata(tmp_path):
    package = _package(tmp_path)
    first = tmp_path / "first.clulatent"
    second = tmp_path / "second.clulatent"
    one = pack_package(package, first)
    two = pack_package(package, second)
    assert one.sha256 == two.sha256
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_unpack_round_trip_and_reader_archive_parity(tmp_path):
    package = _package(tmp_path)
    archive = tmp_path / "portable.clulatent"
    pack_package(package, archive)
    extracted = unpack_archive(archive, tmp_path / "extracted.clulatent")
    assert {
        path.relative_to(package): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in package.rglob("*") if path.is_file()
    } == {
        path.relative_to(extracted): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in extracted.rglob("*") if path.is_file()
    }
    with open_package(package) as directory_reader, open_package(archive) as archive_reader:
        assert archive_reader.package_id == directory_reader.package_id
        assert archive_reader.list_tracks() == directory_reader.list_tracks()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/escape", r"..\\escape"])
def test_unsafe_member_paths_rejected(tmp_path, name):
    archive = _raw_archive(tmp_path / "bad.clulatent", [(name, b"x")])
    with pytest.raises(ArchiveError, match="unsafe archive member"):
        verify_archive(archive)


def test_symlink_member_rejected(tmp_path):
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = _raw_archive(tmp_path / "bad.clulatent", [(info, b"manifest.json")])
    with pytest.raises(ArchiveError, match="non-regular"):
        verify_archive(archive)


def test_duplicate_and_prefix_conflicts_rejected(tmp_path):
    duplicate = tmp_path / "duplicate.clulatent"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr(ARCHIVE_MARKER, _marker())
        archive.writestr("manifest.json", "{}")
        archive.writestr("tracks/a", "1")
        archive.writestr("TRACKS/A", "2")
    with pytest.raises(ArchiveError, match="duplicate"):
        verify_archive(duplicate)
    prefix = _raw_archive(
        tmp_path / "prefix.clulatent",
        [("tracks/a", b"1"), ("tracks/a/child", b"2")],
    )
    with pytest.raises(ArchiveError, match="conflict"):
        verify_archive(prefix)


def test_member_total_file_ratio_and_nested_limits(tmp_path):
    archive = _raw_archive(
        tmp_path / "limits.clulatent",
        [("a", b"1234"), ("b", b"5678")],
    )
    with pytest.raises(ArchiveError, match="member limit"):
        verify_archive(archive, limits=ArchiveLimits(max_members=2))
    with pytest.raises(ArchiveError, match="member too large"):
        verify_archive(archive, limits=ArchiveLimits(max_file_size=3))
    with pytest.raises(ArchiveError, match="total size"):
        verify_archive(archive, limits=ArchiveLimits(max_total_size=5))
    bomb = tmp_path / "ratio.clulatent"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr(ARCHIVE_MARKER, _marker())
        handle.writestr("manifest.json", "{}")
        handle.writestr("bomb", b"0" * 100_000)
    with pytest.raises(ArchiveError, match="compression ratio"):
        verify_archive(bomb, limits=ArchiveLimits(max_compression_ratio=10))
    nested = _raw_archive(tmp_path / "nested.clulatent", [("nested.zip", b"PK")])
    with pytest.raises(ArchiveError, match="nested archive"):
        verify_archive(nested)


def test_pack_rejects_source_symlink(tmp_path):
    package = _package(tmp_path)
    try:
        os.symlink(package / "manifest.json", package / "linked-manifest")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ArchiveError, match="symlink"):
        pack_package(package, tmp_path / "bad.clulatent")


def test_archive_cli_and_primary_read_commands(tmp_path):
    package = _package(tmp_path)
    archive = tmp_path / "portable.clulatent"
    assert runner.invoke(app, ["pack", str(package), "-o", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["archive", "verify", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["validate", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["profile", "verify", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["package-index", "verify", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["package", "inspect", str(archive)]).exit_code == 0
    assert runner.invoke(app, ["agent-read", "summary", str(archive)]).exit_code == 0
    assert runner.invoke(
        app, ["agent-read", "window", str(archive), "--time", "0s"]
    ).exit_code == 0
    assert runner.invoke(
        app, ["ask", str(archive), "What evidence exists around 0s?"]
    ).exit_code == 0
