"""Tests for Phase 2.0: review_events.jsonl factories and validation.

Two layers, mirroring the rest of the test suite's split:

  - ffmpeg-independent unit tests directly against `review.py`'s
    factories and `validate_review_track` (hand-built `EventEnvelope`
    lists, no real package on disk).
  - one ffmpeg-gated integration test proving `validate_package` picks
    up `review_events.jsonl` end-to-end for a real ingested package,
    plus a regression check that packages without a review track still
    validate exactly as before.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from clu_latent.constants import (
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    REVIEW_EVENTS_TRACK_FILE,
    REVIEW_EVENTS_TRACK_NAME,
)
from clu_latent.event import EventEnvelope, Producer
from clu_latent.ingest import ingest_video
from clu_latent.review import (
    ReviewEventError,
    make_human_note_event,
    make_review_approval_event,
    make_review_correction_event,
    make_review_override_event,
    make_review_rejection_event,
    make_review_session_summary_event,
    make_review_status_event,
    validate_review_track,
)
from clu_latent.security.limits import Limits
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Factories: happy paths --------------------------------------------------


def test_make_review_approval_event_happy_path():
    event = make_review_approval_event(
        index=0,
        t_start_ms=1000,
        t_end_ms=2000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    assert event.id == "rv_approval_000000"
    assert event.type == "review_approval"
    assert event.producer.name == "human:alice"
    assert event.payload["review_state"] == "approved"
    assert event.payload["source_event_ids"] == ["ts_000000"]


def test_make_review_rejection_event_happy_path():
    event = make_review_rejection_event(
        index=1,
        t_start_ms=1000,
        t_end_ms=2000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
        reason="wrong speaker",
    )
    assert event.id == "rv_rejection_000001"
    assert event.payload["review_state"] == "rejected"
    assert event.payload["reason"] == "wrong speaker"


def test_make_review_correction_event_happy_path():
    event = make_review_correction_event(
        index=2,
        t_start_ms=1000,
        t_end_ms=2000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    assert event.id == "rv_correction_000002"
    assert event.payload["review_state"] == "corrected"
    assert event.payload["original_payload"] == {"text": "helo"}
    assert event.payload["corrected_payload"] == {"text": "hello"}


def test_make_review_override_event_happy_path():
    event = make_review_override_event(
        index=3,
        t_start_ms=1000,
        t_end_ms=2000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        override_kind="reclassify",
        original_payload={"type": "speech_segment"},
        corrected_payload={"type": "silence"},
        review_state="corrected",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    assert event.id == "rv_override_000003"
    assert event.payload["override_kind"] == "reclassify"


def test_make_human_note_event_allows_empty_source_event_ids():
    event = make_human_note_event(
        index=4,
        t_start_ms=0,
        t_end_ms=0,
        reviewer_id="alice",
        note_text="general commentary about this clip",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    assert event.id == "rv_note_000004"
    assert event.payload["source_event_ids"] == []
    assert "review_state" not in event.payload


def test_make_review_session_summary_event_happy_path():
    event = make_review_session_summary_event(
        index=5,
        t_start_ms=0,
        t_end_ms=0,
        reviewer_id="alice",
        review_event_ids=["rv_approval_000000"],
        session_id="sess-1",
        started_at="2026-07-08T00:00:00Z",
        ended_at="2026-07-08T00:05:00Z",
        counts_by_review_state={"approved": 1},
    )
    assert event.id == "rv_session_000005"
    assert event.payload["counts_by_review_state"] == {"approved": 1}


def test_make_review_status_event_rejects_superseded_state():
    with pytest.raises(ReviewEventError):
        make_review_status_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            review_state="superseded",
            source_event_ids=["ts_000000"],
            reviewed_at="2026-07-08T00:00:00Z",
        )


# --- Factories: failure modes -------------------------------------------------


def test_make_review_approval_event_requires_source_event_ids():
    with pytest.raises(ReviewEventError):
        make_review_approval_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=[],
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_make_review_approval_event_rejects_self_reference():
    with pytest.raises(ReviewEventError):
        make_review_approval_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            supersedes_event_ids=["rv_approval_000000"],
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_make_review_correction_event_requires_matching_payload_keys():
    with pytest.raises(ReviewEventError):
        make_review_correction_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            original_payload={"text": "helo"},
            corrected_payload={"text": "hello", "language": "en"},
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_make_review_override_event_requires_corrected_state_unless_reclassify():
    with pytest.raises(ReviewEventError):
        make_review_override_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            override_kind="respan",
            original_payload={"t_start_ms": 0},
            corrected_payload={"t_start_ms": 500},
            review_state="rejected",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_make_human_note_event_requires_note_text():
    with pytest.raises(ReviewEventError):
        make_human_note_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            note_text="",
            reviewed_at="2026-07-08T00:00:00Z",
        )


def test_make_review_session_summary_event_rejects_self_reference():
    with pytest.raises(ReviewEventError):
        make_review_session_summary_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            review_event_ids=["rv_session_000000"],
            session_id="sess-1",
            started_at="2026-07-08T00:00:00Z",
            ended_at="2026-07-08T00:05:00Z",
            counts_by_review_state={},
        )


def test_factory_rejects_oversized_reason():
    tiny_limits = Limits(max_review_text_bytes=8)
    with pytest.raises(ReviewEventError):
        make_review_approval_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            reviewed_at="2026-07-08T00:00:00Z",
            reason="this reason is much too long for the tiny limit",
            limits=tiny_limits,
        )


def test_factory_rejects_oversized_note_text():
    tiny_limits = Limits(max_review_text_bytes=8)
    with pytest.raises(ReviewEventError):
        make_human_note_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            note_text="this note is much too long for the tiny limit",
            reviewed_at="2026-07-08T00:00:00Z",
            limits=tiny_limits,
        )


def test_factory_rejects_oversized_corrected_payload():
    tiny_limits = Limits(max_review_payload_bytes=8)
    with pytest.raises(ReviewEventError):
        make_review_correction_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            original_payload={"text": "helo"},
            corrected_payload={"text": "hello there this is way too long"},
            reviewed_at="2026-07-08T00:00:00Z",
            limits=tiny_limits,
        )


# --- validate_review_track: unit tests over hand-built events ---------------


def _envelope(event_id: str, event_type: str, payload: dict, *, producer_name: str = "human:alice"):
    return EventEnvelope(
        id=event_id,
        type=event_type,
        t_start_ms=0,
        t_end_ms=0,
        producer=Producer(name=producer_name, version="1.0"),
        confidence=None,
        payload=payload,
    )


def test_validate_review_track_accepts_valid_track():
    events = [
        make_review_approval_event(
            index=0,
            t_start_ms=0,
            t_end_ms=1000,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            reviewed_at="2026-07-08T00:00:00Z",
        ),
        make_human_note_event(
            index=1,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            note_text="looks fine overall",
            reviewed_at="2026-07-08T00:00:00Z",
        ),
    ]
    errors, warnings = validate_review_track(events)
    assert errors == []


def test_validate_review_track_rejects_unrecognized_type():
    events = [_envelope("rv_x_000000", "review_something_else", {"reviewer_id": "alice"})]
    errors, _ = validate_review_track(events)
    assert any("not a recognized review event type" in e for e in errors)


def test_validate_review_track_rejects_non_human_producer():
    events = [
        _envelope(
            "rv_note_000000",
            "human_note",
            {
                "reviewer_id": "alice",
                "note_text": "hi",
                "source_event_ids": [],
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
            producer_name="faster-whisper",
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("must start with" in e for e in errors)


def test_validate_review_track_rejects_non_list_source_event_ids():
    """A bare string is truthy, so a naive `not value` check would miss this."""
    events = [
        _envelope(
            "rv_approval_000000",
            "review_approval",
            {
                "reviewer_id": "alice",
                "source_event_ids": "ts_000000",
                "review_state": "approved",
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("source_event_ids" in e and "array" in e for e in errors)


def test_validate_review_track_rejects_non_string_elements_in_source_event_ids():
    events = [
        _envelope(
            "rv_approval_000000",
            "review_approval",
            {
                "reviewer_id": "alice",
                "source_event_ids": [123, {"nested": "dict"}],
                "review_state": "approved",
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("source_event_ids" in e for e in errors)


def test_validate_review_track_rejects_non_list_supersedes_event_ids():
    events = [
        _envelope(
            "rv_approval_000000",
            "review_approval",
            {
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "supersedes_event_ids": "rv_approval_999999",
                "review_state": "approved",
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("supersedes_event_ids" in e and "array" in e for e in errors)


def test_validate_review_track_rejects_oversized_producer_name_suffix():
    events = [
        _envelope(
            "rv_approval_000000",
            "review_approval",
            {
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "review_state": "approved",
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
            producer_name="human:" + ("a" * 5000),
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("producer.name" in e for e in errors)


def test_validate_review_track_rejects_unhashable_review_state_without_crashing():
    """A list/dict review_state must not raise TypeError from `in <frozenset>`."""
    events = [
        _envelope(
            "rv_status_000000",
            "review_status",
            {
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "review_state": ["not", "a", "string"],
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("review_state" in e for e in errors)


def test_validate_review_track_rejects_missing_override_kind():
    events = [
        _envelope(
            "rv_override_000000",
            "review_override",
            {
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "review_state": "corrected",
                "original_payload": {"text": "helo"},
                "corrected_payload": {"text": "hello"},
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("override_kind" in e for e in errors)


def test_validate_review_track_rejects_missing_session_id():
    events = [
        _envelope(
            "rv_session_000000",
            "review_session_summary",
            {
                "reviewer_id": "alice",
                "review_event_ids": ["rv_approval_000000"],
                "started_at": "2026-07-08T00:00:00Z",
                "ended_at": "2026-07-08T00:05:00Z",
                "counts_by_review_state": {},
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("session_id" in e for e in errors)


def test_validate_review_track_rejects_non_dict_counts_by_review_state():
    approval = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    events = [
        approval,
        _envelope(
            "rv_session_000001",
            "review_session_summary",
            {
                "reviewer_id": "alice",
                "review_event_ids": [approval.id],
                "session_id": "sess-1",
                "started_at": "2026-07-08T00:00:00Z",
                "ended_at": "2026-07-08T00:05:00Z",
                "counts_by_review_state": "not-a-dict",
            },
        ),
    ]
    errors, _ = validate_review_track(events)
    assert any("counts_by_review_state" in e and "object" in e for e in errors)


def test_validate_review_track_rejects_dangling_supersedes_reference():
    events = [
        make_review_correction_event(
            index=0,
            t_start_ms=0,
            t_end_ms=1000,
            reviewer_id="alice",
            source_event_ids=["ts_000000"],
            original_payload={"text": "helo"},
            corrected_payload={"text": "hello"},
            reviewed_at="2026-07-08T00:00:00Z",
            supersedes_event_ids=["rv_correction_999999"],
        ),
    ]
    errors, _ = validate_review_track(events)
    assert any("does not exist in review_events.jsonl" in e for e in errors)


def test_validate_review_track_rejects_supersedes_cycle():
    a = make_review_correction_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    b = make_review_correction_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello world"},
        reviewed_at="2026-07-08T00:00:00Z",
        supersedes_event_ids=[a.id],
    )
    # Mutate `a` (post-construction) to point back at `b`, forming a cycle.
    a = EventEnvelope(
        id=a.id,
        type=a.type,
        t_start_ms=a.t_start_ms,
        t_end_ms=a.t_end_ms,
        producer=a.producer,
        confidence=a.confidence,
        payload={**a.payload, "supersedes_event_ids": [b.id]},
    )
    errors, _ = validate_review_track([a, b])
    assert any("cycle" in e for e in errors)


def test_validate_review_track_warns_on_empty_source_event_ids_human_note():
    events = [
        make_human_note_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            note_text="general note",
            reviewed_at="2026-07-08T00:00:00Z",
        )
    ]
    errors, warnings = validate_review_track(events)
    assert errors == []
    assert any("empty source_event_ids" in w for w in warnings)


def test_validate_review_track_rejects_session_summary_count_mismatch():
    approval = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    summary = make_review_session_summary_event(
        index=1,
        t_start_ms=0,
        t_end_ms=0,
        reviewer_id="alice",
        review_event_ids=[approval.id],
        session_id="sess-1",
        started_at="2026-07-08T00:00:00Z",
        ended_at="2026-07-08T00:05:00Z",
        counts_by_review_state={"rejected": 1},  # wrong on purpose
    )
    errors, _ = validate_review_track([approval, summary])
    assert any("counts_by_review_state" in e for e in errors)


def test_validate_review_track_rejects_missing_review_state_on_status():
    events = [
        _envelope(
            "rv_status_000000",
            "review_status",
            {
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
    ]
    errors, _ = validate_review_track(events)
    assert any("requires review_state" in e for e in errors)


# --- validate_package: end-to-end integration --------------------------------


@pytest.fixture(scope="module")
def tiny_video_for_review(tmp_path_factory):
    directory = tmp_path_factory.mktemp("review_events_fixture")
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


@pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment")
class TestValidatePackageWithReviewTrack:
    @pytest.fixture
    def valid_package(self, tmp_path, tiny_video_for_review):
        output_path = tmp_path / "pkg.clulatent"
        result = ingest_video(tiny_video_for_review, output_path)
        return result.package_path

    def _load_manifest_dict(self, package_path) -> dict:
        return json.loads((package_path / "manifest.json").read_text(encoding="utf-8"))

    def _write_manifest_dict(self, package_path, manifest_dict: dict) -> None:
        (package_path / "manifest.json").write_text(
            json.dumps(manifest_dict, indent=2), encoding="utf-8"
        )

    def _add_review_track(self, package_path, events: list[EventEnvelope]) -> None:
        review_path = package_path / REVIEW_EVENTS_TRACK_FILE
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with review_path.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(event.model_dump_json() + "\n")

        manifest_dict = self._load_manifest_dict(package_path)
        manifest_dict["tracks"].append(
            {
                "name": REVIEW_EVENTS_TRACK_NAME,
                "file": REVIEW_EVENTS_TRACK_FILE,
                "schema_id": EVENT_ENVELOPE_SCHEMA_ID,
                "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
                "record_count": len(events),
                "sorted_by": "t_start_ms",
            }
        )
        self._write_manifest_dict(package_path, manifest_dict)

    def test_validate_package_accepts_valid_review_track(self, valid_package):
        event = make_human_note_event(
            index=0,
            t_start_ms=0,
            t_end_ms=0,
            reviewer_id="alice",
            note_text="looks good",
            reviewed_at="2026-07-08T00:00:00Z",
        )
        self._add_review_track(valid_package, [event])

        report = validate_package(valid_package)
        assert report.valid is True

    def test_validate_package_reports_invalid_review_state(self, valid_package):
        bad_event = EventEnvelope(
            id="rv_status_000000",
            type="review_status",
            t_start_ms=0,
            t_end_ms=1000,
            producer=Producer(name="human:alice", version="1.0"),
            confidence=None,
            payload={
                "reviewer_id": "alice",
                "source_event_ids": ["ts_000000"],
                "review_state": "not_a_real_state",
                "reviewed_at": "2026-07-08T00:00:00Z",
            },
        )
        self._add_review_track(valid_package, [bad_event])

        report = validate_package(valid_package)
        assert report.valid is False
        assert any("not a directly-assertable" in e for e in report.errors)

    def test_validate_package_without_review_track_still_validates(self, valid_package):
        report = validate_package(valid_package)
        assert report.valid is True
        assert not any(t.name == REVIEW_EVENTS_TRACK_NAME for t in report.manifest.tracks)
