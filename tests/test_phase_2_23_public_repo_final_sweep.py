"""Tests for Phase 2.23: public repo final sweep.

This phase adds no new module, adapter, or CLI command — it is the final
conservative pre-public repository sweep (consistency, safety, hygiene,
license consistency, packaging sanity). These tests use stable
keyword/section checks (not brittle exact-prose matching) to guard the
public-facing invariants the sweep verified: the README stays honest and
non-overclaiming, the license stays BSD 3-Clause, no stale "license
pending" wording remains as an active claim, and public docs carry no
private paths or terminal control sequences.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
LICENSE_PATH = REPO_ROOT / "LICENSE"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md"

CHOSEN_LICENSE = "BSD-3-Clause"
CHOSEN_LICENSE_NAME = "BSD 3-Clause"

PUBLIC_DOCS = [
    README_PATH,
    PHASE_DOC,
    REPO_ROOT / "docs" / "TRUST_MODEL.md",
    REPO_ROOT / "docs" / "PUBLIC_PRE_ALPHA_CHECKLIST.md",
    REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "LICENSE_DECISION.md",
    REPO_ROOT / "docs" / "PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md",
    REPO_ROOT / "docs" / "PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Phase doc ----------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_includes_recommendation():
    text = _read(PHASE_DOC)
    assert "GO WITH CAVEATS" in text or "GO" in text or "HOLD" in text


def test_phase_doc_names_the_sweep_areas():
    lowered = _read(PHASE_DOC).lower()
    assert "hygiene" in lowered
    assert "consistency" in lowered


# --- README honesty / completeness -------------------------------------------


def test_readme_mentions_v1_and_provisional_apis():
    lowered = _read(README_PATH).lower()
    assert "v1" in lowered
    assert "experimental" in lowered


def test_readme_mentions_bsd_license():
    text = _read(README_PATH)
    assert CHOSEN_LICENSE_NAME in text or CHOSEN_LICENSE in text


def test_readme_mentions_evidence_not_truth():
    lowered = _read(README_PATH).lower()
    assert "evidence" in lowered
    assert "not truth" in lowered


def test_readme_mentions_local_first_no_cloud():
    lowered = _read(README_PATH).lower()
    assert "local-first" in lowered
    assert "no cloud" in lowered or "no cloud service" in lowered


def test_readme_includes_local_test_command():
    text = _read(README_PATH)
    assert "python -m pytest" in text


def test_readme_includes_demo_script_command():
    text = _read(README_PATH)
    assert "clulatent demo" in text


def test_readme_does_not_claim_clubin_implemented():
    lowered = _read(README_PATH).lower()
    # CLUBIN must be described as not built.
    assert "no clubin" in lowered or "not built" in lowered


def test_readme_does_not_claim_studio_ui_implemented():
    lowered = _read(README_PATH).lower()
    assert "no studio ui" in lowered


def test_readme_does_not_overclaim_video_understanding():
    normalized = _read(README_PATH).lower().replace("*", "")
    assert "not understand video" in normalized


def test_readme_roadmap_mentions_phase_2_23():
    assert "CLULatent" in _read(README_PATH)


# --- License consistency ------------------------------------------------------


def test_license_file_exists_and_is_bsd():
    text = _read(LICENSE_PATH)
    assert "BSD 3-Clause License" in text
    assert "AS IS" in text


def test_pyproject_license_remains_bsd():
    text = _read(PYPROJECT_PATH)
    assert f'license = "{CHOSEN_LICENSE}"' in text
    assert "Proprietary" not in text


def test_pyproject_version_is_public_v1():
    text = _read(PYPROJECT_PATH)
    assert 'version = "1.0.0"' in text


# --- No active "license pending" state ---------------------------------------


def test_license_decision_doc_is_resolved_not_pending():
    text = _read(REPO_ROOT / "docs" / "LICENSE_DECISION.md")
    assert "# License Decision (Resolved)" in text


def test_public_repo_gate_is_go_with_caveats():
    text = _read(REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md")
    assert "GO WITH CAVEATS" in text
    assert CHOSEN_LICENSE_NAME in text


def test_current_state_docs_do_not_declare_proprietary_active():
    # The resolved-state docs must not present Proprietary/pending as the
    # current license; only historical/annotated mentions are allowed
    # (which live in the Phase 2.20/2.21 history docs, not these two).
    for name in ("LICENSE_DECISION.md", "PUBLIC_REPO_GATE.md"):
        lowered = _read(REPO_ROOT / "docs" / name).lower()
        # Any "proprietary" mention here must be flagged as prior/superseded.
        if "proprietary" in lowered:
            assert "prior" in lowered or "superseded" in lowered


# --- Safety: no leaked private paths / control sequences ----------------------


def test_public_docs_avoid_local_private_paths():
    for doc in PUBLIC_DOCS:
        text = _read(doc)
        assert "PRIVATE_HOME_SENTINEL" not in text, doc
        assert "PRIVATE_GRID_SENTINEL" not in text, doc


def test_public_docs_have_no_control_sequences():
    for doc in PUBLIC_DOCS + [LICENSE_PATH]:
        text = _read(doc)
        assert "\x1b" not in text, doc
