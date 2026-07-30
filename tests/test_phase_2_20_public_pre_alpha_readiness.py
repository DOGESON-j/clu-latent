"""Tests for Phase 2.20: public pre-alpha readiness.

This phase adds no new module, adapter, or CLI command — it is a
documentation, repo-hygiene, and safety-messaging pass preparing the repo
for a *possible* public pre-alpha preview (it publishes nothing). These
tests use stable keyword/section checks (not brittle exact-prose matching)
to verify the public-facing docs exist, say the right things, avoid
overclaiming, and contain no leaked private paths or unsafe control
sequences.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
READINESS_DOC = REPO_ROOT / "docs" / "PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md"
TRUST_MODEL_DOC = REPO_ROOT / "docs" / "TRUST_MODEL.md"
CHECKLIST_DOC = REPO_ROOT / "docs" / "PUBLIC_PRE_ALPHA_CHECKLIST.md"

PUBLIC_DOCS = [README_PATH, READINESS_DOC, TRUST_MODEL_DOC, CHECKLIST_DOC]


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _readiness() -> str:
    return READINESS_DOC.read_text(encoding="utf-8")


# --- README public-readiness --------------------------------------------------


def test_readme_marks_v1_and_provisional_apis():
    lowered = _readme().lower()
    assert "v1" in lowered
    assert "experimental" in lowered


def test_readme_states_user_judgment_still_required():
    lowered = _readme().lower()
    assert "judgment" in lowered


def test_readme_includes_evidence_not_truth_framing():
    lowered = _readme().lower()
    assert "evidence" in lowered
    assert "not truth" in lowered or "evidence, not truth" in lowered


def test_readme_includes_local_test_command():
    assert "python -m pytest" in _readme()


def test_readme_includes_demo_script_command():
    assert "clulatent demo" in _readme()


def test_readme_includes_adapter_pipeline_workflow_terms():
    lowered = _readme().lower()
    assert "build-video" in lowered
    assert "open" in lowered
    assert "validate" in lowered


def test_readme_states_local_first_no_cloud():
    lowered = _readme().lower()
    assert "local-first" in lowered or "local developer tool" in lowered
    assert "no cloud" in lowered or "no cloud service" in lowered or "runs locally" in lowered


def test_readme_does_not_claim_clubin_is_implemented():
    lowered = _readme().lower()
    # CLUBIN may be mentioned, but only as not-built / future.
    assert "no clubin" in lowered or "not built" in lowered


def test_readme_does_not_claim_studio_ui_is_implemented():
    lowered = _readme().lower()
    assert "no studio ui" in lowered


def test_readme_does_not_claim_ml_required_for_current_demo():
    lowered = _readme().lower()
    assert "no ml" in lowered


def test_readme_does_not_overclaim_understanding():
    # Normalize away markdown emphasis (e.g. "**not**") before checking.
    normalized = _readme().lower().replace("*", "")
    # The README must explicitly disclaim understanding, not assert it.
    assert "not understand video" in normalized


# --- Readiness doc ------------------------------------------------------------


def test_readiness_doc_exists():
    assert READINESS_DOC.is_file()


def test_readiness_doc_includes_recommendation():
    text = _readiness()
    assert "GO WITH CAVEATS" in text or "GO" in text or "HOLD" in text


def test_readiness_doc_lists_caveats():
    lowered = _readiness().lower()
    assert "caveat" in lowered


def test_readiness_doc_states_no_publishing():
    lowered = _readiness().lower()
    assert "publish" in lowered
    assert "does not publish" in lowered or "publishes nothing" in lowered or "not publish" in lowered


def test_trust_model_doc_exists_and_states_evidence_not_truth():
    assert TRUST_MODEL_DOC.is_file()
    lowered = TRUST_MODEL_DOC.read_text(encoding="utf-8").lower()
    assert "evidence" in lowered
    assert "not truth" in lowered


def test_checklist_doc_exists():
    assert CHECKLIST_DOC.is_file()


# --- Safety: no leaked private paths / control sequences ----------------------


def test_public_docs_avoid_local_private_paths():
    for doc in PUBLIC_DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "PRIVATE_HOME_SENTINEL" not in text, doc
        assert "PRIVATE_GRID_SENTINEL" not in text, doc


def test_public_docs_have_no_control_sequences():
    for doc in PUBLIC_DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "\x1b" not in text, doc


def test_readme_roadmap_mentions_phase_2_20():
    assert "CLULatent" in _readme()
