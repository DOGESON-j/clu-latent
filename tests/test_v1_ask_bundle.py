"""Phase 3.21: the V1 ask bundle / agent handoff (`clu_latent.v1_ask_bundle`).

These tests pin the Phase 3.21 deliverable built on the Phase 3.19
agent-context layer and the Phase 3.18 reader: `build_ask_bundle(package,
question)` renders a safe, evidence-grounded handoff bundle for a user
question -- the question quoted verbatim, package facts, evidence counts,
a relevant evidence window when the question names a timestamp (resolved
via `PackageReader.query_time`), caveats, and the safe-answering /
forbidden-claim rules an agent must follow.

The bundle must go through the read-only path (never mutate the package,
never write a receipt), must never emit a semantic claim in its *authored*
prose, and must quote a user question verbatim even when the question
itself contains forbidden words (the question is user input, not
CLULatent's claim). Fixtures are built by the conformance fixture builder
plus the real evidence-bundle / agent-review writers -- a genuine package
with real tracks, an evidence bundle, and a review; no FFmpeg/Pillow/model.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import agent_context as agent_context_mod
from clu_latent import v1_ask_bundle as v1_ask_bundle_mod
from clu_latent.agent_context import build_agent_context
from clu_latent.agent_review_writer import run_agent_review
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.v1_ask_bundle import (
    ASK_FORBIDDEN_PHRASES,
    AskBundleError,
    build_ask_bundle,
    classify_question,
    parse_time_query,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_askbundle", CONFORMANCE_DIR / "build.py"
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


# --- 1. tiny timestamp parser ----------------------------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What evidence exists around 00:04?", (2000, 6000)),
        ("at 4s", (2000, 6000)),
        ("4000ms please", (2000, 6000)),
        ("window 0:04-0:10", (4000, 10000)),
        ("no time here", None),
        ("I saw 4 objects", None),  # bare integer is not a timestamp
    ],
)
def test_parse_time_query(question, expected):
    assert parse_time_query(question, duration_ms=31729) == expected


def test_parse_time_query_clamps_to_duration():
    # a window past the end is clamped to [0, duration]
    assert parse_time_query("at 40s", duration_ms=31729) == (31729, 31729)


# --- 2. broad, non-semantic intent classification --------------------------


@pytest.mark.parametrize(
    "question,expected",
    [
        ("What evidence exists around 00:04?", "evidence_near_time"),
        ("what happened?", "unsupported_or_unknown"),
        ("who is in the video?", "unsupported_or_unknown"),
        ("Give me an overview", "package_overview"),
        ("describe the timeline throughout", "timeline_overview"),
        ("random text with no cues", "general_question"),
    ],
)
def test_classify_question(question, expected):
    assert classify_question(question) == expected


# --- 3. bundle works + carries the required content ------------------------


def test_bundle_includes_user_question(tmp_path):
    q = "What evidence exists around 00:04?"
    out = build_ask_bundle(_built_package(tmp_path), q)
    assert "# CLULatent Ask Bundle" in out
    assert q in out


def test_bundle_includes_package_facts(tmp_path):
    pkg = _built_package(tmp_path)
    ctx = build_agent_context(pkg)
    out = build_ask_bundle(pkg, "overview please")
    assert "## Package Facts" in out
    assert ctx["package"]["package_id"] in out
    assert "duration:" in out
    assert "validation:" in out
    assert "lock:" in out


def test_bundle_includes_evidence_counts(tmp_path):
    out = build_ask_bundle(_built_package(tmp_path), "overview please")
    assert "## Track Summary" in out
    assert "## Evidence Available" in out
    for lane in agent_context_mod.CORE_LANES:
        assert lane in out


def test_bundle_includes_caveats(tmp_path):
    out = build_ask_bundle(_built_package(tmp_path), "overview please")
    assert "## Caveats" in out
    caveat_block = out.split("## Caveats", 1)[1]
    assert "- " in caveat_block


def test_bundle_includes_safe_answering_and_forbidden(tmp_path):
    out = build_ask_bundle(_built_package(tmp_path), "overview please")
    assert "## Safe Answering Instructions" in out
    assert "## Forbidden Claims" in out
    assert "## Suggested Answer Format" in out
    lowered = out.lower()
    assert "answer only from the evidence" in lowered
    assert "do not infer or assert" in lowered


def test_bundle_includes_bundle_and_review_ids(tmp_path):
    pkg = _built_package(tmp_path)
    ctx = build_agent_context(pkg)
    bundle_ids = [b["id"] for b in ctx["evidence"].get("evidence_bundles", [])]
    review_ids = [r["id"] for r in ctx["evidence"].get("agent_reviews", [])]
    assert bundle_ids and review_ids  # sanity: fixture built them
    out = build_ask_bundle(pkg, "overview please")
    assert "## Evidence Bundles" in out
    assert "## Agent Reviews" in out
    for bid in bundle_ids:
        assert bid in out
    for rid in review_ids:
        assert rid in out


# --- 4. timestamp window vs. no timestamp ----------------------------------


def test_timestamp_question_includes_nearby_events(tmp_path):
    # the fixture package is 5000 ms long, so the +/-2s window around 00:04
    # (2000-6000) clamps to the package duration (2000-5000).
    out = build_ask_bundle(_built_package(tmp_path), "What evidence exists around 00:04?")
    assert "## Relevant Evidence Window" in out
    assert "requested window: [2000-5000 ms]" in out
    # the audio record at 3000-3500 ms overlaps the window and is listed
    # with its opaque id/track/timestamps only (no interpretation)
    assert "audio_events / ae_000001" in out


def test_no_timestamp_question_still_works(tmp_path):
    out = build_ask_bundle(_built_package(tmp_path), "Give me an overview")
    assert "## Relevant Evidence Window" in out
    assert "no timestamp detected in the question" in out


# --- 5. read-only: no mutation, no receipt, goes through the reader --------


def test_bundle_does_not_mutate_package(tmp_path):
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    build_ask_bundle(pkg, "What evidence exists around 00:04?")
    after = _snapshot(pkg)
    assert before == after


def test_bundle_creates_no_receipt(tmp_path):
    pkg = _built_package(tmp_path)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    build_ask_bundle(pkg, "What evidence exists around 00:04?")
    receipts_after = sorted((pkg / "receipts").glob("*.jsonl"))
    assert receipts_before == receipts_after


def test_bundle_uses_agent_context_and_reader(tmp_path, monkeypatch):
    pkg = _built_package(tmp_path)
    calls: list[str] = []
    real_open = agent_context_mod.package_reader_mod.open_package

    def spy_open(*a, **k):
        calls.append("open_package")
        return real_open(*a, **k)

    # patch the shared reader module both layers import
    monkeypatch.setattr(agent_context_mod.package_reader_mod, "open_package", spy_open)
    monkeypatch.setattr(v1_ask_bundle_mod.package_reader_mod, "open_package", spy_open)
    build_ask_bundle(pkg, "What evidence exists around 00:04?")
    # one open for agent_context facts, one for the query_time window
    assert calls == ["open_package", "open_package"]


# --- 6. safety: authored prose vs. quoted user input -----------------------


def test_authored_prose_has_no_forbidden_language(tmp_path):
    # a neutral question: the whole bundle's authored prose must be clean
    # except for the explicitly-exempt instruction blocks that name the
    # forbidden concepts in order to prohibit them.
    out = build_ask_bundle(_built_package(tmp_path), "overview please")
    # split off the authored instruction blocks that deliberately name
    # forbidden concepts (safe-answering, forbidden-claims, framing).
    head = out.split("## Safe Answering Instructions", 1)[0]
    # remove the framing line too (it may name forbidden concepts)
    head_lines = [
        ln for ln in head.splitlines() if not ln.startswith("- evidence-safe framing:")
    ]
    lowered = "\n".join(head_lines).lower()
    for phrase in ASK_FORBIDDEN_PHRASES:
        assert phrase not in lowered, f"authored prose leaked {phrase!r}"


def test_forbidden_words_in_question_are_quoted_not_asserted(tmp_path):
    # the question intentionally contains banned words; it must be quoted
    # verbatim as user input and must NOT cause the guard to fire.
    q = "What did the person say about their identity and intent?"
    out = build_ask_bundle(_built_package(tmp_path), q)
    # quoted verbatim under the User Question heading, as a blockquote
    assert "> " + q in out


def test_missing_package_raises(tmp_path):
    with pytest.raises(AskBundleError):
        build_ask_bundle(tmp_path / "does_not_exist.clulatent", "overview")


# --- 7. JSON output ---------------------------------------------------------


def test_json_output(tmp_path):
    raw = build_ask_bundle(
        _built_package(tmp_path), "around 0:04-0:10", output_format="json"
    )
    data = json.loads(raw)
    assert data["schema_id"] == "clulatent.ask_bundle.v0"
    assert data["intent"] == "evidence_near_time"
    # 0:04-0:10 clamps to the 5000 ms fixture duration -> [4000, 5000]
    assert data["relevant_window"]["start_ms"] == 4000
    assert data["relevant_window"]["end_ms"] == 5000
    assert data["relevant_window"]["events"]
    assert data["caveats"]
    assert data["safe_answering_rules"]
    assert data["forbidden_claim_rules"]


def test_bad_format_rejected(tmp_path):
    with pytest.raises(AskBundleError):
        build_ask_bundle(_built_package(tmp_path), "x", output_format="xml")


# --- 8. CLI wiring ----------------------------------------------------------


def test_cli_ask_help():
    result = runner.invoke(app, ["ask", "--help"])
    assert result.exit_code == 0
    assert "evidence-grounded" in result.stdout


def test_cli_ask_runs(tmp_path):
    pkg = _built_package(tmp_path)
    result = runner.invoke(
        app, ["ask", str(pkg), "What evidence exists around 00:04?"]
    )
    assert result.exit_code == 0
    assert "# CLULatent Ask Bundle" in result.stdout


def test_cli_ask_writes_file(tmp_path):
    pkg = _built_package(tmp_path)
    out = tmp_path / "ask_bundle.md"
    result = runner.invoke(
        app, ["ask", str(pkg), "What evidence exists around 00:04?", "--output", str(out)]
    )
    assert result.exit_code == 0
    assert out.exists()
    assert "# CLULatent Ask Bundle" in out.read_text(encoding="utf-8")
