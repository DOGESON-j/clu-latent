"""Tests for Phase 1.7.5 package locking: integrity lock + operation lock.

Uses a real ffmpeg-generated tiny video to build genuine packages (same
fixture pattern as test_validate_reindex.py). Skips cleanly if
ffmpeg/ffprobe are not available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent.cli import app
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.security.console import safe_console_text
from clu_latent.security.operation_lock import (
    OperationLockError,
    ingest_target_lock,
    operation_lock,
    read_ingest_target_lock,
    read_operation_lock,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("clulatent_lock_fixture")
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
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


# --- 1. Lock file creation ---------------------------------------------


def test_lock_creates_json_and_sha256(valid_package):
    result = lock_mod.create_lock(valid_package)

    assert result.lock_json_path.exists()
    assert result.lock_sha256_path.exists()
    assert result.file_count > 0


def test_lock_json_is_deterministic_and_sha256_matches(valid_package):
    result = lock_mod.create_lock(valid_package)

    raw = result.lock_json_path.read_bytes()
    import hashlib

    digest = hashlib.sha256(raw).hexdigest()
    sidecar = result.lock_sha256_path.read_text(encoding="utf-8").strip()
    assert sidecar.startswith(digest)


def test_lock_excludes_index_by_default(valid_package):
    lock_mod.create_lock(valid_package)
    lock, _ = lock_mod.read_lock(valid_package)

    assert lock.policy.include_index is False
    assert not any(entry.path.startswith("index/") for entry in lock.files)


def test_lock_includes_index_when_requested(valid_package):
    lock_mod.create_lock(valid_package, include_index=True)
    lock, _ = lock_mod.read_lock(valid_package)

    assert lock.policy.include_index is True
    assert any(entry.path.startswith("index/") for entry in lock.files)


def test_relock_without_force_is_refused(valid_package):
    lock_mod.create_lock(valid_package)

    with pytest.raises(lock_mod.LockError, match="already locked"):
        lock_mod.create_lock(valid_package)


def test_relock_with_force_succeeds(valid_package):
    lock_mod.create_lock(valid_package)
    original = (valid_package / "lock" / "package.lock.json").read_bytes()

    # Change a tracked file so the replacement lock has different digests,
    # proving --force actually rewrote the lock rather than no-oping.
    with (valid_package / "manifest.json").open("a", encoding="utf-8") as handle:
        handle.write("\n")

    result = lock_mod.create_lock(valid_package, force=True)
    assert result.lock_json_path.exists()
    assert result.lock_sha256_path.exists()

    replaced = (valid_package / "lock" / "package.lock.json").read_bytes()
    assert replaced != original

    # The replacement lock must match the (now modified) package.
    assert lock_mod.verify_lock(valid_package).valid is True


def test_cli_relock_requires_force(valid_package):
    runner = CliRunner()
    assert runner.invoke(app, ["lock", str(valid_package)]).exit_code == 0

    refused = runner.invoke(app, ["lock", str(valid_package)])
    assert refused.exit_code == 1
    assert "already locked" in refused.output

    forced = runner.invoke(app, ["lock", str(valid_package), "--force"])
    assert forced.exit_code == 0
    assert "Lock created" in forced.output


# --- 2. verify-lock: pass / fail on modify / delete ---------------------


def test_verify_lock_passes_on_untouched_package(valid_package):
    lock_mod.create_lock(valid_package)

    report = lock_mod.verify_lock(valid_package)

    assert report.valid is True
    assert report.changed == []
    assert report.missing == []


def test_verify_lock_fails_when_tracked_file_modified(valid_package):
    lock_mod.create_lock(valid_package)
    manifest_path = valid_package / "manifest.json"
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")

    report = lock_mod.verify_lock(valid_package)

    assert report.valid is False
    assert "manifest.json" in report.changed


def test_verify_lock_fails_when_tracked_file_deleted(valid_package):
    lock_mod.create_lock(valid_package)
    (valid_package / "receipts" / "ingest.jsonl").unlink()

    report = lock_mod.verify_lock(valid_package)

    assert report.valid is False
    assert "receipts/ingest.jsonl" in report.missing


# --- 3. strict mode: extras ----------------------------------------------


def test_verify_lock_strict_flags_extra_file(valid_package):
    lock_mod.create_lock(valid_package)
    (valid_package / "sources" / "extra_junk.bin").write_bytes(b"unexpected")

    report = lock_mod.verify_lock(valid_package, strict=True)

    assert report.valid is False
    assert "sources/extra_junk.bin" in report.extra


def test_verify_lock_non_strict_ignores_extra_file(valid_package):
    lock_mod.create_lock(valid_package)
    (valid_package / "sources" / "extra_junk.bin").write_bytes(b"unexpected")

    report = lock_mod.verify_lock(valid_package, strict=False)

    assert report.valid is True
    assert report.extra == []


def test_verify_lock_strict_never_flags_keyframes_as_extra(valid_package):
    lock_mod.create_lock(valid_package)

    report = lock_mod.verify_lock(valid_package, strict=True)

    assert report.valid is True
    assert not any(path.startswith("media/keyframes") for path in report.extra)


# --- 4. symlink refusals -------------------------------------------------


def test_lock_refuses_symlinked_lock_dir(valid_package, tmp_path):
    outside = tmp_path / "outside_lock"
    outside.mkdir()
    (valid_package / "lock").symlink_to(outside)

    with pytest.raises(lock_mod.LockError):
        lock_mod.create_lock(valid_package)


def test_verify_lock_refuses_symlinked_tracked_file(valid_package, tmp_path):
    lock_mod.create_lock(valid_package)
    keyframes_path = valid_package / "tracks" / "keyframes.jsonl"
    real_content = keyframes_path.read_bytes()
    outside_file = tmp_path / "outside_keyframes.jsonl"
    outside_file.write_bytes(real_content)
    keyframes_path.unlink()
    keyframes_path.symlink_to(outside_file)

    report = lock_mod.verify_lock(valid_package)

    assert report.valid is False
    assert any("symlink" in err for err in report.errors)


def test_operation_lock_refuses_symlinked_lock_dir(valid_package, tmp_path):
    outside = tmp_path / "outside_lock2"
    outside.mkdir()
    (valid_package / "lock").symlink_to(outside)

    with pytest.raises(OperationLockError):
        with operation_lock(valid_package, operation="test"):
            pass


# --- 5. operation lock: concurrency / cleanup ----------------------------


def test_operation_lock_blocks_concurrent_acquisition(valid_package):
    with operation_lock(valid_package, operation="lock"):
        info = read_operation_lock(valid_package)
        assert info is not None
        assert info.pid == os.getpid()

        with pytest.raises(OperationLockError):
            with operation_lock(valid_package, operation="reindex"):
                pass


def test_operation_lock_removed_on_success(valid_package):
    with operation_lock(valid_package, operation="lock"):
        pass

    assert read_operation_lock(valid_package) is None


def test_operation_lock_removed_on_failure(valid_package):
    with pytest.raises(RuntimeError):
        with operation_lock(valid_package, operation="lock"):
            raise RuntimeError("boom")

    assert read_operation_lock(valid_package) is None


# --- 6. stale lock handling -----------------------------------------------


def test_operation_lock_force_stale_clears_lock_with_dead_pid(valid_package):
    lock_dir = valid_package / "lock"
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / "package.operation.lock.json"
    # A pid essentially guaranteed not to be running.
    dead_pid = 2**30
    lock_path.write_text(
        (
            '{"pid": %d, "hostname": "h", "operation": "reindex", '
            '"created_at": "2020-01-01T00:00:00+00:00", '
            '"package_path": "%s", "tool_version": "0.1.0"}\n'
        )
        % (dead_pid, str(valid_package)),
        encoding="utf-8",
    )

    with pytest.raises(OperationLockError):
        with operation_lock(valid_package, operation="lock", force_stale=False):
            pass

    with operation_lock(valid_package, operation="lock", force_stale=True):
        pass

    assert read_operation_lock(valid_package) is None


def test_operation_lock_stale_by_age_even_if_pid_alive(valid_package):
    lock_dir = valid_package / "lock"
    lock_dir.mkdir(exist_ok=True)
    lock_path = lock_dir / "package.operation.lock.json"
    lock_path.write_text(
        (
            '{"pid": %d, "hostname": "h", "operation": "reindex", '
            '"created_at": "2020-01-01T00:00:00+00:00", '
            '"package_path": "%s", "tool_version": "0.1.0"}\n'
        )
        % (os.getpid(), str(valid_package)),
        encoding="utf-8",
    )
    old_time = time.time() - 1000
    os.utime(lock_path, (old_time, old_time))

    with operation_lock(valid_package, operation="lock", force_stale=True, stale_age_s=1.0):
        pass

    assert read_operation_lock(valid_package) is None


# --- 7. console sanitization ------------------------------------------


def test_verify_lock_report_paths_are_console_safe(valid_package):
    lock_mod.create_lock(valid_package)
    (valid_package / "receipts" / "ingest.jsonl").unlink()

    report = lock_mod.verify_lock(valid_package)

    for path in report.missing:
        assert safe_console_text(path) == path


# --- lock-status ------------------------------------------------------


def test_lock_status_unlocked(valid_package):
    status, report = lock_mod.lock_status(valid_package)
    assert status == "unlocked"
    assert report is None


def test_lock_status_locked(valid_package):
    lock_mod.create_lock(valid_package)
    status, report = lock_mod.lock_status(valid_package)
    assert status == "locked"
    assert report is not None
    assert report.valid is True


def test_lock_status_lock_invalid_after_tamper(valid_package):
    lock_mod.create_lock(valid_package)
    (valid_package / "manifest.json").write_bytes(
        (valid_package / "manifest.json").read_bytes() + b"\n"
    )
    status, report = lock_mod.lock_status(valid_package)
    assert status == "lock-invalid"
    assert report is not None
    assert report.valid is False


def test_tampered_sha256_reports_invalid_lock_hash(valid_package):
    result = lock_mod.create_lock(valid_package)
    # Rewrite the sidecar with a valid-looking but wrong digest, leaving
    # every tracked file untouched. The lock.json still matches the
    # package, so the ONLY failure must be the lock-hash mismatch.
    result.lock_sha256_path.write_text(
        f"{'0' * 64}  package.lock.json\n", encoding="utf-8"
    )

    report = lock_mod.verify_lock(valid_package)
    assert report.lock_hash_valid is False
    assert report.valid is False
    assert report.changed == []
    assert report.missing == []

    status, status_report = lock_mod.lock_status(valid_package)
    assert status == "lock-invalid"
    assert status_report is not None
    assert status_report.lock_hash_valid is False


def test_lock_status_partial_when_only_json_present(valid_package):
    result = lock_mod.create_lock(valid_package)
    result.lock_sha256_path.unlink()

    status, report = lock_mod.lock_status(valid_package)
    assert status == "lock-partial"
    assert report is None


# --- CLI wiring ---------------------------------------------------------


def test_cli_lock_and_verify_lock_roundtrip(valid_package):
    runner = CliRunner()

    lock_result = runner.invoke(app, ["lock", str(valid_package)])
    assert lock_result.exit_code == 0
    assert "Lock created" in lock_result.output

    verify_result = runner.invoke(app, ["verify-lock", str(valid_package)])
    assert verify_result.exit_code == 0
    assert "VALID" in verify_result.output


def test_cli_verify_lock_fails_after_tamper(valid_package):
    runner = CliRunner()
    runner.invoke(app, ["lock", str(valid_package)])
    (valid_package / "manifest.json").write_bytes(
        (valid_package / "manifest.json").read_bytes() + b"\n"
    )

    result = runner.invoke(app, ["verify-lock", str(valid_package)])

    assert result.exit_code == 1
    assert "INVALID" in result.output


def test_cli_lock_status(valid_package):
    runner = CliRunner()

    unlocked = runner.invoke(app, ["lock-status", str(valid_package)])
    assert unlocked.exit_code == 0
    assert "unlocked" in unlocked.output

    runner.invoke(app, ["lock", str(valid_package)])

    locked = runner.invoke(app, ["lock-status", str(valid_package)])
    assert locked.exit_code == 0
    assert "locked" in locked.output


def test_cli_reindex_respects_operation_lock(valid_package):
    with operation_lock(valid_package, operation="lock"):
        runner = CliRunner()
        result = runner.invoke(app, ["reindex", str(valid_package)])
        assert result.exit_code == 1
        assert "Error" in result.output


# --- Full suite sanity: verify_lock never modifies canonical files -----


def test_verify_lock_does_not_modify_canonical_files(valid_package):
    lock_mod.create_lock(valid_package)
    manifest_before = (valid_package / "manifest.json").read_bytes()
    keyframes_before = (valid_package / "tracks" / "keyframes.jsonl").read_bytes()

    lock_mod.verify_lock(valid_package, strict=True)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / "tracks" / "keyframes.jsonl").read_bytes() == keyframes_before


# --- ingest operation lock (Phase 1.7.5 gap fix) -------------------------


def test_ingest_blocked_by_existing_target_lock(tmp_path, tiny_video):
    output_path = tmp_path / "locked_target.clulatent"

    with ingest_target_lock(output_path, operation="ingest"):
        with pytest.raises(IngestError, match="locked"):
            ingest_video(tiny_video, output_path)

    # No package, and no lock file, should be left behind.
    assert not output_path.exists()
    assert read_ingest_target_lock(output_path) is None


def test_ingest_removes_target_lock_after_success(tmp_path, tiny_video):
    output_path = tmp_path / "success.clulatent"

    ingest_video(tiny_video, output_path)

    assert output_path.exists()
    assert read_ingest_target_lock(output_path) is None


def test_ingest_removes_target_lock_after_failure(tmp_path, tiny_video):
    output_path = tmp_path / "fail_then_clean.clulatent"
    # First ingest succeeds; second without --force fails during the
    # guarded block (output already exists) but must still release
    # its own target lock rather than leaving one behind.
    ingest_video(tiny_video, output_path)

    with pytest.raises(IngestError):
        ingest_video(tiny_video, output_path, force=False)

    assert read_ingest_target_lock(output_path) is None


def test_ingest_force_stale_lock_clears_dead_pid_lock(tmp_path, tiny_video):
    output_path = tmp_path / "stale_ingest.clulatent"
    lock_path = output_path.parent / f"{output_path.name}.oplock.json"
    dead_pid = 2**30
    lock_path.write_text(
        (
            '{"pid": %d, "hostname": "h", "operation": "ingest", '
            '"created_at": "2020-01-01T00:00:00+00:00", '
            '"package_path": "%s", "tool_version": "0.1.0"}\n'
        )
        % (dead_pid, str(output_path)),
        encoding="utf-8",
    )

    with pytest.raises(IngestError, match="stale"):
        ingest_video(tiny_video, output_path, force_stale_lock=False)

    result = ingest_video(tiny_video, output_path, force_stale_lock=True)

    assert result.package_path.exists()
    assert read_ingest_target_lock(output_path) is None


def test_cli_ingest_respects_target_lock(tmp_path, tiny_video):
    output_path = tmp_path / "cli_locked.clulatent"
    runner = CliRunner()

    with ingest_target_lock(output_path, operation="ingest"):
        result = runner.invoke(app, ["ingest", str(tiny_video), "-o", str(output_path)])

    assert result.exit_code == 1
    assert "Error" in result.output
    assert not output_path.exists()
