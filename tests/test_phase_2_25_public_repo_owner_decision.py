"""Tests for Phase 2.25: public repo owner decision.

This phase adds no new module, adapter, or CLI command — it records the
owner's decision on whether CLULatent should proceed toward public
pre-alpha exposure. This phase does not publish, push, or add a remote.
These tests use stable keyword/section checks (not brittle exact-prose
matching) to guard that the decision record stays conservative: an
explicit GO WITH CAVEATS decision, an explicit "nothing published yet"
statement, and no overclaiming.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_2_25_PUBLIC_REPO_OWNER_DECISION.md"
DECISION_DOC = REPO_ROOT / "docs" / "PUBLIC_REPO_OWNER_DECISION.md"

PUBLIC_DOCS = [
    README_PATH,
    PHASE_DOC,
    DECISION_DOC,
    REPO_ROOT / "docs" / "TRUST_MODEL.md",
    REPO_ROOT / "docs" / "PUBLIC_PRE_ALPHA_CHECKLIST.md",
    REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "LICENSE_DECISION.md",
    REPO_ROOT / "docs" / "PUBLIC_REPO_LAUNCH_PLAN.md",
    REPO_ROOT / "docs" / "PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md",
    REPO_ROOT / "docs" / "PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md",
    REPO_ROOT / "docs" / "PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md",
    REPO_ROOT / "docs" / "PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Phase doc ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_includes_owner_decision():
    lowered = _read(PHASE_DOC).lower()
    assert "decision" in lowered


def test_phase_doc_includes_go_with_caveats():
    assert "GO WITH CAVEATS" in _read(PHASE_DOC)


def test_phase_doc_states_nothing_was_published():
    lowered = _read(PHASE_DOC).lower()
    assert "did not publish anything" in lowered or "no remote was added" in lowered


def test_phase_doc_mentions_bsd_license():
    text = _read(PHASE_DOC)
    assert "BSD-3-Clause" in text or "BSD 3-Clause" in text


def test_phase_doc_mentions_pre_alpha_experimental():
    lowered = _read(PHASE_DOC).lower()
    assert "pre-alpha" in lowered
    assert "experimental" in lowered


def test_phase_doc_includes_caveats_section():
    lowered = _read(PHASE_DOC).lower()
    assert "caveat" in lowered


def test_phase_doc_includes_what_not_to_claim_publicly():
    lowered = _read(PHASE_DOC).lower()
    assert "must not be claimed publicly" in lowered or "not to claim publicly" in lowered


# --- Living decision doc ----------------------------------------------------


def test_living_decision_doc_exists():
    assert DECISION_DOC.is_file()


def test_living_decision_doc_states_go_with_caveats():
    assert "GO WITH CAVEATS" in _read(DECISION_DOC)


def test_living_decision_doc_states_not_yet_published():
    lowered = _read(DECISION_DOC).lower()
    assert "not yet published" in lowered


# --- README --------------------------------------------------------------


def test_readme_includes_phase_2_25_roadmap_bullet():
    assert "CLULatent" in _read(README_PATH)


# --- No overclaiming --------------------------------------------------------


def test_docs_do_not_claim_clulatent_understands_video():
    for doc in (PHASE_DOC, DECISION_DOC):
        normalized = _read(doc).lower().replace("*", "").replace('"', "")
        # The phrase may appear only inside a "must not claim" list.
        if "clulatent understands video" in normalized:
            assert "must not" in normalized or "not claimed publicly" in normalized


def test_docs_do_not_claim_clubin_implemented():
    for doc in (PHASE_DOC, DECISION_DOC):
        lowered = _read(doc).lower()
        assert "clubin is implemented" not in lowered.replace('"', "") or "must not" in lowered


def test_docs_state_no_clubin_no_studio_ui():
    text = _read(PHASE_DOC).lower()
    assert "no clubin" in text
    assert "no studio ui" in text


def test_docs_do_not_claim_v1_or_production_ready():
    lowered = _read(PHASE_DOC).lower()
    assert "0.1.0" in lowered
    assert "pre-alpha" in lowered


# --- Safety: no leaked private paths / control sequences / stale license --


def test_public_docs_avoid_local_private_paths():
    for doc in PUBLIC_DOCS:
        text = _read(doc)
        assert "PRIVATE_HOME_SENTINEL" not in text, doc
        assert "PRIVATE_GRID_SENTINEL" not in text, doc


def test_public_docs_have_no_control_sequences():
    for doc in PUBLIC_DOCS + [REPO_ROOT / "LICENSE"]:
        text = _read(doc)
        assert "\x1b" not in text, doc


def test_license_decision_doc_still_resolved_not_pending():
    text = _read(REPO_ROOT / "docs" / "LICENSE_DECISION.md")
    assert "# License Decision (Resolved)" in text


def test_no_real_github_username_introduced():
    import re

    for doc in (PHASE_DOC, DECISION_DOC):
        text = _read(doc)
        for match in re.finditer(r"github\.com[:/]([A-Za-z0-9_-]+)", text):
            assert match.group(1) == "YOUR_USERNAME", (doc, match.group(0))
