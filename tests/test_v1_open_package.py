"""Phase 3.25: the V1 open-package front door (`clu_latent.v1_open_package`).

`clulatent open PACKAGE` composes existing V1 read surfaces -- the Phase
3.23 evidence playback viewer and the Phase 3.24 budgeted agent-read model
-- into one command, and reports whether the package's built-in Phase 3.22
index and Phase 3.24 agent-read index are present. It is read-only with
respect to the package: it never mutates it, writes no receipt, never
refreshes a package-internal index, and only ever writes to an external
output directory (never inside the package). A failed browser launch is
reported, never fatal.

Fixtures are built by the conformance fixture builder (a genuine package
with real keyframe/audio tracks and a playable stub source file); no
FFmpeg/Pillow/model, no big real video, no real browser launch.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import v1_agent_read_model as agent_read_mod
from clu_latent import v1_package_index as v1_package_index_mod
from clu_latent import v1_open_package as open_mod
from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.agent_review_writer import run_agent_review
from clu_latent.v1_open_package import (
    DEFAULT_BUDGET,
    OpenPackageError,
    OpenPackagePlan,
    OpenPackageResult,
    build_open_package_plan,
    open_package_surface,
    render_open_package_summary,
    write_open_package_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_open", CONFORMANCE_DIR / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def _built_package(tmp_path: Path, *, with_evidence: bool = True) -> Path:
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "p.clulatent")
    if with_evidence:
        build_evidence_bundle(
            pkg, start_ms=0, end_ms=5000, tool_name=TOOL_NAME, tool_version=TOOL_VERSION
        )
        run_agent_review(pkg, _BUNDLE_ID, tool_name=TOOL_NAME, tool_version=TOOL_VERSION)
    return pkg


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _fake_launcher(calls: list[str], *, succeed: bool = True):
    def launcher(url: str) -> bool:
        calls.append(url)
        return succeed

    return launcher


def _fail_launcher(message: str):
    def launcher(url: str) -> bool:
        raise RuntimeError(message)

    return launcher


# --- 1. plan: basic shape ----------------------------------------------------


def test_plan_has_expected_fields(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert isinstance(plan, OpenPackagePlan)
    assert plan.package_path == pkg
    assert plan.output_dir == tmp_path / "open"
    assert plan.viewer_path == tmp_path / "open" / "viewer.html"
    assert plan.agent_read_path == tmp_path / "open" / f"agent_read_{DEFAULT_BUDGET}.md"
    assert plan.summary_path == tmp_path / "open" / "open_summary.md"
    assert plan.duration_ms == 5000
    assert plan.validation_valid is True
    assert plan.track_count > 0


def test_plan_is_read_only(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert _snapshot(pkg) == before


def test_plan_rejects_missing_package(tmp_path: Path) -> None:
    with pytest.raises(OpenPackageError):
        build_open_package_plan(tmp_path / "does-not-exist")


def test_plan_rejects_unsupported_budget(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    with pytest.raises(OpenPackageError):
        build_open_package_plan(pkg, budget="giant")


# --- 2. default output directory ---------------------------------------------


def test_default_output_dir_is_external_and_deterministic(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan_a = build_open_package_plan(pkg)
    plan_b = build_open_package_plan(pkg)
    assert plan_a.output_dir == plan_b.output_dir
    # Never inside the package.
    assert pkg not in plan_a.output_dir.parents
    assert plan_a.output_dir != pkg


def test_default_output_dir_differs_between_packages(tmp_path: Path) -> None:
    pkg_a = build.build_fixture("valid_keyframes_audio", tmp_path / "a.clulatent")
    pkg_b = build.build_fixture("valid_keyframes_audio", tmp_path / "b.clulatent")
    plan_a = build_open_package_plan(pkg_a)
    plan_b = build_open_package_plan(pkg_b)
    assert plan_a.output_dir != plan_b.output_dir


def test_output_dir_override_is_used_verbatim(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "custom-open-dir"
    plan = build_open_package_plan(pkg, output_dir=out)
    assert plan.output_dir == out


# --- 3. artifact generation ---------------------------------------------------


def test_write_creates_viewer_agent_read_and_summary(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    plan = build_open_package_plan(pkg, output_dir=out)
    result = write_open_package_artifacts(plan)
    assert isinstance(result, OpenPackageResult)
    assert plan.viewer_path.is_file()
    assert plan.agent_read_path.is_file()
    assert plan.summary_path.is_file()
    assert result.viewer_written and not result.viewer_reused
    assert plan.viewer_path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert "CLULatent" in plan.agent_read_path.read_text(encoding="utf-8")
    assert "CLULatent open summary" in plan.summary_path.read_text(encoding="utf-8")


def test_artifacts_are_written_outside_the_package(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    plan = build_open_package_plan(pkg, output_dir=out)
    write_open_package_artifacts(plan)
    for path in (plan.viewer_path, plan.agent_read_path, plan.summary_path):
        assert pkg not in path.parents


def test_write_does_not_mutate_package(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    write_open_package_artifacts(plan)
    assert _snapshot(pkg) == before


def test_write_creates_no_receipts(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    receipts_dir = pkg / "receipts"
    before = set(receipts_dir.rglob("*")) if receipts_dir.exists() else set()
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    write_open_package_artifacts(plan)
    after = set(receipts_dir.rglob("*")) if receipts_dir.exists() else set()
    assert after == before


def test_write_does_not_touch_lock_state(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    lock_dir = pkg / "lock"
    before = set(lock_dir.rglob("*")) if lock_dir.exists() else set()
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    write_open_package_artifacts(plan)
    after = set(lock_dir.rglob("*")) if lock_dir.exists() else set()
    assert after == before


def test_write_does_not_refresh_package_internal_index(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    assert not v1_package_index_mod.v1_index_exists(pkg)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    write_open_package_artifacts(plan)
    assert not v1_package_index_mod.v1_index_exists(pkg)
    assert not (pkg / agent_read_mod.INDEX_DIR / agent_read_mod.MANIFEST).exists()


# --- 4. package/index status reporting ---------------------------------------


def test_reports_missing_indexes_honestly(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert plan.package_index_present is False
    assert plan.agent_read_index_present is False


def test_reports_present_indexes(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    v1_package_index_mod.write_v1_package_index(pkg)
    agent_read_mod.write_agent_read_artifacts(pkg)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert plan.package_index_present is True
    assert plan.agent_read_index_present is True


# --- 5. media handling ---------------------------------------------------------


def test_media_playable_reported(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert plan.media_playable is True


def test_media_missing_degrades_gracefully(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    (pkg / "sources" / "source.bin").unlink()
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    assert plan.media_playable is False
    result = write_open_package_artifacts(plan)
    assert result.viewer_written
    assert "not directly playable" in plan.viewer_path.read_text(encoding="utf-8")


# --- 6. --max-events reaches the playback viewer ------------------------------


def test_max_events_reaches_viewer(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open", max_events=1)
    assert plan.timeline_shown == 1
    assert plan.timeline_total > 1


# --- 7. budgets ----------------------------------------------------------------


@pytest.mark.parametrize("budget", ["micro", "summary", "standard", "full"])
def test_all_budgets_produce_a_plan(tmp_path: Path, budget: str) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open", budget=budget)
    assert plan.budget == budget
    assert plan.agent_read_path.name == f"agent_read_{budget}.md"
    assert plan.agent_read_markdown


# --- 8. force / reuse / collision behaviour ------------------------------------


def test_rerun_without_force_reuses_identical_artifacts(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    plan = build_open_package_plan(pkg, output_dir=out)
    write_open_package_artifacts(plan)
    plan2 = build_open_package_plan(pkg, output_dir=out)
    result2 = write_open_package_artifacts(plan2)
    assert result2.viewer_reused
    assert result2.agent_read_reused
    assert result2.summary_reused
    assert not result2.viewer_written
    assert not result2.agent_read_written
    assert not result2.summary_written


def test_rerun_without_force_refuses_to_overwrite_different_content(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    plan = build_open_package_plan(pkg, output_dir=out)
    write_open_package_artifacts(plan)
    plan.viewer_path.write_text("<!DOCTYPE html><html>different</html>", encoding="utf-8")
    plan2 = build_open_package_plan(pkg, output_dir=out)
    with pytest.raises(OpenPackageError):
        write_open_package_artifacts(plan2)


def test_force_overwrites_existing_artifacts(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    plan = build_open_package_plan(pkg, output_dir=out)
    write_open_package_artifacts(plan)
    plan.viewer_path.write_text("<!DOCTYPE html><html>different</html>", encoding="utf-8")
    plan2 = build_open_package_plan(pkg, output_dir=out)
    result2 = write_open_package_artifacts(plan2, force=True)
    assert result2.viewer_written
    assert plan.viewer_path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert "different" not in plan.viewer_path.read_text(encoding="utf-8")


def test_output_path_directory_collision_raises(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "open"
    out.mkdir(parents=True)
    (out / "viewer.html").mkdir()
    plan = build_open_package_plan(pkg, output_dir=out)
    with pytest.raises(OpenPackageError):
        write_open_package_artifacts(plan)


# --- 9. browser opening ---------------------------------------------------------


def test_no_browser_skips_launch(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    calls: list[str] = []
    result = open_package_surface(
        result, launch_browser=False, launcher=_fake_launcher(calls)
    )
    assert calls == []
    assert result.browser_attempted is False


def test_browser_launch_success(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    calls: list[str] = []
    result = open_package_surface(result, launcher=_fake_launcher(calls, succeed=True))
    assert result.browser_attempted is True
    assert result.browser_opened is True
    assert result.browser_error is None
    assert len(calls) == 1
    assert calls[0].startswith("file://")


def test_browser_launch_failure_is_reported_not_fatal(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    result = open_package_surface(result, launcher=_fake_launcher([], succeed=False))
    assert result.browser_attempted is True
    assert result.browser_opened is False
    assert result.browser_error is not None
    # The viewer path is still available/printed even though launch failed.
    summary = render_open_package_summary(result)
    assert str(plan.viewer_path) in summary


def test_browser_launch_exception_is_caught(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    result = open_package_surface(result, launcher=_fail_launcher("boom"))
    assert result.browser_attempted is True
    assert result.browser_opened is False
    assert "boom" in result.browser_error


# --- 10. rendered summary content ----------------------------------------------


def test_render_open_package_summary_is_compact_and_has_facts(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    text = render_open_package_summary(result)
    assert plan.package_id in text
    assert "duration_ms: 5000" in text
    assert "next steps" in text
    # Compact: not a full JSON dump.
    assert '"schema_id"' not in text


def test_render_suggests_index_regeneration_when_missing(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    result = write_open_package_artifacts(plan)
    text = render_open_package_summary(result)
    assert "package-index refresh" in text
    assert "agent-read write-index" in text


# --- 11. safe-language guard on authored open-summary prose --------------------


def test_open_summary_markdown_contains_no_forbidden_phrase(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    lowered = plan.summary_markdown.lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in lowered


def test_opaque_source_filename_with_scary_word_is_never_censored(tmp_path: Path) -> None:
    pkg = build.build_fixture("valid_minimal", tmp_path / "scary.clulatent")
    manifest_path = pkg / "manifest.json"
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        text.replace('"source.bin"', '"the_video_shows_intent.bin"'), encoding="utf-8"
    )
    plan = build_open_package_plan(pkg, output_dir=tmp_path / "open")
    # Opaque data must be preserved verbatim, never rejected/rewritten.
    assert plan.source_filename == "the_video_shows_intent.bin"


# --- 12. CLI wiring --------------------------------------------------------------


def test_cli_open_help_registered() -> None:
    result = runner.invoke(app, ["open", "--help"])
    assert result.exit_code == 0
    assert "output-dir" in result.output
    assert "no-browser" in result.output
    assert "budget" in result.output
    assert "max-events" in result.output


def test_cli_open_writes_artifacts_and_reports_status(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "cli-open"
    result = runner.invoke(
        app,
        ["open", str(pkg), "--output-dir", str(out), "--no-browser"],
    )
    assert result.exit_code == 0, result.output
    assert (out / "viewer.html").is_file()
    assert (out / f"agent_read_{DEFAULT_BUDGET}.md").is_file()
    assert (out / "open_summary.md").is_file()
    assert "package_path" in result.output


def test_cli_open_invalid_budget_rejected() -> None:
    result = runner.invoke(app, ["open", "/tmp/does-not-matter", "--budget", "bogus"])
    assert result.exit_code != 0


def test_cli_open_missing_package_fails_cleanly(tmp_path: Path) -> None:
    result = runner.invoke(app, ["open", str(tmp_path / "nope"), "--no-browser"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_cli_open_does_not_mutate_package(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    result = runner.invoke(
        app,
        ["open", str(pkg), "--output-dir", str(tmp_path / "cli-open2"), "--no-browser"],
    )
    assert result.exit_code == 0, result.output
    assert _snapshot(pkg) == before


def test_cli_open_max_events_option(tmp_path: Path) -> None:
    pkg = _built_package(tmp_path)
    out = tmp_path / "cli-open3"
    result = runner.invoke(
        app,
        ["open", str(pkg), "--output-dir", str(out), "--no-browser", "--max-events", "1"],
    )
    assert result.exit_code == 0, result.output
    html = (out / "viewer.html").read_text(encoding="utf-8")
    assert '"shown": 1' in html
    assert '"truncated": true' in html


def test_cli_open_force_flag_present() -> None:
    result = runner.invoke(app, ["open", "--help"])
    assert "--force" in result.output
