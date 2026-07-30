"""Tests for `clulatent validate` and `clulatent reindex`.

Uses a real ffmpeg-generated tiny video to build genuine packages, then
tampers with specific files to exercise each validation failure mode.
Skips cleanly if ffmpeg/ffprobe are not available.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.index import query_search_index
from clu_latent.ingest import ingest_video
from clu_latent.reindex import ReindexError, reindex_package
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    directory = tmp_path_factory.mktemp("clulatent_validate_fixture")
    video_path = directory / "tiny.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return video_path


@pytest.fixture
def valid_package(tmp_path, tiny_video):
    """Ingest a fresh, valid package for each test."""
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def test_validate_passes_on_valid_package(valid_package):
    report = validate_package(valid_package)
    assert report.valid is True
    assert report.errors == []
    assert report.manifest is not None


def test_validate_fails_if_manifest_missing(valid_package):
    (valid_package / "manifest.json").unlink()

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("manifest.json" in err for err in report.errors)


def test_validate_fails_if_source_hash_wrong(valid_package):
    source_path = valid_package / "sources" / "source.mp4"
    with source_path.open("ab") as handle:
        handle.write(b"corruption")

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("hash does not match" in err for err in report.errors)


def test_validate_fails_if_track_has_invalid_json(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("invalid JSON" in err for err in report.errors)


def test_validate_fails_if_record_count_mismatches(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    lines = keyframes_path.read_text(encoding="utf-8").splitlines()
    assert lines, "fixture must produce at least one keyframe"
    # Remove one valid record without updating manifest.record_count.
    keyframes_path.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""), encoding="utf-8")

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("record_count mismatch" in err for err in report.errors)


def test_validate_fails_if_keyframe_file_missing(valid_package):
    keyframes_dir = valid_package / "media" / "keyframes"
    first_frame = sorted(keyframes_dir.glob("*.jpg"))[0]
    first_frame.unlink()

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("does not exist on disk" in err for err in report.errors)


def test_validate_fails_if_index_missing(valid_package):
    (valid_package / "index" / "search.sqlite").unlink()

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("index file not found" in err for err in report.errors)


def test_validate_fails_if_receipts_missing(valid_package):
    (valid_package / "receipts" / "ingest.jsonl").unlink()

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("receipts file not found" in err for err in report.errors)


def test_validate_on_nonexistent_package():
    report = validate_package("/tmp/definitely_not_a_real_clulatent_package")
    assert report.valid is False
    assert len(report.errors) == 1


def test_reindex_rebuilds_search_sqlite(valid_package):
    sqlite_path = valid_package / "index" / "search.sqlite"
    sqlite_path.unlink()
    assert not sqlite_path.exists()

    result = reindex_package(valid_package)

    assert sqlite_path.exists()
    assert result.indexed_count >= 1
    assert result.tracks_indexed["keyframes"] >= 1


def test_reindex_does_not_modify_canonical_files(valid_package):
    manifest_before = (valid_package / "manifest.json").read_bytes()
    keyframes_before = (valid_package / "tracks" / "keyframes.jsonl").read_bytes()
    source_before = (valid_package / "sources" / "source.mp4").read_bytes()

    reindex_package(valid_package)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / "tracks" / "keyframes.jsonl").read_bytes() == keyframes_before
    assert (valid_package / "sources" / "source.mp4").read_bytes() == source_before


def test_reindex_recovers_from_corrupted_sqlite(valid_package):
    sqlite_path = valid_package / "index" / "search.sqlite"
    sqlite_path.write_bytes(b"not a real sqlite file")

    reindex_package(valid_package)

    # A fresh, valid sqlite file must now exist and be queryable.
    conn = sqlite3.connect(sqlite_path)
    conn.execute("SELECT COUNT(*) FROM records").fetchone()
    conn.close()


def test_query_still_works_after_reindex(valid_package):
    sqlite_path = valid_package / "index" / "search.sqlite"
    sqlite_path.unlink()
    reindex_package(valid_package)

    keyframe_matches = query_search_index(sqlite_path, "keyframe")
    ffmpeg_matches = query_search_index(sqlite_path, "ffmpeg")

    assert len(keyframe_matches) >= 1
    assert len(ffmpeg_matches) >= 1


def test_reindex_fails_cleanly_on_missing_package(tmp_path):
    with pytest.raises(ReindexError):
        reindex_package(tmp_path / "not_a_package.clulatent")


def test_cli_validate_exit_codes(valid_package):
    runner = CliRunner()

    ok_result = runner.invoke(app, ["validate", str(valid_package)])
    assert ok_result.exit_code == 0
    assert "PASS" in ok_result.output

    (valid_package / "manifest.json").unlink()
    fail_result = runner.invoke(app, ["validate", str(valid_package)])
    assert fail_result.exit_code == 1
    assert "FAIL" in fail_result.output


def test_cli_reindex_exit_code(valid_package):
    runner = CliRunner()
    (valid_package / "index" / "search.sqlite").unlink()

    result = runner.invoke(app, ["reindex", str(valid_package)])

    assert result.exit_code == 0
    assert "records indexed" in result.output
    assert (valid_package / "index" / "search.sqlite").exists()


def _load_manifest_dict(package_path) -> dict:
    return json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest_dict(package_path, manifest_dict: dict) -> None:
    (package_path / "manifest.json").write_text(
        json.dumps(manifest_dict, indent=2), encoding="utf-8"
    )


def _first_track(manifest_dict: dict, name: str = "keyframes") -> dict:
    for track in manifest_dict["tracks"]:
        if track["name"] == name:
            return track
    raise AssertionError(f"track {name!r} not found in manifest")


# --- Patch 1: schema_id / schema_version cross-checks -----------------------


def test_validate_fails_on_track_schema_id_mismatch(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    _first_track(manifest_dict)["schema_id"] = "clulatent.track.something_else"
    _write_manifest_dict(valid_package, manifest_dict)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("schema_id mismatch" in err for err in report.errors)
    assert any("keyframes" in err and "clulatent.track.something_else" in err for err in report.errors)


def test_validate_fails_on_track_schema_version_mismatch(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    _first_track(manifest_dict)["schema_version"] = "99.9.9"
    _write_manifest_dict(valid_package, manifest_dict)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("schema_version mismatch" in err for err in report.errors)
    assert any("99.9.9" in err for err in report.errors)


# --- Patch 3: index.status / index.canonical are enforced at schema layer ---


def test_validate_fails_on_invalid_index_status_at_schema_layer(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    manifest_dict["index"]["status"] = "canonical"
    _write_manifest_dict(valid_package, manifest_dict)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("failed schema validation" in err for err in report.errors)


def test_validate_fails_on_invalid_index_canonical_at_schema_layer(valid_package):
    manifest_dict = _load_manifest_dict(valid_package)
    manifest_dict["index"]["canonical"] = True
    _write_manifest_dict(valid_package, manifest_dict)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("failed schema validation" in err for err in report.errors)


# --- Patch 2: reindex tolerates malformed individual JSONL lines ------------


def test_reindex_skips_malformed_line_and_indexes_valid_records(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    valid_lines_before = [
        line for line in keyframes_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    expected_valid_count = len(valid_lines_before)

    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    result = reindex_package(valid_package)

    assert result.tracks_indexed["keyframes"] == expected_valid_count
    assert result.indexed_count >= expected_valid_count


def test_reindex_reports_warning_for_malformed_line(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    valid_line_count = len(
        [line for line in keyframes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    )
    bad_lineno = valid_line_count + 1
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    result = reindex_package(valid_package)

    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "keyframes.jsonl" in warning
    assert f":{bad_lineno}:" in warning
    assert "invalid JSON" in warning
    assert "line skipped" in warning


def test_query_works_after_tolerant_reindex(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    result = reindex_package(valid_package)

    matches = query_search_index(result.sqlite_path, "keyframe")
    assert len(matches) >= 1


def test_reindex_tolerant_does_not_modify_canonical_files(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    manifest_before = (valid_package / "manifest.json").read_bytes()
    keyframes_before = keyframes_path.read_bytes()
    source_before = (valid_package / "sources" / "source.mp4").read_bytes()

    reindex_package(valid_package)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert keyframes_path.read_bytes() == keyframes_before
    assert (valid_package / "sources" / "source.mp4").read_bytes() == source_before


# --- media.keyframes.dir containment check -----------------------------


def test_validate_fails_if_keyframes_dir_escapes_package_via_symlink(tmp_path, valid_package):
    # A lexically-clean, relative path ("media/evil_keyframes") that is
    # actually a symlink pointing outside the package — this can only be
    # caught by the filesystem containment/symlink check in
    # security.paths.resolve_in_package, not by lexical validation alone.
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    symlink_path = valid_package / "media" / "evil_keyframes"
    symlink_path.symlink_to(outside_dir)

    manifest_dict = _load_manifest_dict(valid_package)
    manifest_dict["media"]["keyframes"]["dir"] = "media/evil_keyframes"
    _write_manifest_dict(valid_package, manifest_dict)

    report = validate_package(valid_package)

    assert report.valid is False
    assert any("media.keyframes.dir" in err for err in report.errors)


def test_cli_reindex_prints_warnings_for_malformed_line(valid_package):
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    with keyframes_path.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")

    runner = CliRunner()
    result = runner.invoke(app, ["reindex", str(valid_package)])

    assert result.exit_code == 0
    assert "warning" in result.output.lower()
    assert "keyframes.jsonl" in result.output
    assert "records indexed" in result.output
