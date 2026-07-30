"""Tests for Phase 2.24: public repo launch plan.

This phase adds no new module, adapter, or CLI command — it writes a
conservative, manual public-repository launch plan (checklist + command
templates) for the owner to follow later. This phase does not publish,
push, or add a remote. These tests use stable keyword/section checks (not
brittle exact-prose matching) to guard that the launch plan stays
conservative: placeholder-only remote commands, an explicit warning not to
publish yet, evidence-not-truth framing, and no overclaiming.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_2_24_PUBLIC_REPO_LAUNCH_PLAN.md"
LAUNCH_PLAN_DOC = REPO_ROOT / "docs" / "PUBLIC_REPO_LAUNCH_PLAN.md"

PUBLIC_DOCS = [
    README_PATH,
    PHASE_DOC,
    LAUNCH_PLAN_DOC,
    REPO_ROOT / "docs" / "TRUST_MODEL.md",
    REPO_ROOT / "docs" / "PUBLIC_PRE_ALPHA_CHECKLIST.md",
    REPO_ROOT / "docs" / "PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "LICENSE_DECISION.md",
    REPO_ROOT / "docs" / "PHASE_2_20_PUBLIC_PRE_ALPHA_READINESS.md",
    REPO_ROOT / "docs" / "PHASE_2_21_LICENSE_AND_PUBLIC_REPO_GATE.md",
    REPO_ROOT / "docs" / "PHASE_2_22_LICENSE_SELECTION_AND_METADATA.md",
    REPO_ROOT / "docs" / "PHASE_2_23_PUBLIC_REPO_FINAL_SWEEP.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Phase doc ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_includes_go_with_caveats():
    assert "GO WITH CAVEATS" in _read(PHASE_DOC)


def test_phase_doc_warns_not_to_publish_during_this_phase():
    lowered = _read(PHASE_DOC).lower()
    assert "does not launch" in lowered or "does not itself constitute authorization to publish" in lowered
    assert "no remote was added" in lowered or "did not add" in lowered


# --- Launch plan doc: required sections ------------------------------------


def test_launch_plan_doc_exists():
    assert LAUNCH_PLAN_DOC.is_file()


def test_launch_plan_includes_go_with_caveats():
    assert "GO WITH CAVEATS" in _read(LAUNCH_PLAN_DOC)


def test_launch_plan_includes_pre_launch_checklist():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "pre-launch checklist" in lowered or "before creating the public repo" in lowered


def test_launch_plan_includes_github_repo_setup_checklist():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "creating the repo safely" in lowered or "github repo setup" in lowered


def test_launch_plan_includes_post_push_verification_checklist():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "post-push verification" in lowered


def test_launch_plan_includes_rollback_notes():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "rollback" in lowered and "private" in lowered


def test_launch_plan_includes_placeholder_remote_command_not_real():
    text = _read(LAUNCH_PLAN_DOC)
    assert "git remote add origin" in text
    assert "YOUR_USERNAME" in text


def test_launch_plan_includes_evidence_not_truth():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "evidence" in lowered
    assert "not truth" in lowered


def test_launch_plan_includes_verification_commands():
    text = _read(LAUNCH_PLAN_DOC)
    assert "git status" in text
    assert "python -m pytest" in text


# --- No overclaiming --------------------------------------------------------


def test_launch_plan_lists_forbidden_claims():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "clulatent understands video" in lowered  # listed as a thing to avoid
    assert "clubin is implemented" in lowered
    assert "studio ui exists" in lowered


def test_phase_doc_does_not_claim_clubin_or_studio_implemented():
    lowered = _read(PHASE_DOC).lower()
    assert "no clubin" in lowered or "no studio ui" in lowered


# --- README --------------------------------------------------------------


def test_readme_includes_phase_2_24_roadmap_bullet():
    assert "CLULatent" in _read(README_PATH)


def test_readme_mentions_v1():
    lowered = _read(README_PATH).lower()
    assert "v1" in lowered


def test_readme_still_mentions_bsd_license():
    text = _read(README_PATH)
    assert "BSD 3-Clause" in text or "BSD-3-Clause" in text


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


def test_launch_plan_docs_have_no_real_github_username():
    # Every github.com/<owner> mention in the launch-plan docs must be the
    # literal placeholder, not a real account.
    import re

    for doc in (LAUNCH_PLAN_DOC, PHASE_DOC):
        text = _read(doc)
        for match in re.finditer(r"github\.com[:/]([A-Za-z0-9_-]+)", text):
            assert match.group(1) == "YOUR_USERNAME", (doc, match.group(0))


def test_current_state_docs_do_not_declare_license_pending_active():
    text = _read(REPO_ROOT / "docs" / "LICENSE_DECISION.md")
    assert "# License Decision (Resolved)" in text


def test_launch_plan_does_not_claim_version_1_ready():
    lowered = _read(LAUNCH_PLAN_DOC).lower()
    assert "0.1.0" in lowered
    assert "pre-alpha" in lowered
