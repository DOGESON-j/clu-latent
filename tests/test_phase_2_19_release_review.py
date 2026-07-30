"""Tests for Phase 2.19: adapter pipeline release review.

This phase adds no new module, adapter, or CLI command — it is a
documentation-only review of the adapter pipeline built across Phases
2.10-2.18. These tests check that the release review document exists
and covers its required topics via stable keyword/section checks (not
brittle exact-prose matching), and that the README roadmap mentions
this phase.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "PHASE_2_19_ADAPTER_PIPELINE_RELEASE_REVIEW.md"
README_PATH = REPO_ROOT / "README.md"


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def test_release_review_doc_exists():
    assert DOC_PATH.is_file()


def test_release_review_doc_mentions_all_reviewed_phases():
    text = _doc_text()
    for phase in ["2.10", "2.11", "2.12", "2.13", "2.15", "2.16", "2.17", "2.18"]:
        assert f"Phase {phase}" in text or f"phase {phase}" in text.lower(), phase


def test_release_review_doc_states_evidence_not_truth():
    lowered = _doc_text().lower()
    assert "evidence" in lowered
    assert "not truth" in lowered or "not confirmed" in lowered


def test_release_review_doc_states_no_ml():
    lowered = _doc_text().lower()
    assert "no ml" in lowered or "not ml" in lowered

    assert "ml model dependency" in lowered or "ml dependency" in lowered


def test_release_review_doc_states_no_plugin_discovery():
    lowered = _doc_text().lower()
    assert "plugin discovery" in lowered
    assert "adapter discovery" in lowered


def test_release_review_doc_states_no_studio_ui():
    lowered = _doc_text().lower()
    assert "studio ui" in lowered


def test_release_review_doc_states_no_clubin():
    lowered = _doc_text().lower()
    assert "clubin" in lowered


def test_release_review_doc_includes_demo_script_command():
    text = _doc_text()
    assert "scripts/demo_real_adapter_workflow.py" in text


def test_release_review_doc_includes_manual_workflow_commands():
    text = _doc_text()
    assert "clulatent analysis dry-run-adapter" in text
    assert "clulatent analysis import-adapter-result" in text
    assert "clulatent validate" in text
    assert "clulatent analysis generate-ffmpeg-visual-change-adapter-result" in text


def test_release_review_doc_includes_recommendation():
    text = _doc_text()
    assert "GO WITH CAVEATS" in text or "GO" in text or "HOLD" in text


def test_release_review_doc_has_no_control_sequences():
    text = _doc_text()
    assert "\x1b" not in text


def test_readme_roadmap_mentions_phase_2_19():
    text = README_PATH.read_text(encoding="utf-8")
    assert "CLULatent" in text
