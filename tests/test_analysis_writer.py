"""Tests for Phase 2.7: the lock-aware `analysis_writer.py`.

ffmpeg-gated: every test here needs a real ingested package (writing
analysis lane events requires a real manifest.json to target), so the
whole module is skipped cleanly if ffmpeg/ffprobe are not available,
mirroring test_review_writer.py.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from clu_latent import analysis_writer as analysis_writer_mod
from clu_latent import lock as lock_mod
from clu_latent.analysis_lanes import AnalysisAdapterReceipt
from clu_latent.constants import ANALYSIS_RECEIPTS_FILE
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.security.operation_lock import operation_lock
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> "Path":  # noqa: F821
    directory = tmp_path_factory.mktemp("clulatent_analysis_writer_fixture")
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


def _scene_event(**overrides):
    event = {
        "id": "scn_000000",
        "type": "scene_boundary",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:pyscenedetect", "version": "0.6.0"},
        "payload": {"boundary_kind": "cut"},
    }
    event.update(overrides)
    return event


def _object_event(**overrides):
    event = {
        "id": "obj_000000",
        "type": "object_proposal",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:opencv", "version": "4.9.0"},
        "payload": {"label": "ball-like candidate"},
    }
    event.update(overrides)
    return event


def _link_event(**overrides):
    event = {
        "id": "lnk_000000",
        "type": "cross_lane_link",
        "t_start_ms": 0,
        "t_end_ms": 500,
        "producer": {"name": "adapter:linker", "version": "0.1.0"},
        "payload": {
            "source_event_ids": ["scn_000000"],
            "target_event_ids": ["obj_000000"],
            "relation_type": "co_occurs",
        },
    }
    event.update(overrides)
    return event


_WRITER_KWARGS = dict(adapter_name="test-adapter", tool_name="pyscenedetect", tool_version="0.6.0")


# --- append_analysis_events: happy paths -------------------------------------


def test_valid_event_writes_to_expected_track(valid_package):
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )
    assert result.track_file == "tracks/scene_events.jsonl"
    assert result.track_created is True
    assert result.events_written == 1
    assert result.event_ids == ["scn_000000"]

    events = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert len(events) == 1
    assert events[0].type == "scene_boundary"
    assert events[0].payload == {"boundary_kind": "cut"}


def test_multiple_events_write_safely(valid_package):
    events = [_scene_event(id=f"scn_{i:06d}", t_start_ms=i * 100, t_end_ms=i * 100 + 50) for i in range(5)]
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", events, **_WRITER_KWARGS
    )
    assert result.events_written == 5
    on_disk = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert {e.id for e in on_disk} == {e["id"] for e in events}


def test_second_call_appends_not_overwrites(valid_package):
    first = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event(id="scn_000000")], **_WRITER_KWARGS
    )
    second = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event(id="scn_000001", t_start_ms=600, t_end_ms=900)], **_WRITER_KWARGS
    )
    assert first.track_created is True
    assert second.track_created is False

    events = read_track_file(valid_package / "tracks/scene_events.jsonl")
    assert {e.id for e in events} == {"scn_000000", "scn_000001"}


def test_cross_lane_link_event_can_be_written(valid_package):
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "cross_lane_link_events", [_link_event()], **_WRITER_KWARGS
    )
    assert result.events_written == 1
    events = read_track_file(valid_package / "tracks/cross_lane_link_events.jsonl")
    assert events[0].payload["relation_type"] == "co_occurs"


def test_neutral_object_label_can_be_written(valid_package):
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "object_proposal_events", [_object_event()], **_WRITER_KWARGS
    )
    assert result.events_written == 1


def test_append_analysis_event_singular_wrapper(valid_package):
    result = analysis_writer_mod.append_analysis_event(
        valid_package, "scene_events", _scene_event(), **_WRITER_KWARGS
    )
    assert result.events_written == 1


# --- manifest / track bookkeeping --------------------------------------------


def test_manifest_track_descriptor_is_created_and_updated(valid_package):
    manifest_before = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "scene_events" for t in manifest_before.tracks)

    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )
    manifest_mid = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest_mid.tracks if t.name == "scene_events")
    assert track.file == "tracks/scene_events.jsonl"
    assert track.record_count == 1

    analysis_writer_mod.append_analysis_events(
        valid_package,
        "scene_events",
        [_scene_event(id="scn_000001", t_start_ms=600, t_end_ms=900)],
        **_WRITER_KWARGS,
    )
    manifest_after = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest_after.tracks if t.name == "scene_events")
    assert track.record_count == 2


def test_source_tracks_are_never_modified(valid_package):
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    source_track = manifest.tracks[0]
    before = (valid_package / source_track.file).read_bytes()

    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )

    after = (valid_package / source_track.file).read_bytes()
    assert before == after


def test_written_package_still_validates(valid_package):
    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )
    report = validate_package(valid_package)
    assert report.valid, report.errors


# --- receipts ------------------------------------------------------------


def test_receipt_is_written_by_default(valid_package):
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )
    assert result.receipt_path is not None
    assert (valid_package / ANALYSIS_RECEIPTS_FILE).exists()

    entries = [
        __import__("json").loads(line)
        for line in (valid_package / ANALYSIS_RECEIPTS_FILE).read_text().splitlines()
    ]
    assert len(entries) == 1
    assert entries[0]["adapter_name"] == "test-adapter"
    assert entries[0]["status"] == "success"
    assert entries[0]["event_counts"] == {"scene_events": 1}
    assert entries[0]["output_tracks"] == ["tracks/scene_events.jsonl"]


def test_receipt_can_be_disabled(valid_package):
    result = analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], write_receipt=False, **_WRITER_KWARGS
    )
    assert result.receipt_path is None
    assert not (valid_package / ANALYSIS_RECEIPTS_FILE).exists()


def test_receipts_accumulate_across_calls(valid_package):
    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event(id="scn_000000")], **_WRITER_KWARGS
    )
    analysis_writer_mod.append_analysis_events(
        valid_package,
        "scene_events",
        [_scene_event(id="scn_000001", t_start_ms=600, t_end_ms=900)],
        **_WRITER_KWARGS,
    )
    entries = (valid_package / ANALYSIS_RECEIPTS_FILE).read_text().splitlines()
    assert len(entries) == 2


def test_write_analysis_receipt_standalone_records_failure(valid_package):
    receipt = AnalysisAdapterReceipt(
        adapter_name="test-adapter",
        tool_name="pyscenedetect",
        tool_version="0.6.0",
        status="failure",
        failure_details="scene detector crashed",
    )
    path = analysis_writer_mod.write_analysis_receipt(valid_package, receipt)
    assert path == valid_package / ANALYSIS_RECEIPTS_FILE

    entries = [
        __import__("json").loads(line)
        for line in (valid_package / ANALYSIS_RECEIPTS_FILE).read_text().splitlines()
    ]
    assert entries[0]["status"] == "failure"
    assert entries[0]["failure_details"] == "scene detector crashed"


# --- refusal / error paths ----------------------------------------------


def test_unsupported_lane_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="not a supported analysis lane"):
        analysis_writer_mod.append_analysis_events(
            valid_package, "not_a_real_lane", [_scene_event()], **_WRITER_KWARGS
        )
    assert not (valid_package / ANALYSIS_RECEIPTS_FILE).exists()


def test_invalid_event_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package, "scene_events", [_scene_event(t_start_ms="not-an-int")], **_WRITER_KWARGS
        )
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_negative_timestamp_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package, "scene_events", [_scene_event(t_start_ms=-1)], **_WRITER_KWARGS
        )


def test_absolute_path_in_payload_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "scene_events",
            [_scene_event(payload={"path": "/etc/passwd"})],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


def test_parent_traversal_in_payload_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "scene_events",
            [_scene_event(payload={"path": "../../etc/passwd"})],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / "tracks/scene_events.jsonl").exists()


@pytest.mark.parametrize("identity_field", ["person_name", "identity", "face_identity", "biometric_identity"])
def test_identity_claim_in_object_lane_rejected(valid_package, identity_field):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="identity"):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "object_tracking_events",
            [_object_event(payload={"label": "person-like region", identity_field: "Jane Doe"})],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / "tracks/object_tracking_events.jsonl").exists()


def test_invalid_cross_lane_link_payload_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "cross_lane_link_events",
            [_link_event(payload={"relation_type": "causes", "source_event_ids": ["a"], "target_event_ids": ["b"]})],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / "tracks/cross_lane_link_events.jsonl").exists()


def test_duplicate_id_against_existing_track_rejected(valid_package):
    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event(id="scn_000000")], **_WRITER_KWARGS
    )
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="already exists"):
        analysis_writer_mod.append_analysis_events(
            valid_package, "scene_events", [_scene_event(id="scn_000000")], **_WRITER_KWARGS
        )


def test_unexpected_top_level_field_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="unexpected top-level field"):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "scene_events",
            [_scene_event(extra_field="surprise")],
            **_WRITER_KWARGS,
        )


def test_empty_events_list_rejected(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="non-empty"):
        analysis_writer_mod.append_analysis_events(valid_package, "scene_events", [], **_WRITER_KWARGS)


def test_nonexistent_package_gives_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="Package not found"):
        analysis_writer_mod.append_analysis_events(
            missing, "scene_events", [_scene_event()], **_WRITER_KWARGS
        )


def test_locked_package_refuses_write(valid_package):
    lock_mod.create_lock(valid_package)
    with pytest.raises(analysis_writer_mod.AnalysisWriteError, match="valid integrity lock"):
        analysis_writer_mod.append_analysis_events(
            valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
        )


def test_operation_lock_prevents_concurrent_write(valid_package):
    with operation_lock(valid_package, operation="external"):
        with pytest.raises(analysis_writer_mod.AnalysisWriteError):
            analysis_writer_mod.append_analysis_events(
                valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
            )
    # lock released cleanly afterwards -- a normal write now succeeds
    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )


def test_operation_lock_released_after_successful_write(valid_package):
    analysis_writer_mod.append_analysis_events(
        valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
    )
    assert not (valid_package / "lock" / "package.operation.lock.json").exists()


# --- atomicity / rollback -------------------------------------------------


def test_failed_validation_does_not_partially_write_track_manifest_or_receipt(valid_package):
    with pytest.raises(analysis_writer_mod.AnalysisWriteError):
        analysis_writer_mod.append_analysis_events(
            valid_package,
            "scene_events",
            [_scene_event(id="scn_000000"), _scene_event(id="scn_000000")],  # duplicate within batch
            **_WRITER_KWARGS,
        )
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "scene_events" for t in manifest.tracks)
    assert not (valid_package / "tracks/scene_events.jsonl").exists()
    assert not (valid_package / ANALYSIS_RECEIPTS_FILE).exists()


def test_rollback_removes_new_track_file_when_manifest_write_fails(valid_package, monkeypatch):
    real_atomic_write = analysis_writer_mod._atomic_write
    calls = {"n": 0}

    def flaky_atomic_write(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure on manifest write")
        return real_atomic_write(path, data)

    monkeypatch.setattr(analysis_writer_mod, "_atomic_write", flaky_atomic_write)

    with pytest.raises(OSError, match="simulated disk failure"):
        analysis_writer_mod.append_analysis_events(
            valid_package, "scene_events", [_scene_event()], **_WRITER_KWARGS
        )

    assert not (valid_package / "tracks/scene_events.jsonl").exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "scene_events" for t in manifest.tracks)
