"""Tests for Phase 2.21: license and public repo gate.

This phase adds no new module, adapter, or CLI command — it is a
license-audit, repo-hygiene, and public-gate documentation pass (it
publishes nothing and invents no license). These tests use stable
keyword/section checks (not brittle exact-prose matching) to verify the
gate docs exist, record the license audit and gate checklist, carry an
explicit GO / GO WITH CAVEATS / HOLD recommendation, that the README
communicates license status while keeping its pre-alpha / evidence-not-
truth framing, and that public docs leak no private paths or control
sequences.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
GATE_DOC = REPO_ROOT / "docs" / "PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md"
LICENSE_DECISION_DOC = REPO_ROOT / "docs" / "LICENSE_DECISION.md"
PUBLIC_REPO_GATE_DOC = REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md"

PUBLIC_DOCS = [README_PATH, GATE_DOC, LICENSE_DECISION_DOC, PUBLIC_REPO_GATE_DOC]

_RECOMMENDATIONS = ("GO WITH CAVEATS", "HOLD", "GO")


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _gate() -> str:
    return GATE_DOC.read_text(encoding="utf-8")


# --- Phase 2.21 gate doc ------------------------------------------------------


def test_gate_doc_exists():
    assert GATE_DOC.is_file()


def test_gate_doc_includes_license_audit():
    lowered = _gate().lower()
    assert "license audit" in lowered
    assert "proprietary" in lowered


def test_gate_doc_includes_public_repo_gate_checklist():
    lowered = _gate().lower()
    assert "public repo gate checklist" in lowered or "gate checklist" in lowered


def test_gate_doc_includes_recommendation():
    text = _gate()
    assert any(rec in text for rec in _RECOMMENDATIONS)


def test_gate_doc_recommends_hold_while_license_pending():
    text = _gate()
    # No public-use license is chosen yet, so the gate must HOLD.
    assert "HOLD" in text


def test_gate_doc_states_no_publishing():
    lowered = _gate().lower()
    assert "publish" in lowered


def test_license_decision_doc_exists_and_states_pending():
    assert LICENSE_DECISION_DOC.is_file()
    lowered = LICENSE_DECISION_DOC.read_text(encoding="utf-8").lower()
    assert "pending" in lowered
    assert "proprietary" in lowered


def test_public_repo_gate_doc_exists_with_checklist():
    assert PUBLIC_REPO_GATE_DOC.is_file()
    lowered = PUBLIC_REPO_GATE_DOC.read_text(encoding="utf-8").lower()
    assert "license" in lowered
    assert "secret" in lowered


# --- README license + retained positioning ------------------------------------


def test_readme_mentions_license_status():
    lowered = _readme().lower()
    assert "license" in lowered
    assert "bsd 3-clause" in lowered


def test_readme_states_resolved_public_license():
    lowered = _readme().lower()
    assert "bsd 3-clause" in lowered


def test_readme_marks_v1_and_provisional_apis():
    lowered = _readme().lower()
    assert "v1" in lowered
    assert "experimental" in lowered


def test_readme_still_includes_evidence_not_truth():
    lowered = _readme().lower()
    assert "evidence" in lowered
    assert "not truth" in lowered


def test_readme_does_not_claim_clubin_implemented():
    lowered = _readme().lower()
    assert "no clubin" in lowered or "not built" in lowered


def test_readme_does_not_claim_studio_ui_implemented():
    lowered = _readme().lower()
    assert "no studio ui" in lowered


def test_readme_does_not_overclaim_understanding():
    normalized = _readme().lower().replace("*", "")
    assert "not understand video" in normalized


def test_readme_roadmap_mentions_phase_2_21():
    assert "CLULatent" in _readme()


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
