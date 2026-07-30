"""Tests for Phase 2.2: the lock-aware `review_writer.py` and the
`clulatent review <subcommand>` CLI commands built on it.

ffmpeg-gated: every test here needs a real ingested package (writing
review events requires a real manifest.json + canonical tracks to
target), so the whole module is skipped cleanly if ffmpeg/ffprobe are
not available, mirroring test_review_events.py / test_review_resolver.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent import review_writer as review_writer_mod
from clu_latent.cli import app
from clu_latent.constants import REVIEW_EVENTS_TRACK_FILE, REVIEW_EVENTS_TRACK_NAME
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.review_resolver import resolve_package_review_states
from clu_latent.security.operation_lock import operation_lock
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    directory = tmp_path_factory.mktemp("clulatent_review_writer_fixture")
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


def _first_event_id(package_path) -> str:
    manifest = Manifest.from_json_file(package_path / "manifest.json")
    track = next(t for t in manifest.tracks if t.name != REVIEW_EVENTS_TRACK_NAME)
    events = read_track_file(package_path / track.file)
    return events[0].id


# --- append_review_event: happy paths ---------------------------------------


def test_approve_appends_review_approval_event(valid_package):
    target_id = _first_event_id(valid_package)
    result = review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    assert result.event_id.startswith("rv_approval_")
    assert result.review_track_created is True

    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert len(events) == 1
    assert events[0].type == "review_approval"
    assert events[0].payload["source_event_ids"] == [target_id]
    assert events[0].producer.name == "human:alice"


def test_reject_appends_review_rejection_event(valid_package):
    target_id = _first_event_id(valid_package)
    result = review_writer_mod.append_review_event(
        valid_package,
        "review_rejection",
        target_event_id=target_id,
        reviewer_id="alice",
        reason="wrong",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].id == result.event_id
    assert events[0].type == "review_rejection"
    assert events[0].payload["reason"] == "wrong"


def test_correct_appends_review_correction_event(valid_package):
    target_id = _first_event_id(valid_package)
    result = review_writer_mod.append_review_event(
        valid_package,
        "review_correction",
        target_event_id=target_id,
        reviewer_id="alice",
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].id == result.event_id
    assert events[0].payload["corrected_payload"] == {"text": "hello"}


def test_status_appends_review_status_event(valid_package):
    target_id = _first_event_id(valid_package)
    result = review_writer_mod.append_review_event(
        valid_package,
        "review_status",
        target_event_id=target_id,
        reviewer_id="alice",
        review_state="needs_review",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].id == result.event_id
    assert events[0].payload["review_state"] == "needs_review"


def test_note_appends_human_note_event_without_target(valid_package):
    result = review_writer_mod.append_review_event(
        valid_package,
        "human_note",
        target_event_id=None,
        reviewer_id="alice",
        note_text="general commentary",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].id == result.event_id
    assert events[0].payload["source_event_ids"] == []
    assert events[0].payload["note_text"] == "general commentary"


# --- track/manifest bookkeeping ----------------------------------------------


def test_track_created_if_missing(valid_package):
    manifest_before = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == REVIEW_EVENTS_TRACK_NAME for t in manifest_before.tracks)

    target_id = _first_event_id(valid_package)
    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )

    manifest_after = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest_after.tracks if t.name == REVIEW_EVENTS_TRACK_NAME)
    assert track.file == REVIEW_EVENTS_TRACK_FILE
    assert track.record_count == 1


def test_existing_review_track_is_appended_not_overwritten(valid_package):
    target_id = _first_event_id(valid_package)
    first = review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    second = review_writer_mod.append_review_event(
        valid_package,
        "human_note",
        target_event_id=target_id,
        reviewer_id="bob",
        note_text="second look",
        reviewed_at="2026-07-08T00:01:00Z",
    )
    assert first.event_id != second.event_id

    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    ids = {event.id for event in events}
    assert {first.event_id, second.event_id} == ids

    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest.tracks if t.name == REVIEW_EVENTS_TRACK_NAME)
    assert track.record_count == 2


def test_source_tracks_are_never_modified(valid_package):
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    source_track = next(t for t in manifest.tracks if t.name != REVIEW_EVENTS_TRACK_NAME)
    before = (valid_package / source_track.file).read_bytes()

    target_id = _first_event_id(valid_package)
    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )

    after = (valid_package / source_track.file).read_bytes()
    assert before == after


def test_written_package_still_validates(valid_package):
    target_id = _first_event_id(valid_package)
    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    report = validate_package(valid_package)
    assert report.valid, report.errors


def test_review_state_reflects_newly_written_review(valid_package):
    target_id = _first_event_id(valid_package)
    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    states, warnings = resolve_package_review_states(valid_package)
    assert warnings == []
    assert states[target_id].review_state == "approved"


# --- rollback: real failure injection ----------------------------------------


def test_rollback_removes_new_track_file_when_manifest_write_fails(valid_package, monkeypatch):
    """First-ever write: if the manifest write fails, the just-created
    review_events.jsonl must be removed, not left behind as an orphan
    that manifest.json does not declare."""
    target_id = _first_event_id(valid_package)
    real_atomic_write = review_writer_mod._atomic_write
    calls = {"n": 0}

    def flaky_atomic_write(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure on manifest write")
        return real_atomic_write(path, data)

    monkeypatch.setattr(review_writer_mod, "_atomic_write", flaky_atomic_write)

    with pytest.raises(OSError, match="simulated disk failure"):
        review_writer_mod.append_review_event(
            valid_package,
            "review_approval",
            target_event_id=target_id,
            reviewer_id="alice",
            reviewed_at="2026-07-08T00:00:00Z",
        )

    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == REVIEW_EVENTS_TRACK_NAME for t in manifest.tracks)


def test_rollback_restores_prior_track_contents_when_manifest_write_fails(valid_package, monkeypatch):
    """Second write onto an existing review track: if the manifest write
    fails, review_events.jsonl must be restored to exactly what it held
    before this attempt, not left holding the new (undeclared) event."""
    target_id = _first_event_id(valid_package)
    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    original_bytes = (valid_package / REVIEW_EVENTS_TRACK_FILE).read_bytes()

    real_atomic_write = review_writer_mod._atomic_write
    calls = {"n": 0}

    def flaky_atomic_write(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure on manifest write")
        return real_atomic_write(path, data)

    monkeypatch.setattr(review_writer_mod, "_atomic_write", flaky_atomic_write)

    with pytest.raises(OSError, match="simulated disk failure"):
        review_writer_mod.append_review_event(
            valid_package,
            "human_note",
            target_event_id=target_id,
            reviewer_id="bob",
            note_text="second look",
            reviewed_at="2026-07-08T00:01:00Z",
        )

    assert (valid_package / REVIEW_EVENTS_TRACK_FILE).read_bytes() == original_bytes
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert len(events) == 1
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest.tracks if t.name == REVIEW_EVENTS_TRACK_NAME)
    assert track.record_count == 1


# --- refusal / error paths ---------------------------------------------------


def test_nonexistent_package_raises_clean_error_not_traceback(tmp_path):
    """Regression: lock_status()/operation_lock resolve the package path
    with strict=True and raise a bare FileNotFoundError on a missing
    package; append_review_event must check existence first and convert
    this into a clean ReviewWriteError."""
    missing = tmp_path / "does_not_exist.clulatent"
    with pytest.raises(review_writer_mod.ReviewWriteError, match="Package not found"):
        review_writer_mod.append_review_event(
            missing,
            "review_approval",
            target_event_id="ts_000000",
            reviewer_id="alice",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_out_of_range_certainty_rejected_cleanly(valid_package):
    """Regression: confidence range is enforced by EventEnvelope itself
    (pydantic ValidationError), not review.py's ReviewEventError — both
    must surface as a clean ReviewWriteError with nothing written."""
    target_id = _first_event_id(valid_package)
    with pytest.raises(review_writer_mod.ReviewWriteError, match="confidence"):
        review_writer_mod.append_review_event(
            valid_package,
            "review_approval",
            target_event_id=target_id,
            reviewer_id="alice",
            reviewed_at="2026-07-08T00:00:00Z",
            confidence=5.0,
        )
    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


def test_locked_package_refuses_write(valid_package):
    lock_mod.create_lock(valid_package)
    target_id = _first_event_id(valid_package)
    with pytest.raises(review_writer_mod.ReviewWriteError, match="valid integrity lock"):
        review_writer_mod.append_review_event(
            valid_package,
            "review_approval",
            target_event_id=target_id,
            reviewer_id="alice",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_operation_lock_prevents_concurrent_write(valid_package):
    target_id = _first_event_id(valid_package)
    with operation_lock(valid_package, operation="review"):
        with pytest.raises(Exception):
            with operation_lock(valid_package, operation="review"):
                review_writer_mod.append_review_event(
                    valid_package,
                    "review_approval",
                    target_event_id=target_id,
                    reviewer_id="alice",
                    reviewed_at="2026-07-08T00:00:00Z",
                )


def test_invalid_event_id_rejected(valid_package):
    with pytest.raises(review_writer_mod.ReviewWriteError, match="not found"):
        review_writer_mod.append_review_event(
            valid_package,
            "review_approval",
            target_event_id="does_not_exist_000000",
            reviewer_id="alice",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_invalid_reviewer_id_rejected(valid_package):
    target_id = _first_event_id(valid_package)
    with pytest.raises(review_writer_mod.ReviewWriteError):
        review_writer_mod.append_review_event(
            valid_package,
            "review_approval",
            target_event_id=target_id,
            reviewer_id="bad\x00id",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_correction_missing_payload_rejected(valid_package):
    target_id = _first_event_id(valid_package)
    with pytest.raises(review_writer_mod.ReviewWriteError, match="original_payload"):
        review_writer_mod.append_review_event(
            valid_package,
            "review_correction",
            target_event_id=target_id,
            reviewer_id="alice",
            corrected_payload={"text": "hello"},
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_nothing_written_when_validation_fails(valid_package):
    target_id = _first_event_id(valid_package)
    with pytest.raises(review_writer_mod.ReviewWriteError):
        review_writer_mod.append_review_event(
            valid_package,
            "review_correction",
            target_event_id=target_id,
            reviewer_id="alice",
            original_payload={"a": 1},
            corrected_payload={"b": 2},  # different key set -> validation failure
            reviewed_at="2026-07-08T00:00:00Z",
        )
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == REVIEW_EVENTS_TRACK_NAME for t in manifest.tracks)
    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


# --- CLI ----------------------------------------------------------------------


def test_cli_review_approve(valid_package):
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(app, ["review", "approve", str(valid_package), target_id])
    assert result.exit_code == 0, result.output
    assert "Review event written" in result.output

    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].type == "review_approval"


def test_cli_review_correct_with_json_payloads(valid_package):
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "correct",
            str(valid_package),
            target_id,
            "--original-payload-json",
            json.dumps({"text": "helo"}),
            "--corrected-payload-json",
            json.dumps({"text": "hello"}),
        ],
    )
    assert result.exit_code == 0, result.output
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].type == "review_correction"


def test_cli_review_correct_rejects_malformed_json(valid_package):
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "correct",
            str(valid_package),
            target_id,
            "--original-payload-json",
            "not-json",
            "--corrected-payload-json",
            "{}",
        ],
    )
    assert result.exit_code == 1
    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


def test_cli_review_correct_rejects_non_object_json(valid_package):
    """Valid JSON that isn't an object (e.g. a bare array) must also be
    rejected before append_review_event is ever called."""
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "review",
            "correct",
            str(valid_package),
            target_id,
            "--original-payload-json",
            "[1, 2, 3]",
            "--corrected-payload-json",
            "{}",
        ],
    )
    assert result.exit_code == 1
    assert "must be a JSON object" in result.output
    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


def test_cli_review_approve_on_nonexistent_package_prints_clean_error(tmp_path):
    """Regression: previously crashed with a bare unhandled
    FileNotFoundError (exit code 1 but no message at all)."""
    runner = CliRunner()
    missing = tmp_path / "does_not_exist.clulatent"
    result = runner.invoke(app, ["review", "approve", str(missing), "ts_000000"])
    assert result.exit_code == 1
    assert "Package not found" in result.output


def test_cli_review_approve_rejects_out_of_range_certainty(valid_package):
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(
        app, ["review", "approve", str(valid_package), target_id, "--certainty", "5.0"]
    )
    assert result.exit_code == 1
    assert "confidence" in result.output
    assert not (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


def test_cli_review_note_general(valid_package):
    runner = CliRunner()
    result = runner.invoke(app, ["review", "note", str(valid_package), "--note", "hello"])
    assert result.exit_code == 0, result.output
    events = read_track_file(valid_package / REVIEW_EVENTS_TRACK_FILE)
    assert events[0].type == "human_note"
    assert events[0].payload["source_event_ids"] == []


def test_cli_review_on_locked_package_fails(valid_package):
    lock_mod.create_lock(valid_package)
    target_id = _first_event_id(valid_package)
    runner = CliRunner()
    result = runner.invoke(app, ["review", "approve", str(valid_package), target_id])
    assert result.exit_code == 1
    assert "integrity lock" in result.output
