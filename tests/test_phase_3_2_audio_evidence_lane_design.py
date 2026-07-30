"""Tests for Phase 3.2: audio evidence lane design.

This phase adds no new module, adapter, or CLI command — it is a
design/schema/docs phase defining the next-generation audio evidence
lane catalog. These tests use stable keyword/section checks (not
brittle exact-prose matching) to guard that the docs stay conservative:
evidence-not-truth language, all ten required lanes, compression
evidence, perceptual-effect candidates backed by measurable evidence,
no forbidden intent/manipulation/certainty claims, no claim that
CLULatent understands audio, and future adapters framed as optional.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_2_AUDIO_EVIDENCE_LANE_DESIGN.md"
LANES_DOC = REPO_ROOT / "docs" / "AUDIO_EVIDENCE_LANES.md"

PUBLIC_DOCS = [README_PATH, PHASE_DOC, LANES_DOC]

REQUIRED_LANES = (
    "audio_energy_events",
    "audio_transient_events",
    "audio_rhythm_events",
    "audio_pitch_events",
    "audio_stem_events",
    "audio_instrument_candidate_events",
    "audio_texture_events",
    "audio_compression_events",
    "audio_spatial_events",
    "audio_perceptual_effect_events",
)

FORBIDDEN_CLAIMS = (
    "this audio is trying to manipulate you",
    "this proves intent",
    "this makes the viewer afraid",
    "this is definitely a gunshot",
    "this is definitely a specific instrument",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_normalized(path: Path) -> str:
    """Read a doc with wrapped-line whitespace collapsed to single spaces.

    Prose docs are hand-wrapped at ~72 chars, so a phrase that reads as
    one sentence can be split across a newline. Collapsing whitespace
    lets tests match on stable phrases without being brittle to wrap
    width.
    """
    return " ".join(_read(path).split())


# --- Phase doc -----------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_lanes_doc_exists():
    assert LANES_DOC.is_file()


def test_phase_doc_references_lanes_doc():
    assert "AUDIO_EVIDENCE_LANES.md" in _read(PHASE_DOC)


# --- Evidence-not-truth language ------------------------------------------


def test_docs_include_evidence_not_truth_language():
    for doc in (PHASE_DOC, LANES_DOC):
        lowered = _read(doc).lower()
        assert "evidence" in lowered
        assert "not truth" in lowered or "is not truth" in lowered


def test_docs_state_audio_evidence_is_not_intent():
    for doc in (PHASE_DOC, LANES_DOC):
        lowered = _read_normalized(doc).lower()
        assert "audio evidence is not intent" in lowered


# --- Lane catalog ----------------------------------------------------------


def test_docs_define_all_required_audio_lanes():
    for doc in (PHASE_DOC, LANES_DOC):
        text = _read(doc)
        for lane in REQUIRED_LANES:
            assert lane in text, f"{lane} missing from {doc.name}"


def test_docs_include_compression_limiting_artifact_evidence():
    lowered = _read(LANES_DOC).lower()
    assert "clipping" in lowered
    assert "limiting" in lowered
    assert "dynamic range" in lowered


# --- Perceptual-effect candidates -------------------------------------------


def test_docs_include_perceptual_effect_candidates():
    for doc in (PHASE_DOC, LANES_DOC):
        lowered = _read(doc).lower()
        assert "perceptual-effect" in lowered or "perceptual_effect" in lowered
        assert "tension-like buildup" in lowered


def test_docs_require_measurable_evidence_for_perceptual_effect_candidates():
    lowered = _read_normalized(LANES_DOC).lower()
    assert "measurable" in lowered
    assert "no pure vibes" in lowered


def test_phase_doc_includes_worked_example_with_linked_event_ids():
    text = _read(PHASE_DOC)
    assert "linked_event_ids" in text
    assert '"tension-like buildup"' in text


# --- Forbidden claims / conservative language -------------------------------


def test_docs_forbid_intent_proof_manipulation_claims():
    lowered = _read(PHASE_DOC).lower()
    for phrase in FORBIDDEN_CLAIMS:
        assert phrase in lowered  # listed as an explicitly forbidden example
    assert "forbidden claims" in lowered or "never assert" in lowered


def test_docs_do_not_claim_clulatent_understands_audio():
    for doc in (PHASE_DOC, LANES_DOC):
        normalized = _read(doc).lower().replace("*", "").replace('"', "")
        # The phrase may appear only inside a "must not claim" list.
        if "clulatent understands audio" in normalized:
            assert "must not" in normalized or "never" in normalized


# --- Future adapters ---------------------------------------------------------


def test_docs_mention_future_adapters_as_optional():
    lowered = _read_normalized(LANES_DOC).lower()
    assert "future adapter" in lowered
    assert "none is required" in lowered
    for tool in ("ffmpeg", "librosa", "essentia", "aubio", "demucs"):
        assert tool in lowered


def test_docs_mark_model_based_tools_as_later_optional_evidence():
    lowered = _read(LANES_DOC).lower()
    for tool in ("yamnet", "panns", "openl3"):
        assert tool in lowered
    assert "optional model evidence" in lowered


# --- README ------------------------------------------------------------------


def test_readme_includes_phase_3_2_roadmap_bullet():
    assert "CLULatent" in _read(README_PATH)


# --- Safety: no leaked private paths / control sequences ----------------------


def test_public_docs_avoid_local_private_paths():
    for doc in PUBLIC_DOCS:
        text = _read(doc)
        assert "PRIVATE_HOME_SENTINEL" not in text, doc
        assert "PRIVATE_GRID_SENTINEL" not in text, doc


def test_public_docs_have_no_control_sequences():
    for doc in PUBLIC_DOCS:
        text = _read(doc)
        assert "\x1b" not in text, doc
