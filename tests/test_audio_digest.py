"""Tests for Phase 3.4: audio evidence digest schema primitives.

Covers `src/clu_latent/audio_digest.py` -- the code-level, non-raising
`(errors, warnings)` validators for the three Phase 3.3 digest record
shapes (`audio_feature_series`, `audio_digest_segment`,
`audio_llm_context_packet`), plus the shared envelope checks and the
Phase 3.4 docs/README bullet.

Tests use minimal, explicit fixtures (not brittle prose matching) so
they stay stable as the module's internals evolve.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from clu_latent import audio_digest

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_4_AUDIO_DIGEST_SCHEMA_PRIMITIVES.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_normalized(path: Path) -> str:
    return " ".join(_read(path).split())


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


def _set_payload(event: dict, **payload_overrides) -> dict:
    event = copy.deepcopy(event)
    event["payload"].update(payload_overrides)
    return event


# --- module imports cleanly --------------------------------------------------


def test_module_imports_cleanly():
    assert audio_digest is not None


# --- supported digest record types -------------------------------------------


def test_supported_digest_record_types():
    assert audio_digest.AUDIO_DIGEST_RECORD_TYPES == (
        "audio_feature_series",
        "audio_digest_segment",
        "audio_llm_context_packet",
    )
    assert audio_digest.is_supported_audio_digest_type("audio_feature_series")
    assert audio_digest.is_supported_audio_digest_type("audio_digest_segment")
    assert audio_digest.is_supported_audio_digest_type("audio_llm_context_packet")
    assert not audio_digest.is_supported_audio_digest_type("not_a_real_type")
    assert not audio_digest.is_supported_audio_digest_type(None)


# --- audio_feature_series -----------------------------------------------------


def test_valid_audio_feature_series_passes():
    errors, warnings = audio_digest.validate_audio_feature_series(_feature_series())
    assert errors == []


def test_audio_feature_series_rejects_raw_values_array():
    event = _set_payload(_feature_series(), values=[0.1, 0.2, 0.3])
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("values" in e and "raw dense-array" in e for e in errors)


def test_audio_feature_series_rejects_absolute_data_path():
    event = _set_payload(_feature_series(), data_path="/etc/passwd")
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("data_path" in e for e in errors)


def test_audio_feature_series_rejects_parent_traversal_data_path():
    event = _set_payload(_feature_series(), data_path="../../etc/passwd")
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("data_path" in e for e in errors)


def test_audio_feature_series_requires_summary_stats():
    event = _set_payload(_feature_series(), summary={})
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("summary" in e for e in errors)

    event = copy.deepcopy(_feature_series())
    del event["payload"]["summary"]
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("summary" in e for e in errors)


# --- audio_digest_segment ------------------------------------------------------


def test_valid_audio_digest_segment_passes():
    errors, _ = audio_digest.validate_audio_digest_segment(_digest_segment())
    assert errors == []


def test_audio_digest_segment_requires_salience_bounded_0_1():
    event = _set_payload(_digest_segment(), salience=1.5)
    errors, _ = audio_digest.validate_audio_digest_segment(event)
    assert any("salience" in e for e in errors)

    event = copy.deepcopy(_digest_segment())
    del event["payload"]["salience"]
    errors, _ = audio_digest.validate_audio_digest_segment(event)
    assert any("salience" in e for e in errors)


def test_audio_digest_segment_requires_caveats():
    event = _set_payload(_digest_segment(), caveats=[])
    errors, _ = audio_digest.validate_audio_digest_segment(event)
    assert any("caveats" in e for e in errors)


def test_audio_digest_segment_rejects_intent_manipulation_proof_language():
    event = _set_payload(_digest_segment(), summary="This proves intent behind the mix.")
    errors, _ = audio_digest.validate_audio_digest_segment(event)
    assert any("forbidden language" in e for e in errors)

    event = _set_payload(_digest_segment(), summary="This is manipulation of the listener.")
    errors, _ = audio_digest.validate_audio_digest_segment(event)
    assert any("forbidden language" in e for e in errors)


# --- audio_llm_context_packet --------------------------------------------------


def test_valid_audio_llm_context_packet_passes():
    errors, _ = audio_digest.validate_audio_llm_context_packet(_context_packet())
    assert errors == []


def test_audio_llm_context_packet_requires_bounded_token_budget():
    event = _set_payload(_context_packet(), budget_tokens_estimate=-5)
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("budget_tokens_estimate" in e for e in errors)

    event = _set_payload(_context_packet(), budget_tokens_estimate=10_000_000)
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("budget_tokens_estimate" in e for e in errors)


def test_audio_llm_context_packet_requires_caveats_and_warnings():
    event = _set_payload(_context_packet(), caveats=[])
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("caveats" in e for e in errors)

    event = _set_payload(_context_packet(), warnings=[])
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("warnings" in e for e in errors)

    event = _set_payload(
        _context_packet(), caveats=["This packet is short and to the point."]
    )
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("evidence-not-truth" in e for e in errors)


def test_audio_llm_context_packet_rejects_dense_arrays():
    event = _set_payload(_context_packet(), embedding=[0.1, 0.2, 0.3])
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("embedding" in e and "raw dense-array" in e for e in errors)


def test_audio_llm_context_packet_supports_retrieval_hints():
    errors, _ = audio_digest.validate_audio_llm_context_packet(_context_packet())
    assert errors == []

    event = copy.deepcopy(_context_packet())
    del event["payload"]["retrieval_hints"]
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("retrieval_hints" in e for e in errors)

    event = _set_payload(_context_packet(), retrieval_hints={"by_time_range": "x"})
    errors, _ = audio_digest.validate_audio_llm_context_packet(event)
    assert any("by_evidence_id" in e for e in errors)


# --- shared envelope checks -----------------------------------------------------


def test_shared_envelope_rejects_bool_timestamps():
    event = _set_payload(_feature_series())
    event = copy.deepcopy(event)
    event["t_start_ms"] = True
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("t_start_ms" in e for e in errors)


def test_shared_envelope_rejects_confidence_outside_0_1():
    event = copy.deepcopy(_feature_series())
    event["confidence"] = 1.5
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("confidence" in e for e in errors)

    event = copy.deepcopy(_feature_series())
    event["confidence"] = -0.1
    errors, _ = audio_digest.validate_audio_feature_series(event)
    assert any("confidence" in e for e in errors)


def test_shared_envelope_rejects_unknown_type():
    event = copy.deepcopy(_feature_series())
    event["type"] = "audio_something_else"
    errors, _ = audio_digest.validate_audio_digest_event(event)
    assert any("not a supported audio digest record type" in e for e in errors)


# --- digest track ----------------------------------------------------------------


def test_digest_track_validates_multiple_events():
    errors, _ = audio_digest.validate_audio_digest_track(
        [_feature_series(), _digest_segment(), _context_packet()]
    )
    assert errors == []


def test_digest_track_rejects_duplicate_ids():
    event_a = _feature_series(id="dup_000001")
    event_b = _digest_segment(id="dup_000001")
    errors, _ = audio_digest.validate_audio_digest_track([event_a, event_b])
    assert any("duplicate audio digest record id" in e for e in errors)


def test_digest_track_returns_errors_without_raising():
    errors, warnings = audio_digest.validate_audio_digest_track(
        [{"not": "a valid record"}, None, 42]
    )
    assert isinstance(errors, list)
    assert isinstance(warnings, list)
    assert len(errors) > 0


# --- docs --------------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_states_core_rule():
    lowered = _read_normalized(PHASE_DOC).lower()
    assert "store deep" in lowered
    assert "show shallow" in lowered
    assert "retrieve detail only when needed" in lowered


def test_readme_includes_phase_3_4_roadmap_bullet():
    assert "CLULatent" in _read(README_PATH)


# --- no regressions ------------------------------------------------------------


def test_phase_3_2_and_3_3_docs_and_tests_still_exist():
    assert (REPO_ROOT / "docs" / "PHASE_3_2_AUDIO_EVIDENCE_LANE_DESIGN.md").is_file()
    assert (REPO_ROOT / "docs" / "AUDIO_EVIDENCE_LANES.md").is_file()
    assert (REPO_ROOT / "docs" / "PHASE_3_3_AUDIO_EVIDENCE_DIGEST_CONTRACT.md").is_file()
    assert (REPO_ROOT / "docs" / "AUDIO_EVIDENCE_DIGEST_CONTRACT.md").is_file()
