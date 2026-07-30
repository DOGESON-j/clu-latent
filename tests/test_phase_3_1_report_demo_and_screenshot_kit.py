"""Tests for Phase 3.1: report demo and screenshot kit.

This phase adds no new module or CLI command. It records how the
report screenshot kit distinguishes a clean PASS demo report
(recommended for public screenshots) from an optional FAIL
demo report (documented only as a trust-model example), and ensures
generated demo packages/reports stay local-only and are never
committed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_1_REPORT_DEMO_AND_SCREENSHOT_KIT.md"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"

GENERATED_REPORT_NAMES = ["report.html", "report-transcribed.html", "report-demo.html"]


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


def _git_tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


# --- Phase doc ---------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_documents_pass_report_as_recommended_screenshot():
    lowered = _read_normalized(PHASE_DOC).lower()
    assert "pass" in lowered
    assert "recommended public screenshot" in lowered


def test_phase_doc_documents_fail_report_as_optional_trust_model_example():
    lowered = _read_normalized(PHASE_DOC).lower()
    assert "trust-model example" in lowered
    assert "not the public default" in lowered or "optional" in lowered


def test_phase_doc_states_generated_artifacts_must_not_be_committed():
    lowered = _read_normalized(PHASE_DOC).lower()
    assert "not committed" in lowered or "must not be committed" in lowered


def test_phase_doc_lists_expected_generated_artifact_names():
    text = _read(PHASE_DOC)
    for name in ("sample.clulatent", "sample-transcribed.clulatent", "report.html", "report-transcribed.html"):
        assert name in text


# --- .gitignore ----------------------------------------------------------------


def test_gitignore_covers_generated_report_html_patterns():
    text = _read(GITIGNORE_PATH)
    assert "report.html" in text
    assert "report-transcribed.html" in text
    assert "report*.html" in text


def test_gitignore_covers_generated_clulatent_packages():
    text = _read(GITIGNORE_PATH)
    assert "*.clulatent/" in text


# --- No generated artifacts tracked by git --------------------------------------


def test_no_generated_report_html_files_are_tracked():
    tracked = _git_tracked_files()
    for name in GENERATED_REPORT_NAMES:
        assert name not in tracked, f"{name} must not be committed"
    for path in tracked:
        assert not (path.startswith("report") and path.endswith(".html")), path


def test_no_generated_clulatent_packages_are_tracked():
    tracked = _git_tracked_files()
    for path in tracked:
        assert ".clulatent/" not in path, path


# --- README ------------------------------------------------------------------


def test_readme_includes_phase_3_1_roadmap_bullet():
    assert "CLULatent" in _read(README_PATH)


# --- Safety: no leaked private paths / control sequences ----------------------


def test_phase_doc_avoids_local_private_paths():
    text = _read(PHASE_DOC)
    assert "PRIVATE_HOME_SENTINEL" not in text
    assert "PRIVATE_GRID_SENTINEL" not in text


def test_phase_doc_has_no_control_sequences():
    assert "\x1b" not in _read(PHASE_DOC)
