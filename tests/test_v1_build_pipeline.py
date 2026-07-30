"""Phase 3.19: the V1 build pipeline (`clu_latent.v1_build`).

These tests exercise `build_v1_package`'s orchestration without a real
movie, FFmpeg, Pillow, or a network/model call. The ingest step is
monkeypatched to hand the pipeline a genuinely-valid package (built
deterministically by the conformance fixture builder), so the *rest* of
the pipeline — validate, the Pillow-gated visual lanes, the full-package
evidence bundle, the agent review, and the second validate — runs for
real against a real package.

Covered: build order, validate-before / validate-after, clear failure
when Pillow is absent under the default profile, `--allow-partial`
continuing and marking the result partial, real bundle + review
generation, the visual lanes running when available, unknown-profile
rejection, and that the build summary carries no semantic claim.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

from clu_latent import v1_build as v1_build_mod
from clu_latent.agent_context import FORBIDDEN_CONTEXT_PHRASES
from clu_latent.ingest import IngestResult
from clu_latent.manifest import Manifest
from clu_latent.v1_build import V1BuildError, build_v1_package

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_DIR = REPO_ROOT / "tests" / "fixtures" / "conformance"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "clulatent_conformance_build_v1", CONFORMANCE_DIR / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = _load_builder()


@dataclass
class _FakeVisualResult:
    events_written: int
    strength_counts: dict


def _make_package(tmp_path: Path) -> Path:
    return build.build_fixture("valid_keyframes_audio", tmp_path / "ingested.clulatent")


def _patch_ingest(monkeypatch, package_path: Path) -> None:
    """Make the pipeline's ingest step return an existing real package."""
    manifest = Manifest.from_json_file(package_path / "manifest.json")

    def fake_ingest(video, output, **kwargs):  # noqa: ANN001
        return IngestResult(manifest=manifest, package_path=package_path)

    monkeypatch.setattr(v1_build_mod, "ingest_video", fake_ingest)


# --- 1. Pillow absent, default profile -> clear failure ---------------------


def test_build_fails_clearly_when_pillow_unavailable(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)

    with pytest.raises(V1BuildError) as excinfo:
        build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=False)
    assert "visual" in str(excinfo.value).lower()


# --- 2. Pillow absent + --allow-partial -> partial build succeeds -----------


def test_build_partial_when_allowed(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)

    result = build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=True)

    assert result.partial is True
    assert result.skipped_lanes == ["visual_change", "changed_regions"]
    assert result.visual_change_events == 0
    assert result.changed_region_events == 0
    # the bundle + review still ran, over real evidence
    assert result.bundle_ids == ["eb_000000000000_000000005000"]
    assert result.review_ids == ["ar_eb_000000000000_000000005000"]
    assert result.validation_before.valid
    assert result.validation_after.valid


# --- 3. Pillow available -> visual lanes run (stubbed, no real Pillow) -------


def test_build_runs_visual_lanes_when_available(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: True)
    monkeypatch.setattr(v1_build_mod.changed_region_mod, "is_available", lambda: True)

    calls: list[str] = []

    def fake_vc(package_path, **kwargs):  # noqa: ANN001
        calls.append("visual_change")
        return _FakeVisualResult(events_written=3, strength_counts={"low": 3})

    def fake_cr(package_path, **kwargs):  # noqa: ANN001
        calls.append("changed_regions")
        return _FakeVisualResult(events_written=2, strength_counts={"high": 2})

    monkeypatch.setattr(v1_build_mod, "analyze_visual_change", fake_vc)
    monkeypatch.setattr(v1_build_mod, "analyze_changed_regions", fake_cr)

    result = build_v1_package(tmp_path / "in.mp4", pkg)

    assert result.partial is False
    assert result.skipped_lanes == []
    assert calls == ["visual_change", "changed_regions"]
    assert result.visual_change_events == 3
    assert result.changed_region_events == 2
    assert result.bundle_ids and result.review_ids


# --- 4. build order is fixed and correct ------------------------------------


def test_build_order(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)

    order: list[str] = []
    real_validate = v1_build_mod.validate_package
    real_bundle = v1_build_mod.build_evidence_bundle
    real_review = v1_build_mod.run_agent_review

    def rec_validate(*a, **k):
        order.append("validate")
        return real_validate(*a, **k)

    def rec_bundle(*a, **k):
        order.append("evidence_bundle")
        return real_bundle(*a, **k)

    def rec_review(*a, **k):
        order.append("agent_review")
        return real_review(*a, **k)

    # ingest is already patched; record it too by wrapping the fake
    fake_ingest = v1_build_mod.ingest_video

    def rec_ingest(*a, **k):
        order.append("ingest")
        return fake_ingest(*a, **k)

    monkeypatch.setattr(v1_build_mod, "ingest_video", rec_ingest)
    monkeypatch.setattr(v1_build_mod, "validate_package", rec_validate)
    monkeypatch.setattr(v1_build_mod, "build_evidence_bundle", rec_bundle)
    monkeypatch.setattr(v1_build_mod, "run_agent_review", rec_review)

    build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=True)

    assert order == [
        "ingest",
        "validate",
        "evidence_bundle",
        "agent_review",
        "validate",
    ]


# --- 5. unknown profile is rejected -----------------------------------------


def test_unknown_profile_rejected(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    with pytest.raises(V1BuildError):
        build_v1_package(tmp_path / "in.mp4", pkg, profile="v2")


# --- 6. the build result carries no semantic claim --------------------------


def test_build_result_has_no_semantic_language(tmp_path, monkeypatch):
    pkg = _make_package(tmp_path)
    _patch_ingest(monkeypatch, pkg)
    monkeypatch.setattr(v1_build_mod.visual_change_mod, "is_available", lambda: False)

    result = build_v1_package(tmp_path / "in.mp4", pkg, allow_partial=True)

    blob = " ".join(
        [
            *result.track_counts.keys(),
            *result.receipts,
            *result.bundle_ids,
            *result.review_ids,
            *result.skipped_lanes,
            *(f"{s.name} {s.status} {s.detail}" for s in result.steps),
        ]
    ).lower()
    for phrase in FORBIDDEN_CONTEXT_PHRASES:
        assert phrase not in blob, f"build summary leaked forbidden phrase {phrase!r}"
