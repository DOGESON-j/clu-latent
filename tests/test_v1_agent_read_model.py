from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent.agent_review_writer import run_agent_review
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.lock import create_lock
from clu_latent.v1_agent_read_model import (
    ARTIFACTS, MANIFEST, MANIFEST_SCHEMA_ID, AgentReadModelError,
    build_agent_read_model, get_agent_read_window, load_agent_read_manifest,
    render_agent_read_markdown, render_agent_read_summary, write_agent_read_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clulatent_324_fixture", ROOT / "tests/fixtures/conformance/build.py")
build = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(build)
runner = CliRunner()
BUNDLE_ID = "eb_000000000000_000000005000"


def package(tmp_path: Path, evidence: bool = True) -> Path:
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "p.clulatent")
    if evidence:
        build_evidence_bundle(pkg, start_ms=0, end_ms=5000, tool_name=TOOL_NAME, tool_version=TOOL_VERSION)
        run_agent_review(pkg, BUNDLE_ID, tool_name=TOOL_NAME, tool_version=TOOL_VERSION)
    return pkg


def snapshot(pkg: Path) -> set[str]:
    return {str(p.relative_to(pkg)) for p in pkg.rglob("*") if p.is_file()}


def test_builds_budgeted_models_with_progressive_detail(tmp_path):
    pkg = package(tmp_path)
    models = {b: build_agent_read_model(pkg, budget=b) for b in ("micro", "summary", "standard", "full")}
    assert all(m["schema_id"] == "clulatent.v1_agent_read_model.v0" for m in models.values())
    assert models["summary"]["track_counts"]
    assert models["standard"]["windows"] and models["standard"]["rankings"]
    assert len(render_agent_read_markdown(models["full"], budget="full")) >= len(render_agent_read_markdown(models["standard"], budget="standard"))
    micro = render_agent_read_summary(models["micro"])
    assert len(micro.split()) < 200 and "validation" in micro and "tracks" in micro


def test_windows_have_required_refs_policy_and_safe_rankings(tmp_path):
    model = build_agent_read_model(package(tmp_path))
    window = model["windows"][0]
    for key in ("window_id", "t_start_ms", "t_end_ms", "timecode_start", "evidence_density",
                "keyframe_ids", "evidence_bundle_ids", "agent_review_ids", "safe_summary",
                "missing_or_unavailable_evidence", "caveats", "retrieval_hints"):
        assert key in window
    assert BUNDLE_ID in window["evidence_bundle_ids"]
    assert window["agent_review_ids"]
    assert "recommended_inspection_order" in model["rankings"]
    assert "important" not in json.dumps(model["rankings"]).lower()


def test_focused_window_and_event_cap(tmp_path):
    pkg = package(tmp_path)
    result = get_agent_read_window(pkg, time_ms=1700)
    assert result["window"]["t_start_ms"] == 1000
    assert result["window"]["t_end_ms"] == 2000
    capped = build_agent_read_model(pkg, max_windows=2)
    assert len(capped["windows"]) == 2 and capped["windows_truncated"]


def test_generation_is_read_only_and_preserves_opaque_data(tmp_path):
    pkg = package(tmp_path)
    manifest_path = pkg / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["source"]["filename"] = "person appeared object moved intent.mp4"
    manifest_path.write_text(json.dumps(data))
    before = snapshot(pkg)
    model = build_agent_read_model(pkg)
    assert model["package"]["source"]["filename"] == data["source"]["filename"]
    assert snapshot(pkg) == before


def test_write_artifacts_manifest_jsonl_and_no_receipt(tmp_path):
    pkg = package(tmp_path)
    before_receipts = list((pkg / "receipts").glob("*"))
    result = write_agent_read_artifacts(pkg)
    assert result.written_paths == [f"index/v1/{n}" for n in (*ARTIFACTS, MANIFEST)]
    assert list((pkg / "receipts").glob("*")) == before_receipts
    manifest = load_agent_read_manifest(pkg)
    assert manifest["schema_id"] == MANIFEST_SCHEMA_ID and manifest["package_id"]
    assert all(a["sha256"] for a in manifest["artifacts"])
    lines = (pkg / "index/v1/agent_read_windows.jsonl").read_text().splitlines()
    assert lines and all(json.loads(line)["window_id"] for line in lines)
    added = snapshot(pkg) - {str(p.relative_to(pkg)) for p in pkg.rglob("*") if False}
    assert all(not p.startswith("receipts/") for p in result.written_paths)


def test_write_refuses_integrity_lock(tmp_path):
    pkg = package(tmp_path)
    create_lock(pkg)
    before = snapshot(pkg)
    with pytest.raises(AgentReadModelError, match="integrity lock"):
        write_agent_read_artifacts(pkg)
    assert snapshot(pkg) == before


@pytest.mark.parametrize("args", [
    ["agent-read", "--help"],
    ["agent-read", "summary"],
    ["agent-read", "export"],
    ["agent-read", "window"],
    ["agent-read", "write-index"],
])
def test_cli_help(args):
    result = runner.invoke(app, args + (["--help"] if args[-1] != "--help" else []))
    assert result.exit_code == 0, result.output


def test_cli_summary_export_window_and_write(tmp_path):
    pkg = package(tmp_path)
    output = tmp_path / "model.json"
    assert runner.invoke(app, ["agent-read", "summary", str(pkg)]).exit_code == 0
    assert runner.invoke(app, ["agent-read", "export", str(pkg), "--output", str(output), "--max-windows", "2"]).exit_code == 0
    assert json.loads(output.read_text())["budget"] == "standard"
    assert runner.invoke(app, ["agent-read", "window", str(pkg), "--time", "1.7s"]).exit_code == 0
    assert runner.invoke(app, ["agent-read", "write-index", str(pkg)]).exit_code == 0


def test_authored_prose_avoids_forbidden_claims(tmp_path):
    text = json.dumps(build_agent_read_model(package(tmp_path))).lower()
    for phrase in ("person appeared", "object moved", "car entered", "face changed", "someone said", "confirmed scene", "the video shows"):
        assert phrase not in text
