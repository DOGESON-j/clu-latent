"""Phase 3.19: the agent-context export (`clu_latent.agent_context`).

These tests pin the stable shape of the `clulatent.agent_context.v0`
document and the guarantees it must keep for a downstream agent: a stable
schema envelope, every documented section present, a non-empty `caveats`
section, safe `next_steps`, no forbidden semantic language, a working
Markdown render, and — crucially — that building the context is strictly
read-only (no mutation, no receipt written) and goes through the Phase
3.18 reader rather than walking the folder tree.

Fixtures are built by the conformance fixture builder plus the real
evidence-bundle / agent-review writers, so the context is exercised
against a genuine package with real tracks, an evidence bundle, and a
review — no FFmpeg / Pillow / model.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from clu_latent import agent_context as agent_context_mod
from clu_latent import package_reader as package_reader_mod
from clu_latent.agent_context import (
    AGENT_CONTEXT_SCHEMA_ID,
    AGENT_CONTEXT_SCHEMA_VERSION,
    FORBIDDEN_CONTEXT_PHRASES,
    AgentContextError,
    assert_no_forbidden_language,
    build_agent_context,
    context_to_json,
    render_markdown,
)
from clu_latent.agent_review_writer import run_agent_review
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_ctx", CONFORMANCE_DIR / "build.py"
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


# --- 1. stable envelope + all sections present ------------------------------


def test_schema_envelope_stable(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    assert ctx["schema_id"] == AGENT_CONTEXT_SCHEMA_ID == "clulatent.agent_context.v0"
    assert ctx["schema_version"] == AGENT_CONTEXT_SCHEMA_VERSION == "0.1.0"
    assert ctx["generated_by"]


def test_all_sections_present(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    for key in (
        "package",
        "validation",
        "lock",
        "tracks",
        "track_summary",
        "event_counts",
        "time_coverage",
        "receipts",
        "evidence",
        "timeline",
        "timeline_truncated",
        "unavailable_evidence",
        "caveats",
        "next_steps",
    ):
        assert key in ctx, f"missing section {key!r}"

    pkg = ctx["package"]
    assert pkg["package_id"]
    assert pkg["source"]["filename"]
    assert pkg["source"]["sha256"]
    assert pkg["duration_ms"] == 5000


def test_validation_and_event_counts(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    assert ctx["validation"]["valid"] is True
    assert ctx["validation"]["error_count"] == 0
    # keyframes + audio_events + evidence_bundles + agent_review_events
    assert ctx["event_counts"]["keyframes"] == 2
    assert ctx["event_counts"]["audio_events"] == 2
    assert ctx["event_counts"]["evidence_bundles"] == 1
    assert ctx["event_counts"]["agent_review_events"] == 1
    assert ctx["track_summary"]["total_events"] == 6


# --- 2. evidence section reflects the real bundle + review ------------------


def test_evidence_section(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    evidence = ctx["evidence"]
    assert len(evidence["evidence_bundles"]) == 1
    bundle = evidence["evidence_bundles"][0]
    assert bundle["id"] == _BUNDLE_ID
    assert bundle["evidence_counts"]["keyframes"] == 2
    assert len(evidence["agent_reviews"]) == 1
    review = evidence["agent_reviews"][0]
    assert review["evidence_bundle_id"] == _BUNDLE_ID
    assert review["review_status"] == "bundle_valid"


def test_unavailable_evidence_lists_absent_lanes(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    # this fixture has no visual/changed-region/speech lanes
    assert "visual_change_candidates" in ctx["unavailable_evidence"]
    assert "changed_region_candidates" in ctx["unavailable_evidence"]
    # present lanes are not listed as unavailable
    assert "keyframes" not in ctx["unavailable_evidence"]
    assert "evidence_bundles" not in ctx["unavailable_evidence"]


# --- 3. caveats + next steps + forbidden-language guard ---------------------


def test_caveats_and_next_steps_present(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    assert len(ctx["caveats"]) >= 3
    assert ctx["next_steps"]
    joined = " ".join(ctx["caveats"]).lower()
    assert "candidate evidence only" in joined
    assert "human review" in joined


def test_no_forbidden_language_in_full_document(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    blob = json.dumps(ctx).lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in blob, f"context leaked forbidden phrase {phrase!r}"


def test_guard_rejects_smuggled_semantic_prose():
    poisoned = {
        "caveats": ["this means the person appeared"],
        "next_steps": [],
        "generated_by": "clulatent 0.1.0",
    }
    with pytest.raises(AgentContextError):
        assert_no_forbidden_language(poisoned)


def test_guard_ignores_user_controlled_data(tmp_path):
    # A forbidden word inside opaque data (e.g. a source filename) must not
    # trip the guard, which only scans authored prose.
    clean = {
        "caveats": list(agent_context_mod.CONTEXT_CAVEATS),
        "next_steps": ["Use event_counts and time_coverage to decide where a human should look."],
        "generated_by": "clulatent 0.1.0",
        "package": {"source": {"filename": "the_truth_about_intent_and_identity.mp4"}},
    }
    assert_no_forbidden_language(clean)  # does not raise


# --- 4. markdown render + json serialization --------------------------------


def test_markdown_render(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    md = render_markdown(ctx)
    assert md.startswith("# Agent context")
    assert "## Caveats" in md
    assert "## Suggested next steps" in md
    assert _BUNDLE_ID in md


def test_json_serialization_roundtrips(tmp_path):
    ctx = build_agent_context(_built_package(tmp_path))
    text = context_to_json(ctx)
    assert json.loads(text) == ctx


# --- 5. read-only: no mutation, no receipt, uses the reader -----------------


def test_export_does_not_mutate_package(tmp_path):
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_agent_context(pkg)
    after = _snapshot(pkg)
    assert before == after


def test_export_creates_no_receipt(tmp_path):
    pkg = _built_package(tmp_path)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    build_agent_context(pkg)
    receipts_after = sorted((pkg / "receipts").glob("*.jsonl"))
    assert receipts_before == receipts_after


def test_build_uses_the_reader(tmp_path, monkeypatch):
    pkg = _built_package(tmp_path)
    calls: list[str] = []
    real_open = package_reader_mod.open_package

    def spy_open(*a, **k):
        calls.append("open_package")
        return real_open(*a, **k)

    monkeypatch.setattr(agent_context_mod.package_reader_mod, "open_package", spy_open)
    build_agent_context(pkg)
    assert calls == ["open_package"]


def test_missing_package_raises(tmp_path):
    with pytest.raises(AgentContextError):
        build_agent_context(tmp_path / "does_not_exist.clulatent")
