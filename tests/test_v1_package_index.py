"""Phase 3.22: the V1 self-describing package index (`clu_latent.v1_package_index`).

These tests pin the Phase 3.22 deliverable built on the Phase 3.19
agent-context layer and the Phase 3.18 reader: a V1-built package carries
its own agent-readable index under `index/v1/` -- `agent_context.json`,
`agent_context.md`, `ask_prompt.md`, and `index_manifest.json`. The
writer re-packages the already-built agent context (adds no evidence lane,
runs no FFmpeg/Pillow/model), writes no receipt, honors the integrity
lock, and never emits a semantic claim in its authored prose. `build-video`
writes the index by default; `refresh` regenerates it; `inspect` reports it
read-only.

Fixtures are built by the conformance fixture builder plus the real
evidence-bundle / agent-review writers -- a genuine package with real
tracks, an evidence bundle, and a review; no FFmpeg/Pillow/model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import v1_build as v1_build_mod
from clu_latent import v1_package_index as v1_package_index_mod
from clu_latent.v1_build import build_v1_package
from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.cli import app
from clu_latent.constants import TOOL_NAME, TOOL_VERSION
from clu_latent.evidence_bundle_writer import build_evidence_bundle
from clu_latent.ingest import IngestResult
from clu_latent.lock import create_lock
from clu_latent.manifest import Manifest
from clu_latent.v1_package_index import (
    INDEX_ARTIFACT_FILENAMES,
    INDEX_MANIFEST_FILENAME,
    INDEX_V1_DIR,
    V1_PACKAGE_INDEX_SCHEMA_ID,
    V1_PACKAGE_INDEX_SCHEMA_VERSION,
    V1PackageIndexError,
    build_v1_package_index,
    load_v1_index_manifest,
    refresh_v1_package_index,
    v1_index_exists,
    write_v1_package_index,
)
from clu_latent.agent_review_writer import run_agent_review

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"

_BUNDLE_ID = "eb_000000000000_000000005000"

runner = CliRunner()


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_pkgindex", CONFORMANCE_DIR / "build.py"
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


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


# --- 1. write creates the four expected artifacts --------------------------


def test_write_creates_all_four_artifacts(tmp_path):
    pkg = _built_package(tmp_path)
    result = write_v1_package_index(pkg)
    assert result.wrote is True
    v1_dir = pkg / INDEX_V1_DIR
    for filename in list(INDEX_ARTIFACT_FILENAMES) + [INDEX_MANIFEST_FILENAME]:
        assert (v1_dir / filename).is_file(), filename
    # written_paths are package-relative and POSIX
    assert result.written_paths == [
        f"{INDEX_V1_DIR}/{name}"
        for name in list(INDEX_ARTIFACT_FILENAMES) + [INDEX_MANIFEST_FILENAME]
    ]


def test_v1_index_exists_reflects_state(tmp_path):
    pkg = _built_package(tmp_path)
    assert v1_index_exists(pkg) is False
    write_v1_package_index(pkg)
    assert v1_index_exists(pkg) is True


# --- 2. index manifest content ---------------------------------------------


def test_index_manifest_has_required_fields(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest = load_v1_index_manifest(pkg)
    assert manifest["schema_id"] == V1_PACKAGE_INDEX_SCHEMA_ID
    assert manifest["schema_version"] == V1_PACKAGE_INDEX_SCHEMA_VERSION
    assert manifest["package_id"]
    assert manifest["generated_at"]
    assert manifest["generated_by"]["tool"] == TOOL_NAME
    assert manifest["generated_by"]["version"] == TOOL_VERSION
    assert "source_validation" in manifest
    assert manifest["caveats"]
    assert manifest["artifacts"]
    # track counts + evidence ids are carried
    assert isinstance(manifest["package"]["track_counts"], dict)
    assert isinstance(manifest["evidence"]["evidence_bundle_ids"], list)
    assert isinstance(manifest["evidence"]["agent_review_ids"], list)


def test_index_manifest_carries_evidence_ids(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest = load_v1_index_manifest(pkg)
    assert _BUNDLE_ID in manifest["evidence"]["evidence_bundle_ids"]
    assert manifest["evidence"]["agent_review_ids"]  # a review was built


def test_index_manifest_is_not_a_lock_claim(tmp_path):
    # The manifest must explicitly disclaim being a lock/certificate.
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest = load_v1_index_manifest(pkg)
    note = manifest["note"].lower()
    assert "not a lock" in note
    assert "not a certificate" in note


# --- 3. artifacts are valid + hashes match ---------------------------------


def test_agent_context_json_is_valid_json(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    raw = (pkg / INDEX_V1_DIR / "agent_context.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["schema_id"] == "clulatent.agent_context.v0"


def test_markdown_and_prompt_artifacts_present(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    md = (pkg / INDEX_V1_DIR / "agent_context.md").read_text(encoding="utf-8")
    prompt = (pkg / INDEX_V1_DIR / "ask_prompt.md").read_text(encoding="utf-8")
    assert md.startswith("# Agent context")
    assert "CLULATENT ASK PROMPT" in prompt


def test_artifact_hashes_match_file_contents(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest = load_v1_index_manifest(pkg)
    hashed = 0
    for artifact in manifest["artifacts"]:
        # paths are package-relative and stay inside index/v1/
        assert artifact["path"].startswith(f"{INDEX_V1_DIR}/")
        assert ".." not in artifact["path"]
        sha = artifact.get("sha256")
        if sha is None:
            # only the manifest's own entry is self-referential (no hash)
            assert artifact["name"] == INDEX_MANIFEST_FILENAME
            continue
        data = (pkg / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha, artifact["name"]
        assert artifact["size_bytes"] == len(data)
        hashed += 1
    assert hashed == len(INDEX_ARTIFACT_FILENAMES)


# --- 4. build (in-memory) is read-only -------------------------------------


def test_build_does_not_write_anything(tmp_path):
    pkg = _built_package(tmp_path)
    before = _snapshot(pkg)
    built = build_v1_package_index(pkg)
    after = _snapshot(pkg)
    assert before == after
    assert not v1_index_exists(pkg)
    # but it returns the full, hashed manifest in memory
    assert built["index_manifest"]["schema_id"] == V1_PACKAGE_INDEX_SCHEMA_ID


# --- 5. inspect is read-only, creates no receipt ---------------------------


def test_inspect_does_not_mutate_or_create_receipt(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    before = _snapshot(pkg)
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    load_v1_index_manifest(pkg)
    v1_index_exists(pkg)
    assert _snapshot(pkg) == before
    assert sorted((pkg / "receipts").glob("*.jsonl")) == receipts_before


# --- 6. refresh writes artifacts but no new evidence track / receipt -------


def test_refresh_writes_index_but_no_new_evidence(tmp_path):
    pkg = _built_package(tmp_path)
    tracks_before = sorted((pkg / "tracks").glob("*.jsonl"))
    receipts_before = sorted((pkg / "receipts").glob("*.jsonl"))
    refresh_v1_package_index(pkg)
    assert v1_index_exists(pkg)
    # no new evidence track and no new receipt were created
    assert sorted((pkg / "tracks").glob("*.jsonl")) == tracks_before
    assert sorted((pkg / "receipts").glob("*.jsonl")) == receipts_before


def test_refresh_is_idempotent_on_content(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    first = (pkg / INDEX_V1_DIR / "agent_context.json").read_bytes()
    refresh_v1_package_index(pkg)
    second = (pkg / INDEX_V1_DIR / "agent_context.json").read_bytes()
    # agent context content is deterministic (generated_at lives in the
    # index manifest, not the agent context artifact)
    assert first == second


# --- 7. integrity lock is respected ----------------------------------------


def test_write_refuses_when_package_locked(tmp_path):
    pkg = _built_package(tmp_path)
    create_lock(pkg)
    with pytest.raises(V1PackageIndexError) as excinfo:
        write_v1_package_index(pkg)
    assert "integrity lock" in str(excinfo.value).lower()
    assert not v1_index_exists(pkg)


# --- 8. missing / bad package ----------------------------------------------


def test_missing_package_raises(tmp_path):
    with pytest.raises(V1PackageIndexError):
        write_v1_package_index(tmp_path / "does_not_exist.clulatent")


def test_load_manifest_missing_raises(tmp_path):
    pkg = _built_package(tmp_path)
    with pytest.raises(V1PackageIndexError):
        load_v1_index_manifest(pkg)  # never written


# --- 9. no forbidden semantic language in authored prose -------------------


def test_authored_prose_has_no_forbidden_language(tmp_path):
    pkg = _built_package(tmp_path)
    write_v1_package_index(pkg)
    manifest = load_v1_index_manifest(pkg)
    authored = list(manifest["caveats"]) + [manifest["note"]]
    lowered = "\n".join(authored).lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in lowered, f"authored index prose leaked {phrase!r}"


def test_scary_source_filename_does_not_trip_guard(tmp_path):
    # A package whose opaque source filename contains banned words must not
    # trip the authored-prose guard (the guard scans only authored strings).
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "p.clulatent")
    manifest_path = pkg / "manifest.json"
    manifest = Manifest.from_json_file(manifest_path)
    # rewrite only the display filename (opaque user data), not stored_path
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["source"]["filename"] = "the_video_shows_intent_and_identity.mp4"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    # build must succeed despite the scary filename appearing in facts
    result = write_v1_package_index(pkg)
    assert result.wrote is True
    ctx = (pkg / INDEX_V1_DIR / "agent_context.json").read_text(encoding="utf-8")
    assert "the_video_shows_intent_and_identity.mp4" in ctx


# --- 10. build-video integration (default on / skip flag) ------------------


@dataclass
class _Ignore:
    pass


def _patch_ingest(monkeypatch, package_path: Path) -> None:
    manifest = Manifest.from_json_file(package_path / "manifest.json")

    def fake_ingest(video, output, **kwargs):  # noqa: ANN001
        return IngestResult(manifest=manifest, package_path=package_path)

    monkeypatch.setattr(v1_build_mod, "ingest_video", fake_ingest)


def test_build_v1_writes_index_by_default(tmp_path, monkeypatch):
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "ingested.clulatent")
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)
    result = build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=True)
    assert result.index_written is True
    assert result.index_artifacts
    assert v1_index_exists(pkg)


def test_build_v1_skips_index_when_disabled(tmp_path, monkeypatch):
    pkg = build.build_fixture("valid_keyframes_audio", tmp_path / "ingested.clulatent")
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)
    result = build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=True, write_index=False)
    assert result.index_written is False
    assert result.index_artifacts == []
    assert not v1_index_exists(pkg)


# --- 11. CLI wiring --------------------------------------------------------


def test_cli_package_index_help():
    result = runner.invoke(app, ["package-index", "--help"])
    assert result.exit_code == 0
    assert "index" in result.stdout.lower()


def test_cli_refresh_then_inspect(tmp_path):
    pkg = _built_package(tmp_path)
    r1 = runner.invoke(app, ["package-index", "refresh", str(pkg)])
    assert r1.exit_code == 0, r1.output
    assert "refreshed" in r1.stdout.lower()
    assert v1_index_exists(pkg)
    r2 = runner.invoke(app, ["package-index", "inspect", str(pkg)])
    assert r2.exit_code == 0, r2.output
    assert V1_PACKAGE_INDEX_SCHEMA_ID in r2.stdout


def test_cli_inspect_missing_index_is_not_error(tmp_path):
    pkg = _built_package(tmp_path)
    result = runner.invoke(app, ["package-index", "inspect", str(pkg)])
    assert result.exit_code == 0
    assert "no built-in v1 index" in result.stdout.lower()
