"""Tests for Phase 3.3: audio evidence digest contract.

This phase adds no new module, adapter, or CLI command — it is a
design/schema/docs phase defining the LLM-safe audio digest layer
("store deep, show shallow, retrieve detail only when needed"). These
tests use stable keyword/section checks (not brittle exact-prose
matching) to guard that the docs stay conservative: the six-level
pyramid, the three new digest record shapes, salience/ranking,
bounded context packets, retrieval by time range and evidence id,
package-internal path references (not raw dumps), mandatory
evidence-not-truth caveats, allowed hedged language, and forbidden
claims.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_3_AUDIO_EVIDENCE_DIGEST_CONTRACT.md"
CONTRACT_DOC = REPO_ROOT / "docs" / "AUDIO_EVIDENCE_DIGEST_CONTRACT.md"

PUBLIC_DOCS = [README_PATH, PHASE_DOC, CONTRACT_DOC]

REQUIRED_RECORD_SHAPES = (
    "audio_feature_series",
    "audio_digest_segment",
    "audio_llm_context_packet",
)

ALLOWED_PHRASES = (
    "candidate effect",
    "linked evidence suggests",
    "compression/limiting evidence",
    "rhythmic density increase",
    "tension-like buildup candidate",
)

FORBIDDEN_CLAIMS = (
    "proves intent",
    "manipulation",
    "makes viewer afraid",
    "clulatent understands audio",
    "semantic audio truth",
    "definitely exact instrument/source",
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


# --- Docs exist ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_contract_doc_exists():
    assert CONTRACT_DOC.is_file()


def test_phase_doc_references_contract_doc():
    assert "AUDIO_EVIDENCE_DIGEST_CONTRACT.md" in _read(PHASE_DOC)


# --- Core rule / evidence-not-truth -----------------------------------------


def test_docs_state_core_rule():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read_normalized(doc).lower()
        assert "store deep" in lowered
        assert "show shallow" in lowered
        assert "retrieve detail only when needed" in lowered


def test_docs_include_evidence_not_truth_language():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        assert "evidence" in lowered
        assert "not truth" in lowered


# --- Multi-resolution pyramid ------------------------------------------------


def test_docs_define_pyramid_levels_0_through_5():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        assert "level 0" in lowered
        assert "level 1" in lowered
        assert "level 2" in lowered
        assert "level 3" in lowered
        assert "level 4" in lowered
        assert "level 5" in lowered


def test_docs_name_each_pyramid_level():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        assert "source audio" in lowered
        assert "dense metrics" in lowered
        assert "events" in lowered
        assert "segments" in lowered
        assert "summaries" in lowered
        assert "llm context packet" in lowered


def test_docs_define_required_record_shapes():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        text = _read(doc)
        for shape in REQUIRED_RECORD_SHAPES:
            assert shape in text, f"{shape} missing from {doc.name}"


# --- Salience / ranking / bounding -------------------------------------------


def test_contract_doc_defines_salience_and_ranking():
    lowered = _read(CONTRACT_DOC).lower()
    assert "salience" in lowered
    assert "ranking" in lowered or "rank" in lowered


def test_contract_doc_requires_bounded_context_packets():
    lowered = _read_normalized(CONTRACT_DOC).lower()
    assert "bounded" in lowered
    assert "token" in lowered or "context budget" in lowered


# --- Retrieval ---------------------------------------------------------------


def test_docs_define_retrieval_by_time_range_and_evidence_id():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        assert "time range" in lowered
        assert "evidence id" in lowered


def test_contract_doc_requires_path_references_not_raw_dumps():
    lowered = _read_normalized(CONTRACT_DOC).lower()
    assert "package-internal path" in lowered or "package-relative" in lowered
    assert "not inlined" in lowered or "no raw dumps" in lowered


# --- Caveats -------------------------------------------------------------------


def test_contract_doc_requires_mandatory_caveat_field():
    lowered = _read_normalized(CONTRACT_DOC).lower()
    assert "caveat" in lowered
    assert "mandatory" in lowered or "required, non-optional" in lowered


# --- Allowed language / forbidden claims -------------------------------------


def test_docs_include_allowed_language():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        for phrase in ALLOWED_PHRASES:
            assert phrase in lowered, f"{phrase!r} missing from {doc.name}"


def test_docs_forbid_required_claims():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        lowered = _read(doc).lower()
        for phrase in FORBIDDEN_CLAIMS:
            assert phrase in lowered  # listed as an explicitly forbidden example
        assert "forbidden claims" in lowered


def test_docs_do_not_assert_clulatent_understands_audio():
    for doc in (PHASE_DOC, CONTRACT_DOC):
        normalized = _read(doc).lower().replace("*", "").replace('"', "")
        # The phrase may appear only inside a "must not claim" / forbidden list.
        if "clulatent understands audio" in normalized:
            assert "forbidden" in normalized or "never" in normalized or "must not" in normalized


# --- README ------------------------------------------------------------------


def test_readme_includes_phase_3_3_roadmap_bullet():
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
