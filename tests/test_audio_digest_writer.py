"""Tests for Phase 3.5: the lock-aware `audio_digest_writer.py`.

ffmpeg-gated: every test here needs a real ingested package (writing
audio digest events requires a real manifest.json to target), so the
whole module is skipped cleanly if ffmpeg/ffprobe are not available,
mirroring test_analysis_writer.py / test_review_writer.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clu_latent import audio_digest_writer as writer_mod
from clu_latent import lock as lock_mod
from clu_latent.constants import AUDIO_DIGEST_RECEIPTS_FILE, AUDIO_DIGEST_TRACK_FILE
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.security.operation_lock import operation_lock
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_5_AUDIO_DIGEST_WRITER_RECEIPTS.md"


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_audio_digest_writer_fixture")
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


def _producer() -> dict:
    return {"name": "test-producer", "version": "0.0.1"}


def _feature_series(**overrides) -> dict:
    event = {
        "id": "series_000001",
        "type": "audio_feature_series",
        "t_start_ms": 0,
        "t_end_ms": 60000,
        "producer": _producer(),
        "confidence": 0.9,
        "payload": {
            "feature": "loudness_rms",
            "window_ms": 20,
            "hop_ms": 20,
            "units": "dbfs",
            "data_path": "media/audio_features/loudness_rms.jsonl",
            "summary": {"min": -40.0, "max": -6.0, "mean": -18.5, "count": 3000},
        },
    }
    event.update(overrides)
    return event


def _digest_segment(**overrides) -> dict:
    event = {
        "id": "seg_000001",
        "type": "audio_digest_segment",
        "t_start_ms": 12000,
        "t_end_ms": 18500,
        "producer": _producer(),
        "confidence": 0.8,
        "payload": {
            "label": "tension-like buildup candidate",
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "linked_event_ids": ["energy_000012", "rhythm_000006"],
            "linked_feature_series_ids": ["series_000001"],
            "salience": 0.81,
            "recommended_for_llm_context": True,
            "caveats": ["This is evidence, not truth; it does not establish intent."],
        },
    }
    event.update(overrides)
    return event


def _context_packet(**overrides) -> dict:
    event = {
        "id": "packet_000001",
        "type": "audio_llm_context_packet",
        "t_start_ms": 0,
        "t_end_ms": 120000,
        "producer": _producer(),
        "confidence": 0.75,
        "payload": {
            "time_range": {"t_start_ms": 0, "t_end_ms": 120000},
            "budget_tokens_estimate": 500,
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "top_evidence": ["compression/limiting evidence in segment seg_000001"],
            "warnings": ["Some lower-salience segments were omitted."],
            "caveats": [
                "This packet describes evidence, not truth, and does not establish "
                "intent, meaning, or a listener's emotional response."
            ],
            "linked_event_ids": ["energy_000012"],
            "linked_digest_segment_ids": ["seg_000001"],
            "linked_feature_series_ids": ["series_000001"],
            "omitted_detail_reason": "Lower-salience segments omitted to stay within budget.",
            "retrieval_hints": {
                "by_time_range": "retrieve tracks/audio_digest_segment.jsonl records overlapping the range",
                "by_evidence_id": "retrieve any linked id by exact match",
            },
        },
    }
    event.update(overrides)
    return event


_WRITER_KWARGS = dict(tool_name="test-digest-writer", tool_version="0.0.1")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- append_audio_digest_events: happy paths ---------------------------------


def test_module_imports_cleanly():
    assert writer_mod is not None


def test_build_audio_digest_track_path_is_package_relative(valid_package):
    path = writer_mod.build_audio_digest_track_path(valid_package)
    assert path == valid_package / AUDIO_DIGEST_TRACK_FILE


def test_append_valid_feature_series_writes_track_and_receipt(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package, [_feature_series()], **_WRITER_KWARGS
    )
    assert result.track_file == AUDIO_DIGEST_TRACK_FILE
    assert result.track_created is True
    assert result.events_written == 1
    assert result.event_ids == ["series_000001"]
    assert result.receipt_path is not None

    events = read_track_file(valid_package / AUDIO_DIGEST_TRACK_FILE)
    assert len(events) == 1
    assert events[0].type == "audio_feature_series"


def test_append_valid_digest_segment_writes_track_and_receipt(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package, [_digest_segment()], **_WRITER_KWARGS
    )
    assert result.events_written == 1
    events = read_track_file(valid_package / AUDIO_DIGEST_TRACK_FILE)
    assert events[0].type == "audio_digest_segment"


def test_append_valid_context_packet_writes_track_and_receipt(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package, [_context_packet()], **_WRITER_KWARGS
    )
    assert result.events_written == 1
    events = read_track_file(valid_package / AUDIO_DIGEST_TRACK_FILE)
    assert events[0].type == "audio_llm_context_packet"


def test_batch_write_writes_all_events(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series(), _digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )
    assert result.events_written == 3
    assert result.record_type_counts == {
        "audio_feature_series": 1,
        "audio_digest_segment": 1,
        "audio_llm_context_packet": 1,
    }
    events = read_track_file(valid_package / AUDIO_DIGEST_TRACK_FILE)
    assert len(events) == 3


def test_second_call_appends_not_overwrites(valid_package):
    first = writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(id="series_000001")], **_WRITER_KWARGS
    )
    second = writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(id="series_000002")], **_WRITER_KWARGS
    )
    assert first.track_created is True
    assert second.track_created is False
    events = read_track_file(valid_package / AUDIO_DIGEST_TRACK_FILE)
    assert {e.id for e in events} == {"series_000001", "series_000002"}


def test_append_audio_digest_event_singular_wrapper(valid_package):
    result = writer_mod.append_audio_digest_event(
        valid_package, _feature_series(), **_WRITER_KWARGS
    )
    assert result.events_written == 1


def test_written_package_still_validates(valid_package):
    # tiny_video is only ~2000ms long; use an in-bounds event so this test
    # exercises full-package validation rather than the unrelated
    # source-duration tolerance check exercised elsewhere.
    event = _feature_series(t_start_ms=0, t_end_ms=2000)
    writer_mod.append_audio_digest_events(valid_package, [event], **_WRITER_KWARGS)
    report = validate_package(valid_package)
    assert report.valid, report.errors


# --- manifest / track bookkeeping --------------------------------------------


def test_manifest_includes_audio_digest_track_descriptor(valid_package):
    manifest_before = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "audio_digest_events" for t in manifest_before.tracks)

    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest_mid = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest_mid.tracks if t.name == "audio_digest_events")
    assert track.file == AUDIO_DIGEST_TRACK_FILE
    assert track.record_count == 1

    writer_mod.append_audio_digest_events(
        valid_package, [_digest_segment()], **_WRITER_KWARGS
    )
    manifest_after = Manifest.from_json_file(valid_package / "manifest.json")
    track = next(t for t in manifest_after.tracks if t.name == "audio_digest_events")
    assert track.record_count == 2


def test_writer_does_not_touch_source_tracks(valid_package):
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    source_track = manifest.tracks[0]
    before = (valid_package / source_track.file).read_bytes()

    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    after = (valid_package / source_track.file).read_bytes()
    assert before == after


def test_writer_does_not_touch_analysis_tracks(valid_package):
    scene_track_path = valid_package / "tracks" / "scene_events.jsonl"
    assert not scene_track_path.exists()

    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    assert not scene_track_path.exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "scene_events" for t in manifest.tracks)


def test_writer_does_not_touch_index(valid_package):
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    index_path = valid_package / manifest.index.file
    before = index_path.read_bytes()

    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    after = index_path.read_bytes()
    assert before == after


# --- receipts ------------------------------------------------------------


def test_receipt_is_written_by_default(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package, [_feature_series()], **_WRITER_KWARGS
    )
    assert result.receipt_path is not None
    assert (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()

    entries = _read_jsonl(valid_package / AUDIO_DIGEST_RECEIPTS_FILE)
    assert len(entries) == 1
    assert entries[0]["operation"] == "append_audio_digest_events"
    assert entries[0]["status"] == "success"
    assert entries[0]["event_count"] == 1
    assert entries[0]["record_type_counts"] == {"audio_feature_series": 1}
    assert entries[0]["output_track"] == AUDIO_DIGEST_TRACK_FILE
    assert "evidence" in entries[0]["evidence_not_truth_reminder"].lower()
    assert "not truth" in entries[0]["evidence_not_truth_reminder"].lower()


def test_receipt_can_be_disabled(valid_package):
    result = writer_mod.append_audio_digest_events(
        valid_package, [_feature_series()], write_receipt=False, **_WRITER_KWARGS
    )
    assert result.receipt_path is None
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()


def test_receipts_accumulate_across_calls(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(id="series_000001")], **_WRITER_KWARGS
    )
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(id="series_000002")], **_WRITER_KWARGS
    )
    entries = _read_jsonl(valid_package / AUDIO_DIGEST_RECEIPTS_FILE)
    assert len(entries) == 2


def test_write_audio_digest_receipt_standalone_records_failure(valid_package):
    receipt = writer_mod.AudioDigestReceipt(
        operation="append_audio_digest_events",
        status="failure",
        tool_name="test-digest-writer",
        tool_version="0.0.1",
        output_track=AUDIO_DIGEST_TRACK_FILE,
        failure_details="upstream digest builder crashed",
    )
    path = writer_mod.write_audio_digest_receipt(valid_package, receipt)
    assert path == valid_package / AUDIO_DIGEST_RECEIPTS_FILE

    entries = _read_jsonl(valid_package / AUDIO_DIGEST_RECEIPTS_FILE)
    assert entries[0]["status"] == "failure"
    assert entries[0]["failure_details"] == "upstream digest builder crashed"


def test_linked_evidence_recorded_on_receipt(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series()],
        linked_evidence={"source_lane_events": ["energy_000012"]},
        **_WRITER_KWARGS,
    )
    entries = _read_jsonl(valid_package / AUDIO_DIGEST_RECEIPTS_FILE)
    assert entries[0]["linked_evidence"] == {"source_lane_events": ["energy_000012"]}


# --- refusal / error paths ----------------------------------------------


def test_empty_events_list_rejected(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError, match="non-empty"):
        writer_mod.append_audio_digest_events(valid_package, [], **_WRITER_KWARGS)


def test_invalid_event_rejected(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series(t_start_ms="not-an-int")], **_WRITER_KWARGS
        )
    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()


def test_unsupported_event_type_rejected(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series(type="not_a_real_digest_type")], **_WRITER_KWARGS
        )
    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()


def test_duplicate_id_against_existing_track_rejected(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(id="series_000001")], **_WRITER_KWARGS
    )
    with pytest.raises(writer_mod.AudioDigestWriteError, match="already exists"):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series(id="series_000001")], **_WRITER_KWARGS
        )


def test_duplicate_id_within_batch_rejected(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError):
        writer_mod.append_audio_digest_events(
            valid_package,
            [_feature_series(id="dup_000001"), _digest_segment(id="dup_000001")],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()


def test_nonexistent_package_gives_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    with pytest.raises(writer_mod.AudioDigestWriteError, match="Package not found"):
        writer_mod.append_audio_digest_events(missing, [_feature_series()], **_WRITER_KWARGS)


def test_non_directory_package_root_gives_clean_error(tmp_path):
    not_a_dir = tmp_path / "not_a_dir.clulatent"
    not_a_dir.write_text("not a package")
    with pytest.raises(writer_mod.AudioDigestWriteError, match="Package not found"):
        writer_mod.append_audio_digest_events(not_a_dir, [_feature_series()], **_WRITER_KWARGS)


def test_unsafe_data_path_rejected(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError):
        writer_mod.append_audio_digest_events(
            valid_package,
            [_feature_series(payload={**_feature_series()["payload"], "data_path": "/etc/passwd"})],
            **_WRITER_KWARGS,
        )
    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()


def test_locked_package_refuses_write(valid_package):
    lock_mod.create_lock(valid_package)
    with pytest.raises(writer_mod.AudioDigestWriteError, match="valid integrity lock"):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series()], **_WRITER_KWARGS
        )


def test_operation_lock_prevents_concurrent_write(valid_package):
    with operation_lock(valid_package, operation="external"):
        with pytest.raises(writer_mod.AudioDigestWriteError):
            writer_mod.append_audio_digest_events(
                valid_package, [_feature_series()], **_WRITER_KWARGS
            )
    # lock released cleanly afterwards -- a normal write now succeeds
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)


# --- atomicity / rollback -------------------------------------------------


def test_failed_validation_does_not_partially_write_track_manifest_or_receipt(valid_package):
    with pytest.raises(writer_mod.AudioDigestWriteError):
        writer_mod.append_audio_digest_events(
            valid_package,
            [_feature_series(id="series_000001"), _feature_series(id="series_000001")],
            **_WRITER_KWARGS,
        )
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "audio_digest_events" for t in manifest.tracks)
    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()


def test_rollback_removes_new_track_file_when_manifest_write_fails(valid_package, monkeypatch):
    real_atomic_write = writer_mod._atomic_write
    calls = {"n": 0}

    def flaky_atomic_write(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated disk failure on manifest write")
        return real_atomic_write(path, data)

    monkeypatch.setattr(writer_mod, "_atomic_write", flaky_atomic_write)

    with pytest.raises(OSError, match="simulated disk failure"):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series()], **_WRITER_KWARGS
        )

    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "audio_digest_events" for t in manifest.tracks)


def test_rollback_restores_manifest_and_track_when_receipt_write_fails(valid_package, monkeypatch):
    def failing_check_receipt_bounds(receipt, limits):
        return ["simulated receipt failure"]

    monkeypatch.setattr(writer_mod, "_check_receipt_bounds", failing_check_receipt_bounds)

    with pytest.raises(writer_mod.AudioDigestWriteError, match="simulated receipt failure"):
        writer_mod.append_audio_digest_events(
            valid_package, [_feature_series()], **_WRITER_KWARGS
        )

    assert not (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "audio_digest_events" for t in manifest.tracks)


# --- docs / README ---------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_states_core_rule():
    text = " ".join(PHASE_DOC.read_text(encoding="utf-8").split()).lower()
    assert "store deep" in text
    assert "show shallow" in text
    assert "retrieve detail only when needed" in text


def test_readme_includes_phase_3_5_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
