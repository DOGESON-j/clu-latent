"""Tests for Phase 2.6: analysis lane schema primitives.

No adapter, tracker, OCR, or ML dependency is exercised here -- these
are pure, offline unit tests over `analysis_lanes.py`'s raw-dict
validation helpers, mirroring `test_review_events.py`'s split between
factory/shape tests and track-level tests.
"""

from __future__ import annotations

import pytest

from clu_latent.analysis_lanes import (
    ANALYSIS_LANE_NAMES,
    AnalysisAdapterReceipt,
    AnalysisEventError,
    SUPPORTED_ANALYSIS_LANES,
    is_supported_analysis_lane,
    normalize_analysis_lane_name,
    validate_analysis_event,
    validate_analysis_track,
)
from clu_latent.security.limits import Limits


def _minimal_event(**overrides):
    event = {
        "id": "scn_000000",
        "type": "scene_boundary",
        "t_start_ms": 1000,
        "t_end_ms": 1000,
        "producer": {"name": "adapter:pyscenedetect", "version": "0.6.0"},
        "payload": {"boundary_kind": "cut"},
    }
    event.update(overrides)
    return event


# --- Lane catalog ------------------------------------------------------


def test_analysis_lane_names_match_phase_2_5_catalog():
    expected = {
        "scene_events",
        "visual_change_events",
        "motion_events",
        "object_proposal_events",
        "object_tracking_events",
        "ocr_events",
        "audio_energy_events",
        "audio_transient_events",
        "audio_texture_events",
        "audio_signature_events",
        "rhythm_events",
        "music_events",
        "stereo_events",
        "cross_lane_link_events",
    }
    assert SUPPORTED_ANALYSIS_LANES == expected
    assert len(ANALYSIS_LANE_NAMES) == len(set(ANALYSIS_LANE_NAMES))  # no duplicates


def test_is_supported_analysis_lane_true_for_known_lane():
    assert is_supported_analysis_lane("scene_events") is True


def test_is_supported_analysis_lane_false_for_unknown_lane():
    assert is_supported_analysis_lane("nonsense_events") is False


def test_is_supported_analysis_lane_false_for_non_string():
    assert is_supported_analysis_lane(123) is False


def test_normalize_analysis_lane_name_strips_whitespace():
    assert normalize_analysis_lane_name(" scene_events \n") == "scene_events"


def test_normalize_analysis_lane_name_rejects_unknown_lane():
    with pytest.raises(AnalysisEventError):
        normalize_analysis_lane_name("not_a_real_lane")


def test_normalize_analysis_lane_name_rejects_non_string():
    with pytest.raises(AnalysisEventError):
        normalize_analysis_lane_name(123)


# --- validate_analysis_event: happy path --------------------------------


def test_validate_analysis_event_accepts_valid_minimal_event():
    errors, warnings = validate_analysis_event(_minimal_event(), lane="scene_events")
    assert errors == []


def test_validate_analysis_event_accepts_event_without_lane():
    errors, _ = validate_analysis_event(_minimal_event())
    assert errors == []


def test_validate_analysis_event_accepts_optional_confidence():
    errors, _ = validate_analysis_event(_minimal_event(confidence=0.75))
    assert errors == []


def test_validate_analysis_event_accepts_matching_duration_ms():
    event = _minimal_event(t_start_ms=1000, t_end_ms=1500, duration_ms=500)
    errors, _ = validate_analysis_event(event)
    assert errors == []


# --- validate_analysis_event: record/type failures ----------------------


def test_validate_analysis_event_rejects_non_dict_record():
    errors, _ = validate_analysis_event(["not", "a", "dict"])
    assert any("JSON object" in e for e in errors)


def test_validate_analysis_event_rejects_missing_id():
    event = _minimal_event()
    del event["id"]
    errors, _ = validate_analysis_event(event)
    assert any(e == "id is required" for e in errors)


def test_validate_analysis_event_rejects_oversized_id():
    tiny_limits = Limits(max_analysis_id_bytes=4)
    errors, _ = validate_analysis_event(
        _minimal_event(id="way_too_long_for_the_bound"), limits=tiny_limits
    )
    assert any("id" in e and "bound" in e for e in errors)


def test_validate_analysis_event_rejects_oversized_type():
    tiny_limits = Limits(max_analysis_type_bytes=4)
    errors, _ = validate_analysis_event(
        _minimal_event(type="a_type_string_much_too_long"), limits=tiny_limits
    )
    assert any("type" in e and "bound" in e for e in errors)


# --- validate_analysis_event: timestamp failures -------------------------


def test_validate_analysis_event_rejects_string_t_start_ms():
    errors, _ = validate_analysis_event(_minimal_event(t_start_ms="1000"))
    assert any("t_start_ms must be an integer" in e for e in errors)


def test_validate_analysis_event_rejects_float_t_end_ms():
    errors, _ = validate_analysis_event(_minimal_event(t_end_ms=1000.0))
    assert any("t_end_ms must be an integer" in e for e in errors)


def test_validate_analysis_event_rejects_bool_t_start_ms():
    """bool is a subclass of int in Python -- must not be silently accepted."""
    errors, _ = validate_analysis_event(_minimal_event(t_start_ms=True))
    assert any("t_start_ms must be an integer" in e for e in errors)


def test_validate_analysis_event_rejects_negative_t_start_ms():
    errors, _ = validate_analysis_event(_minimal_event(t_start_ms=-5, t_end_ms=100))
    assert any("t_start_ms must be >= 0" in e for e in errors)


def test_validate_analysis_event_rejects_negative_t_end_ms():
    errors, _ = validate_analysis_event(_minimal_event(t_start_ms=0, t_end_ms=-5))
    assert any("t_end_ms must be >= 0" in e for e in errors)


def test_validate_analysis_event_rejects_t_end_before_t_start():
    errors, _ = validate_analysis_event(_minimal_event(t_start_ms=2000, t_end_ms=1000))
    assert any("must be >= t_start_ms" in e for e in errors)


def test_validate_analysis_event_rejects_mismatched_duration_ms():
    event = _minimal_event(t_start_ms=1000, t_end_ms=1500, duration_ms=999)
    errors, _ = validate_analysis_event(event)
    assert any("duration_ms" in e and "does not match" in e for e in errors)


def test_validate_analysis_event_rejects_negative_duration_ms():
    event = _minimal_event(duration_ms=-1)
    errors, _ = validate_analysis_event(event)
    assert any("duration_ms must be a non-negative integer" in e for e in errors)


# --- validate_analysis_event: confidence failures ------------------------


def test_validate_analysis_event_rejects_confidence_above_one():
    errors, _ = validate_analysis_event(_minimal_event(confidence=1.5))
    assert any("confidence must be within [0.0, 1.0]" in e for e in errors)


def test_validate_analysis_event_rejects_confidence_below_zero():
    errors, _ = validate_analysis_event(_minimal_event(confidence=-0.1))
    assert any("confidence must be within [0.0, 1.0]" in e for e in errors)


def test_validate_analysis_event_rejects_bool_confidence():
    errors, _ = validate_analysis_event(_minimal_event(confidence=True))
    assert any("confidence must be a number" in e for e in errors)


# --- validate_analysis_event: producer failures ---------------------------


def test_validate_analysis_event_rejects_missing_producer():
    event = _minimal_event()
    del event["producer"]
    errors, _ = validate_analysis_event(event)
    assert any(e == "producer is required" for e in errors)


def test_validate_analysis_event_rejects_string_producer():
    errors, _ = validate_analysis_event(_minimal_event(producer="adapter:opencv"))
    assert any("producer must be an object" in e for e in errors)


def test_validate_analysis_event_rejects_oversized_producer_name():
    tiny_limits = Limits(max_analysis_label_bytes=4)
    errors, _ = validate_analysis_event(
        _minimal_event(producer={"name": "adapter:way-too-long", "version": "1.0"}),
        limits=tiny_limits,
    )
    assert any("producer.name" in e for e in errors)


# --- validate_analysis_event: payload failures ----------------------------


def test_validate_analysis_event_rejects_non_dict_payload():
    errors, _ = validate_analysis_event(_minimal_event(payload=["not", "a", "dict"]))
    assert any("payload must be a JSON object" in e for e in errors)


def test_validate_analysis_event_rejects_missing_payload():
    event = _minimal_event()
    del event["payload"]
    errors, _ = validate_analysis_event(event)
    assert any(e == "payload is required" for e in errors)


def test_validate_analysis_event_rejects_oversized_payload():
    tiny_limits = Limits(max_analysis_payload_bytes=8)
    errors, _ = validate_analysis_event(
        _minimal_event(payload={"boundary_kind": "cut", "notes": "way too much data for 8 bytes"}),
        limits=tiny_limits,
    )
    assert any("payload exceeds" in e for e in errors)


# --- validate_analysis_event: path fields --------------------------------


def test_validate_analysis_event_rejects_absolute_path_field():
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "person-like region", "mask_path": "/etc/passwd"},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert any("must be relative" in e for e in errors)


def test_validate_analysis_event_rejects_parent_traversal_path_field():
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "object_candidate_7", "mask_path": "../../etc/passwd"},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert any("'..' segments" in e for e in errors)


def test_validate_analysis_event_accepts_relative_posix_path_field():
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "ball-like candidate", "mask_path": "media/masks/000001.png"},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert errors == []


def test_validate_analysis_event_rejects_symlink_escape_with_package_root(tmp_path):
    package_root = tmp_path / "pkg.clulatent"
    (package_root / "media").mkdir(parents=True)
    outside_target = tmp_path / "outside.png"
    outside_target.write_bytes(b"not a real image")
    (package_root / "media" / "escape.png").symlink_to(outside_target)

    event = _minimal_event(
        type="object_proposal",
        payload={"label": "object_candidate_7", "mask_path": "media/escape.png"},
    )
    errors, _ = validate_analysis_event(
        event, lane="object_proposal_events", package_root=package_root
    )
    assert any("symlink" in e for e in errors)


# --- Safe claim rules: identity ------------------------------------------


@pytest.mark.parametrize(
    "forbidden_field", ["person_name", "identity", "face_identity", "biometric_identity"]
)
def test_validate_analysis_event_rejects_identity_claim_fields(forbidden_field):
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "person-like region", forbidden_field: "Jane Doe"},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert any("identity claim" in e for e in errors)


def test_validate_analysis_event_rejects_is_identity_true():
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "person-like region", "is_identity": True},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert any("is_identity" in e and "must be false" in e for e in errors)


def test_validate_analysis_event_accepts_is_identity_false():
    event = _minimal_event(
        type="object_proposal",
        payload={"label": "person-like region", "is_identity": False},
    )
    errors, _ = validate_analysis_event(event, lane="object_proposal_events")
    assert errors == []


def test_validate_analysis_event_accepts_neutral_labels():
    for label in ["ball-like candidate", "person-like region", "object_candidate_7"]:
        event = _minimal_event(type="object_proposal", payload={"label": label})
        errors, _ = validate_analysis_event(event, lane="object_proposal_events")
        assert errors == [], f"unexpected errors for label {label!r}: {errors}"


def test_validate_analysis_event_accepts_neutral_audio_labels():
    for label in [
        "impact-like",
        "speech-like",
        "music-like",
        "repeating pattern",
        "left-channel dominant",
        "sudden silence",
    ]:
        event = _minimal_event(
            type="audio_texture_window", payload={"texture_tags": [label]}
        )
        errors, _ = validate_analysis_event(event, lane="audio_texture_events")
        assert errors == [], f"unexpected errors for label {label!r}: {errors}"


# --- cross_lane_link_events -----------------------------------------------


def _link_event(**payload_overrides):
    payload = {
        "source_event_ids": ["scn_000004"],
        "target_event_ids": ["mot_000010"],
        "relation_type": "co_occurs",
    }
    payload.update(payload_overrides)
    return _minimal_event(
        id="lnk_000000", type="cross_lane_link", payload=payload
    )


def test_validate_analysis_event_accepts_valid_cross_lane_link_event():
    errors, _ = validate_analysis_event(_link_event(), lane="cross_lane_link_events")
    assert errors == []


def test_validate_analysis_event_accepts_cross_lane_link_confidence():
    errors, _ = validate_analysis_event(
        _link_event(confidence=0.5), lane="cross_lane_link_events"
    )
    assert errors == []


def test_validate_analysis_event_rejects_cross_lane_link_missing_source_ids():
    event = _link_event()
    del event["payload"]["source_event_ids"]
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("source_event_ids is required" in e for e in errors)


def test_validate_analysis_event_rejects_cross_lane_link_missing_target_ids():
    event = _link_event()
    del event["payload"]["target_event_ids"]
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("target_event_ids is required" in e for e in errors)


def test_validate_analysis_event_rejects_cross_lane_link_non_list_source_ids():
    event = _link_event(source_event_ids="scn_000004")
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("source_event_ids" in e and "array" in e for e in errors)


def test_validate_analysis_event_rejects_cross_lane_link_missing_relation_type():
    event = _link_event()
    del event["payload"]["relation_type"]
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("relation_type is required" in e for e in errors)


def test_validate_analysis_event_rejects_cross_lane_link_self_reference():
    event = _link_event(source_event_ids=["lnk_000000"])
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("source_event_ids must not reference its own event id" in e for e in errors)


@pytest.mark.parametrize("causal_relation", ["causes", "caused_by", "proves", "confirms"])
def test_validate_analysis_event_rejects_causal_relation_type(causal_relation):
    event = _link_event(relation_type=causal_relation)
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("asserts causation" in e for e in errors)


def test_validate_analysis_event_rejects_cross_lane_link_identity_claim():
    event = _link_event(is_identity_claim=True)
    errors, _ = validate_analysis_event(event, lane="cross_lane_link_events")
    assert any("is_identity_claim" in e and "must be false" in e for e in errors)


# --- validate_analysis_track ----------------------------------------------


def test_validate_analysis_track_accepts_valid_track():
    events = [
        _minimal_event(id="scn_000000", t_start_ms=0, t_end_ms=0),
        _minimal_event(id="scn_000001", t_start_ms=5000, t_end_ms=5000),
    ]
    errors, _ = validate_analysis_track(events, lane="scene_events")
    assert errors == []


def test_validate_analysis_track_rejects_unsupported_lane():
    errors, _ = validate_analysis_track([_minimal_event()], lane="not_a_real_lane")
    assert any("not a supported analysis lane" in e for e in errors)


def test_validate_analysis_track_rejects_duplicate_ids():
    events = [
        _minimal_event(id="scn_000000", t_start_ms=0, t_end_ms=0),
        _minimal_event(id="scn_000000", t_start_ms=1000, t_end_ms=1000),
    ]
    errors, _ = validate_analysis_track(events, lane="scene_events")
    assert any("duplicate analysis event id" in e for e in errors)


def test_validate_analysis_track_aggregates_errors_across_records():
    events = [
        _minimal_event(id="scn_000000", t_start_ms=-1, t_end_ms=0),
        _minimal_event(id="scn_000001", confidence=5.0),
    ]
    errors, _ = validate_analysis_track(events, lane="scene_events")
    assert any("scn_000000" in e and "t_start_ms" in e for e in errors)
    assert any("scn_000001" in e and "confidence" in e for e in errors)


def test_validate_analysis_track_accepts_empty_track():
    errors, warnings = validate_analysis_track([], lane="scene_events")
    assert errors == []
    assert warnings == []


# --- AnalysisAdapterReceipt --------------------------------------------


def test_analysis_adapter_receipt_to_dict_round_trip():
    receipt = AnalysisAdapterReceipt(
        adapter_name="adapter:pyscenedetect",
        tool_name="pyscenedetect",
        tool_version="0.6.3",
        status="success",
        output_tracks=["scene_events"],
        event_counts={"scene_boundary": 12},
        input_sources=["sources/video.mp4"],
        warnings=["1 event clamped to source duration"],
        clamped_count=1,
    )
    data = receipt.to_dict()
    assert data["adapter_name"] == "adapter:pyscenedetect"
    assert data["status"] == "success"
    assert data["output_tracks"] == ["scene_events"]
    assert data["event_counts"] == {"scene_boundary": 12}
    assert data["clamped_count"] == 1
    assert data["failure_details"] is None
