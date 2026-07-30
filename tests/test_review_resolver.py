"""Tests for Phase 2.1: the read-only reviewed-state resolver.

Two layers, mirroring test_review_events.py's split:

  - ffmpeg-independent unit tests directly against
    `review_resolver.resolve_review_states` (hand-built `EventEnvelope`
    lists, no real package on disk).
  - ffmpeg-gated integration tests proving `resolve_package_review_states`
    and the `clulatent review-state` CLI command work end-to-end against
    a real ingested package, with and without a review_events track.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from clu_latent.cli import app
from clu_latent.constants import (
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    REVIEW_EVENTS_TRACK_FILE,
    REVIEW_EVENTS_TRACK_NAME,
)
from clu_latent.event import EventEnvelope, Producer
from clu_latent.ingest import ingest_video
from clu_latent.review import (
    make_human_note_event,
    make_review_approval_event,
    make_review_correction_event,
    make_review_override_event,
    make_review_rejection_event,
    make_review_session_summary_event,
    make_review_status_event,
)
from clu_latent.review_resolver import (
    resolve_package_review_states,
    resolve_review_state_for_event,
    resolve_review_states,
    summarize_review_states,
)

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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


# --- resolve_review_states: core behavior ------------------------------------


def test_no_review_events_resolves_everything_unreviewed():
    states, warnings = resolve_review_states(["ts_000000", "ts_000001"], [])
    assert warnings == []
    assert states["ts_000000"].review_state == "unreviewed"
    assert states["ts_000000"].is_reviewed is False
    assert states["ts_000001"].review_state == "unreviewed"


def test_approval_resolves_approved():
    event = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    state, warnings = resolve_review_state_for_event("ts_000000", [event])
    assert warnings == []
    assert state.review_state == "approved"
    assert state.is_approved is True
    assert state.is_reviewed is True
    assert state.latest_review_event_id == event.id
    assert state.review_event_ids == [event.id]
    assert state.reviewer_id == "alice"


def test_rejection_resolves_rejected():
    event = make_review_rejection_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
        reason="wrong speaker",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [event])
    assert state.review_state == "rejected"
    assert state.is_rejected is True
    assert state.reason == "wrong speaker"


def test_correction_resolves_corrected_and_exposes_corrected_payload():
    event = make_review_correction_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [event])
    assert state.review_state == "corrected"
    assert state.is_corrected is True
    assert state.corrected_payload == {"text": "hello"}


def test_override_resolves_corrected():
    event = make_review_override_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        override_kind="reclassify",
        original_payload={"type": "speech_segment"},
        corrected_payload={"type": "silence"},
        review_state="corrected",
        reviewed_at="2026-07-08T00:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [event])
    assert state.review_state == "corrected"
    assert state.is_corrected is True
    assert state.corrected_payload == {"type": "silence"}


def test_review_status_can_set_needs_review():
    event = make_review_status_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        review_state="needs_review",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [event])
    assert state.review_state == "needs_review"
    assert state.is_reviewed is True
    assert state.is_approved is False


def test_human_note_does_not_change_state():
    note = make_human_note_event(
        index=0,
        t_start_ms=0,
        t_end_ms=0,
        reviewer_id="alice",
        note_text="general commentary",
        reviewed_at="2026-07-08T00:00:00Z",
        source_event_ids=["ts_000000"],
    )
    state, warnings = resolve_review_state_for_event("ts_000000", [note])
    assert warnings == []
    assert state.review_state == "unreviewed"
    assert state.review_event_ids == []


def test_review_session_summary_does_not_change_state():
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
        counts_by_review_state={"approved": 1},
    )
    state, _ = resolve_review_state_for_event("ts_000000", [approval, summary])
    assert state.review_state == "approved"
    assert state.review_event_ids == [approval.id]


# --- ordering ------------------------------------------------------------


def test_latest_reviewed_at_wins_regardless_of_list_order():
    later = make_review_rejection_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T12:00:00Z",
    )
    earlier = make_review_approval_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    # `later` (rejection) appears first in the list but has the later
    # reviewed_at — it should still win.
    state, _ = resolve_review_state_for_event("ts_000000", [later, earlier])
    assert state.review_state == "rejected"
    assert state.latest_review_event_id == later.id


def test_track_order_fallback_when_reviewed_at_unparseable():
    first = _envelope(
        "rv_approval_000000",
        "review_approval",
        {
            "reviewer_id": "alice",
            "source_event_ids": ["ts_000000"],
            "review_state": "approved",
            "reviewed_at": "not-a-real-timestamp",
        },
    )
    second = _envelope(
        "rv_rejection_000001",
        "review_rejection",
        {
            "reviewer_id": "alice",
            "source_event_ids": ["ts_000000"],
            "review_state": "rejected",
            "reviewed_at": "also-not-a-timestamp",
        },
    )
    state, _ = resolve_review_state_for_event("ts_000000", [first, second])
    # Neither reviewed_at parses, so track (list) order decides: second wins.
    assert state.review_state == "rejected"
    assert state.latest_review_event_id == second.id


# --- conflicts -------------------------------------------------------------


def test_approved_then_rejected_sets_conflict():
    approval = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    rejection = make_review_rejection_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T01:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [approval, rejection])
    assert state.review_state == "rejected"
    assert state.has_conflict is True


def test_corrected_then_approved_without_superseding_sets_conflict():
    correction = make_review_correction_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    approval = make_review_approval_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T01:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [correction, approval])
    assert state.review_state == "approved"
    assert state.has_conflict is True


def test_correction_superseding_prior_correction_avoids_conflict():
    first = make_review_correction_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    second = make_review_correction_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello there"},
        reviewed_at="2026-07-08T01:00:00Z",
        supersedes_event_ids=[first.id],
    )
    state, _ = resolve_review_state_for_event("ts_000000", [first, second])
    assert state.review_state == "corrected"
    assert state.corrected_payload == {"text": "hello there"}
    assert state.has_conflict is False
    assert state.is_superseded is True


def test_multiple_unlinked_corrections_set_conflict_even_with_same_state():
    first = make_review_correction_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "hello"},
        reviewed_at="2026-07-08T00:00:00Z",
    )
    second = make_review_correction_event(
        index=1,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="bob",
        source_event_ids=["ts_000000"],
        original_payload={"text": "helo"},
        corrected_payload={"text": "totally different"},
        reviewed_at="2026-07-08T01:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [first, second])
    # Both resolve to "corrected" (same state), but neither supersedes the
    # other, so this is still a real conflict (two different corrected
    # payloads asserted independently).
    assert state.review_state == "corrected"
    assert state.has_conflict is True


def test_no_supersession_means_is_superseded_false():
    event = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    state, _ = resolve_review_state_for_event("ts_000000", [event])
    assert state.is_superseded is False


# --- defensiveness -----------------------------------------------------------


def test_dangling_source_event_id_reference_is_ignored_with_warning():
    event = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_999999"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    states, warnings = resolve_review_states(["ts_000000"], [event])
    assert states["ts_000000"].review_state == "unreviewed"
    assert any("ts_999999" in w for w in warnings)


def test_malformed_review_payloads_do_not_crash_resolver():
    non_list_source_ids = _envelope(
        "rv_approval_000000",
        "review_approval",
        {
            "reviewer_id": "alice",
            "source_event_ids": "ts_000000",  # should be a list
            "review_state": "approved",
            "reviewed_at": "2026-07-08T00:00:00Z",
        },
    )
    unhashable_review_state = _envelope(
        "rv_status_000001",
        "review_status",
        {
            "reviewer_id": "alice",
            "source_event_ids": ["ts_000000"],
            "review_state": ["not", "a", "string"],
            "reviewed_at": "2026-07-08T00:00:00Z",
        },
    )
    override_missing_state = _envelope(
        "rv_override_000002",
        "review_override",
        {
            "reviewer_id": "alice",
            "source_event_ids": ["ts_000000"],
            "corrected_payload": {"text": "hello"},
            "reviewed_at": "2026-07-08T00:00:00Z",
        },
    )
    valid = make_review_approval_event(
        index=3,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T02:00:00Z",
    )

    states, warnings = resolve_review_states(
        ["ts_000000"],
        [non_list_source_ids, unhashable_review_state, override_missing_state, valid],
    )
    # None of the malformed records crash resolution, and the one
    # well-formed record still resolves correctly.
    assert states["ts_000000"].review_state == "approved"
    assert states["ts_000000"].latest_review_event_id == valid.id
    assert len(warnings) >= 2


def test_summarize_review_states_counts():
    approved = make_review_approval_event(
        index=0,
        t_start_ms=0,
        t_end_ms=1000,
        reviewer_id="alice",
        source_event_ids=["ts_000000"],
        reviewed_at="2026-07-08T00:00:00Z",
    )
    states, _ = resolve_review_states(["ts_000000", "ts_000001"], [approved])
    summary = summarize_review_states(states)
    assert summary["total"] == 2
    assert summary["approved"] == 1
    assert summary["unreviewed"] == 1
    assert summary["conflicts"] == 0


# --- integration: real package -----------------------------------------------


@pytest.fixture(scope="module")
def tiny_video_for_resolver(tmp_path_factory):
    directory = tmp_path_factory.mktemp("review_resolver_fixture")
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
class TestResolvePackageReviewStates:
    @pytest.fixture
    def valid_package(self, tmp_path, tiny_video_for_resolver):
        output_path = tmp_path / "pkg.clulatent"
        result = ingest_video(tiny_video_for_resolver, output_path)
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

    def test_package_without_review_track_resolves_all_unreviewed(self, valid_package):
        states, warnings = resolve_package_review_states(valid_package)
        assert warnings == []
        assert states
        assert all(state.review_state == "unreviewed" for state in states.values())

    def test_package_with_review_track_resolves_approval(self, valid_package):
        first_id = next(iter(resolve_package_review_states(valid_package)[0]))
        approval = make_review_approval_event(
            index=0,
            t_start_ms=0,
            t_end_ms=1000,
            reviewer_id="alice",
            source_event_ids=[first_id],
            reviewed_at="2026-07-08T00:00:00Z",
        )
        self._add_review_track(valid_package, [approval])

        states, warnings = resolve_package_review_states(valid_package)
        assert warnings == []
        assert states[first_id].review_state == "approved"

    def test_cli_review_state_command(self, valid_package):
        runner = CliRunner()
        result = runner.invoke(app, ["review-state", str(valid_package)])
        assert result.exit_code == 0
        assert "total source events" in result.output
        assert "conflicts" in result.output
