"""Phase 3.20: the V1 "open and ask" demo kit (`clu_latent.v1_open_ask`).

These tests pin the two Phase 3.20 deliverables built on top of the Phase
3.19 agent-context layer:

- `build_evidence_summary` — a short, human-readable, *evidence-only*
  summary of a package (package facts, tracks present, candidate-evidence
  counts, evidence bundle / agent review IDs, unavailable evidence,
  caveats, safe next steps) that never makes a semantic claim.
- `build_ask_prompt` — a ready-to-paste prompt that instructs a downstream
  agent to answer strictly from evidence, cite IDs/timestamps, and refuse
  to infer people/objects/actions/intent/identity/scene meaning.

Both must go through the read-only agent-context path (which opens the
package via the Phase 3.18 reader), and neither may mutate the package or
write a receipt. Fixtures are built by the conformance fixture builder plus
the real evidence-bundle / agent-review writers — a genuine package with
real tracks, an evidence bundle, and a review; no FFmpeg / Pillow / model.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import agent_context as agent_context_mod
from clu_latent import v1_open_ask as v1_open_ask_mod
from clu_latent.agent_context import build_agent_context
from clu_latent.agent_review_writer import run_agent_review
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.v1_open_ask import (
    OpenAskError,
    SUMMARY_FORBIDDEN_PHRASES,
    build_ask_prompt,
    build_evidence_summary,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_openask", CONFORMANCE_DIR / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def _built_package(tmp_path: Path, *, with_evidence: bool = True) -> Path:
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "p.clulatent")
    if with_evidence:
        build_evidence_bundle(
            pkg, start_ms=0, end_ms=5000, tool_name=TOOL_NAME, tool_version=TOOL_VERSION
        )
        run_agent_review(pkg, _BUNDLE_ID, tool_name=TOOL_NAME, tool_version=TOOL_VERSION)
    return pkg


# --- 1. summary: works from a valid package + carries the core facts -------


def test_summary_from_valid_package(tmp_path):
    summary = build_evidence_summary(_built_package(tmp_path))
    assert "CLULatent evidence summary" in summary
    assert "Package facts:" in summary
    assert "valid" in summary


def test_summary_includes_tracks_and_event_counts(tmp_path):
    summary = build_evidence_summary(_built_package(tmp_path))
    assert "Tracks present" in summary
    # every core lane appears with a status line
    for lane in agent_context_mod.CORE_LANES:
        assert lane in summary


def test_summary_includes_evidence_bundle_ids(tmp_path):
    pkg = _built_package(tmp_path)
    ctx = build_agent_context(pkg)
    bundle_ids = [b["id"] for b in ctx["evidence"].get("evidence_bundles", [])]
    assert bundle_ids  # sanity: the fixture built one
    summary = build_evidence_summary(pkg)
    assert "Evidence bundle IDs" in summary
    for bid in bundle_ids:
        assert bid in summary


def test_summary_includes_agent_review_ids(tmp_path):
    pkg = _built_package(tmp_path)
    ctx = build_agent_context(pkg)
    review_ids = [r["id"] for r in ctx["evidence"].get("agent_reviews", [])]
    assert review_ids  # sanity: the fixture ran one review
    summary = build_evidence_summary(pkg)
    assert "Agent review IDs" in summary
    for rid in review_ids:
        assert rid in summary


def test_summary_includes_caveats(tmp_path):
    summary = build_evidence_summary(_built_package(tmp_path))
    assert "Caveats:" in summary
    assert "Safe next steps:" in summary
    # caveats section is non-empty (carried from the agent context)
    caveat_block = summary.split("Caveats:", 1)[1]
    assert "- " in caveat_block


# --- 2. summary: never emits forbidden semantic language -------------------


def test_summary_no_forbidden_semantic_language(tmp_path):
    # a package whose source is named to try to trip the guard on opaque data
    pkg = _built_package(tmp_path)
    summary = build_evidence_summary(pkg)
    lowered = summary.lower()
    for phrase in SUMMARY_FORBIDDEN_PHRASES:
        assert phrase not in lowered, f"summary leaked forbidden phrase {phrase!r}"


def test_summary_without_evidence_reports_unavailable(tmp_path):
    summary = build_evidence_summary(_built_package(tmp_path, with_evidence=False))
    assert "Unavailable / missing evidence:" in summary
    assert "evidence_bundles" in summary
    # no bundle / review IDs to show
    assert "Evidence bundle IDs (0):" in summary
    assert "Agent review IDs (0):" in summary


# --- 3. prompt: works + carries grounding + forbidden-claim instructions ---


def test_prompt_from_valid_package(tmp_path):
    prompt = build_ask_prompt(_built_package(tmp_path))
    assert "CLULATENT ASK PROMPT" in prompt
    assert "BEGIN CLULATENT CONTEXT" in prompt
    assert "END CLULATENT CONTEXT" in prompt


def test_prompt_includes_evidence_grounding(tmp_path):
    prompt = build_ask_prompt(_built_package(tmp_path)).lower()
    assert "using only the" in prompt
    assert "cite" in prompt
    assert "unsupported by available evidence" in prompt


def test_prompt_prohibits_semantic_inference(tmp_path):
    prompt = build_ask_prompt(_built_package(tmp_path)).lower()
    assert "do not infer" in prompt
    for concept in ("people", "objects", "actions", "intent", "identity", "scene meaning"):
        assert concept in prompt


def test_prompt_embeds_package_facts(tmp_path):
    pkg = _built_package(tmp_path)
    ctx = build_agent_context(pkg)
    prompt = build_ask_prompt(pkg)
    assert ctx["package"]["package_id"] in prompt


# --- 4. read-only: no mutation, no receipt, goes through the reader --------


def test_summary_does_not_mutate_package(tmp_path):
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_evidence_summary(pkg)
    after = _snapshot(pkg)
    assert before == after


def test_prompt_does_not_mutate_package(tmp_path):
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_ask_prompt(pkg)
    after = _snapshot(pkg)
    assert before == after


def test_summary_creates_no_receipt(tmp_path):
    pkg = _built_package(tmp_path)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    build_evidence_summary(pkg)
    receipts_after = sorted((pkg / "receipts").glob("*.jsonl"))
    assert receipts_before == receipts_after


def test_prompt_creates_no_receipt(tmp_path):
    pkg = _built_package(tmp_path)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    build_ask_prompt(pkg)
    receipts_after = sorted((pkg / "receipts").glob("*.jsonl"))
    assert receipts_before == receipts_after


def test_summary_uses_agent_context_and_reader(tmp_path, monkeypatch):
    pkg = _built_package(tmp_path)
    calls: list[str] = []
    real_open = agent_context_mod.package_reader_mod.open_package

    def spy_open(*a, **k):
        calls.append("open_package")
        return real_open(*a, **k)

    monkeypatch.setattr(agent_context_mod.package_reader_mod, "open_package", spy_open)
    build_evidence_summary(pkg)
    assert calls == ["open_package"]


# --- 5. errors --------------------------------------------------------------


def test_summary_missing_package_raises(tmp_path):
    with pytest.raises(OpenAskError):
        build_evidence_summary(tmp_path / "does_not_exist.clulatent")


def test_prompt_missing_package_raises(tmp_path):
    with pytest.raises(OpenAskError):
        build_ask_prompt(tmp_path / "does_not_exist.clulatent")


# --- 6. CLI wiring ----------------------------------------------------------


def test_cli_summarize_help():
    result = runner.invoke(app, ["agent-context", "summarize", "--help"])
    assert result.exit_code == 0
    assert "evidence-only" in result.stdout


def test_cli_prompt_help():
    result = runner.invoke(app, ["agent-context", "prompt", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.stdout


def test_cli_summarize_runs(tmp_path):
    pkg = _built_package(tmp_path)
    result = runner.invoke(app, ["agent-context", "summarize", str(pkg)])
    assert result.exit_code == 0
    assert "CLULatent evidence summary" in result.stdout


def test_cli_prompt_writes_file(tmp_path):
    pkg = _built_package(tmp_path)
    out = tmp_path / "ask_prompt.md"
    result = runner.invoke(app, ["agent-context", "prompt", str(pkg), "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "CLULATENT ASK PROMPT" in out.read_text(encoding="utf-8")
