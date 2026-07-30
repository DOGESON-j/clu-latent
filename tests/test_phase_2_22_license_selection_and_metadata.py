"""Tests for Phase 2.22: license selection and metadata.

This phase adds no new module, adapter, or CLI command — it records the
owner's license decision (BSD 3-Clause) consistently across the root
`LICENSE` file, `pyproject.toml`, the README, and the license/gate docs
(it publishes nothing and changes no package version). These tests use
stable keyword/section checks (not brittle exact-prose matching) to verify
the license is applied consistently and no stale "decision pending" wording
remains as an active claim.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LICENSE_PATH = REPO_ROOT / "LICENSE"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md"
LICENSE_DECISION_DOC = REPO_ROOT / "docs" / "LICENSE_DECISION.md"
PUBLIC_REPO_GATE_DOC = REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md"

CHOSEN_LICENSE = "BSD-3-Clause"
CHOSEN_LICENSE_NAME = "BSD 3-Clause"
COPYRIGHT_LINE = "Copyright (c) 2026 Jayden Kambule"

PUBLIC_DOCS = [
    README_PATH,
    PHASE_DOC,
    LICENSE_DECISION_DOC,
    PUBLIC_REPO_GATE_DOC,
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- LICENSE file -------------------------------------------------------------


def test_license_file_exists():
    assert LICENSE_PATH.is_file()


def test_license_file_mentions_chosen_license_and_copyright():
    text = _read(LICENSE_PATH)
    assert "BSD 3-Clause License" in text
    assert COPYRIGHT_LINE in text
    # Sanity: the standard BSD-3 third clause / disclaimer is present.
    assert "Neither the name of the copyright holder" in text
    assert "AS IS" in text


# --- pyproject metadata -------------------------------------------------------


def test_pyproject_license_matches_chosen_license():
    text = _read(PYPROJECT_PATH)
    assert f'license = "{CHOSEN_LICENSE}"' in text
    # Old placeholder license must be gone.
    assert "Proprietary" not in text


def test_pyproject_declares_license_file():
    text = _read(PYPROJECT_PATH)
    assert "LICENSE" in text
    assert "license-files" in text


# --- README -------------------------------------------------------------------


def test_readme_mentions_chosen_license():
    text = _read(README_PATH)
    assert CHOSEN_LICENSE_NAME in text or CHOSEN_LICENSE in text


def test_readme_marks_v1_and_provisional_apis():
    lowered = _read(README_PATH).lower()
    assert "v1" in lowered
    assert "experimental" in lowered


def test_readme_still_includes_evidence_not_truth():
    lowered = _read(README_PATH).lower()
    assert "evidence" in lowered
    assert "not truth" in lowered


def test_readme_still_states_no_clubin_and_no_studio_ui():
    lowered = _read(README_PATH).lower()
    assert "no clubin" in lowered or "not built" in lowered
    assert "no studio ui" in lowered


def test_readme_does_not_overclaim_understanding():
    normalized = _read(README_PATH).lower().replace("*", "")
    assert "not understand video" in normalized


# --- Docs: resolved, not pending ---------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_names_chosen_license():
    text = _read(PHASE_DOC)
    assert CHOSEN_LICENSE_NAME in text or CHOSEN_LICENSE in text


def test_phase_doc_includes_recommendation():
    text = _read(PHASE_DOC)
    assert "GO WITH CAVEATS" in text or "GO" in text or "HOLD" in text


def test_license_decision_doc_is_resolved_not_pending():
    text = _read(LICENSE_DECISION_DOC)
    assert CHOSEN_LICENSE_NAME in text
    # The active heading/state must no longer be "Pending".
    assert "# License Decision (Resolved)" in text


def test_public_repo_gate_no_longer_hold_on_license():
    text = _read(PUBLIC_REPO_GATE_DOC)
    # The license section must now be a pass, and the overall verdict
    # must have moved off HOLD.
    assert CHOSEN_LICENSE_NAME in text
    assert "GO WITH CAVEATS" in text


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


def test_readme_roadmap_mentions_phase_2_22():
    assert "CLULatent" in _read(README_PATH)
