"""Phase 3.26: V1 package index verify + staleness check (read-only).

`clulatent package-index verify PACKAGE` answers "is the package's V1
self-description present, internally consistent, and fresh relative to the
package it describes?" It never refreshes either index, never writes
agent-read artifacts, never alters the manifest/tracks/receipts/lock, and
runs no evidence generation, media analysis, or model/network call.

Fixtures reuse the same conformance builder and real evidence-bundle /
agent-review writers as `test_v1_package_index.py`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import v1_agent_read_model as v1_agent_read_model_mod
from clu_latent import v1_package_index as v1_package_index_mod
from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.lock import create_lock
from clu_latent.v1_package_index import (
    INDEX_V1_DIR,
    V1PackageIndexError,
    render_v1_package_index_verification,
    verify_v1_package_index,
    write_v1_package_index,
)
from clu_latent.agent_review_writer import run_agent_review

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_pkgindex_verify", CONFORMANCE_DIR / "build.py"
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


def _snapshot(root: Path) -> dict[str, str]:
    """Content-hash snapshot: sha256 per file, so mtime-only changes don't hide a mutation."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# --- 1. fully fresh package (both indexes present, matching, resolving) ----


def test_fresh_package_index_and_agent_read_both_fresh(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    v1_agent_read_model_mod.write_agent_read_artifacts(pkg)
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "FRESH"
    assert result.agent_read_index.status == "FRESH"
    assert result.overall_status == "FRESH"
    assert result.remediation == []


def test_render_output_has_no_forbidden_language(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    v1_agent_read_model_mod.write_agent_read_artifacts(pkg)
    result = verify_v1_package_index(pkg)
    text = render_v1_package_index_verification(result)
    lowered = text.lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in lowered, f"verification render leaked {phrase!r}"


# --- 2. missing V1 package index ---------------------------------------


def test_missing_package_index_reports_missing(tmp_path):
    pkg = _built_package(tmp_path)
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "MISSING"
    assert result.overall_status == "MISSING"
    assert any("refresh" in cmd for cmd in result.remediation)
    assert str(pkg) in result.remediation[0]


def test_missing_agent_read_index_reported_but_package_index_can_be_fresh(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "FRESH"
    assert result.agent_read_index.status == "MISSING"
    assert any("agent-read write-index" in cmd for cmd in result.remediation)


# --- 3. missing individual artifact -------------------------------------


def test_missing_individual_artifact_is_invalid(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    (pkg / INDEX_V1_DIR / "ask_prompt.md").unlink()
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "INVALID"
    statuses = {a.name: a.status for a in result.package_index.artifacts}
    assert statuses["ask_prompt.md"] == "MISSING"


# --- 4. package ID mismatch ----------------------------------------------


def test_package_id_mismatch_is_invalid(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["package_id"] = "not-the-real-package-id"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "INVALID"
    assert any("package id mismatch" in f for f in result.package_index.findings)


# --- 5. track inventory/count mismatch (stale) ----------------------------


def test_track_count_mismatch_is_stale(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["package"]["track_counts"] = {"keyframes": 999}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "STALE"
    assert any("track counts changed" in f for f in result.package_index.findings)


# --- 6. unresolved referenced evidence bundle / agent review -------------


def test_unresolved_evidence_bundle_reference_is_invalid(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["evidence"]["evidence_bundle_ids"].append("eb_does_not_exist")
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "INVALID"
    assert any(
        "evidence bundle reference no longer resolves: eb_does_not_exist" in f
        for f in result.package_index.findings
    )


def test_unresolved_agent_review_reference_is_invalid(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest_path = pkg / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["evidence"]["agent_review_ids"].append("rev_does_not_exist")
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "INVALID"
    assert any(
        "agent review reference no longer resolves: rev_does_not_exist" in f
        for f in result.package_index.findings
    )


# --- 7. stale agent-read condition ----------------------------------------


def test_agent_read_track_count_mismatch_is_stale(tmp_path):
    pkg = _built_package(tmp_path)
    v1_agent_read_model_mod.write_agent_read_artifacts(pkg)
    manifest_path = pkg / v1_agent_read_model_mod.INDEX_DIR / v1_agent_read_model_mod.MANIFEST
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["track_counts"] = {"keyframes": 999}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = verify_v1_package_index(pkg)
    assert result.agent_read_index.status == "STALE"
    assert any("track counts changed" in f for f in result.agent_read_index.findings)


# --- 8. unknown freshness when provenance is insufficient -----------------


def test_unknown_when_current_state_cannot_be_recomputed(tmp_path, monkeypatch):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)

    def _boom(*args, **kwargs):
        raise v1_package_index_mod.agent_context_mod.AgentContextError("simulated failure")

    monkeypatch.setattr(v1_package_index_mod, "build_agent_context", _boom)
    result = verify_v1_package_index(pkg)
    assert result.package_index.status == "UNKNOWN"
    assert any("could not recompute current package state" in f for f in result.package_index.findings)


# --- 9. remediation recommendation correctness ----------------------------


def test_remediation_only_names_the_broken_component(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    # package-index is fresh; agent-read is simply absent.
    result = verify_v1_package_index(pkg)
    assert len(result.remediation) == 1
    assert "agent-read write-index" in result.remediation[0]
    assert "package-index refresh" not in " ".join(result.remediation)


# --- 10. read-only guarantees ----------------------------------------------


def test_verify_does_not_mutate_package_content_hashes(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    v1_agent_read_model_mod.write_agent_read_artifacts(pkg)
    before = _snapshot(pkg)
    verify_v1_package_index(pkg)
    assert _snapshot(pkg) == before


def test_verify_creates_no_receipts(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    verify_v1_package_index(pkg)
    assert sorted((pkg / "receipts").glob("*.jsonl")) == receipts_before


def test_verify_does_not_change_lock_state(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    lock_dir_before = sorted((pkg / "lock").glob("*")) if (pkg / "lock").is_dir() else []
    verify_v1_package_index(pkg)
    lock_dir_after = sorted((pkg / "lock").glob("*")) if (pkg / "lock").is_dir() else []
    assert lock_dir_before == lock_dir_after


def test_verify_works_against_locked_package(tmp_path):
    # Unlike write/refresh, verify is read-only and must not refuse a
    # locked package -- it should still be able to report its status.
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    create_lock(pkg)
    result = verify_v1_package_index(pkg)
    assert result.package_index.status in {"FRESH", "STALE", "INVALID", "UNKNOWN"}


# --- 11. malformed package handling ----------------------------------------


def test_missing_package_raises(tmp_path):
    with pytest.raises(V1PackageIndexError):
        verify_v1_package_index(tmp_path / "does_not_exist.clulatent")


def test_malformed_package_raises(tmp_path):
    bad = tmp_path / "bad.clulatent"
    bad.mkdir()
    (bad / "manifest.json").write_text("not json", encoding="utf-8")
    with pytest.raises(V1PackageIndexError):
        verify_v1_package_index(bad)


# --- 12. deliberately stale copied package (manual-smoke style, in test form) --


def test_stale_copy_detected_without_damaging_original(tmp_path):
    pkg = _built_package(tmp_path / "original")
    write_v1_package_index(pkg)
    original_before = _snapshot(pkg)

    stale_copy = tmp_path / "stale_copy.clulatent"
    shutil.copytree(pkg, stale_copy)
    # Simulate new evidence added after the index was generated: append a
    # bundle id the copied manifest never recorded, without adding evidence.
    manifest_path = stale_copy / INDEX_V1_DIR / "index_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["package"]["track_counts"] = {**data["package"]["track_counts"], "keyframes": 12345}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    fresh_result = verify_v1_package_index(pkg)
    stale_result = verify_v1_package_index(stale_copy)

    assert fresh_result.package_index.status == "FRESH"
    assert stale_result.package_index.status == "STALE"
    # the original package was never touched by verifying the copy
    assert _snapshot(pkg) == original_before


# --- 13. CLI wiring ----------------------------------------------------------


def test_cli_package_index_verify_help():
    result = runner.invoke(app, ["package-index", "verify", "--help"])
    assert result.exit_code == 0
    assert "verify" in result.stdout.lower()


def test_cli_verify_fresh_package(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    v1_agent_read_model_mod.write_agent_read_artifacts(pkg)
    result = runner.invoke(app, ["package-index", "verify", str(pkg)])
    assert result.exit_code == 0, result.output
    assert "FRESH" in result.stdout


def test_cli_verify_missing_index(tmp_path):
    pkg = _built_package(tmp_path)
    result = runner.invoke(app, ["package-index", "verify", str(pkg)])
    assert result.exit_code == 0, result.output
    assert "MISSING" in result.stdout
    assert "package-index refresh" in result.stdout


def test_cli_verify_missing_package_fails_cleanly(tmp_path):
    result = runner.invoke(app, ["package-index", "verify", str(tmp_path / "nope.clulatent")])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_cli_verify_does_not_mutate(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    before = _snapshot(pkg)
    runner.invoke(app, ["package-index", "verify", str(pkg)])
    assert _snapshot(pkg) == before
