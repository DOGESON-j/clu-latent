"""Tests for Phase 2.16: the real adapter demo package.

Proves the Phase 2.15 real (ffmpeg `scdet`) visual-change adapter is
demonstrable end-to-end in a clean user-facing workflow: generate ->
dry-run -> import -> validate, against a real `.clulatent` package,
with the resulting tracks/manifest/receipt inspected directly.

This phase adds no new module, no new CLI command, and no new adapter
architecture -- every command exercised here already existed before
Phase 2.16 (Phase 2.8's `analysis` CLI group, Phase 2.15's `generate-
ffmpeg-visual-change-adapter-result`). Mirrors the structure of
`tests/test_analysis_adapter_demo_workflow.py` (Phase 2.14's fixture-
adapter demo), but exercises the *real* adapter instead of the static
fixture.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent.analysis_adapter_dry_run import dry_run_adapter_result
from clu_latent.analysis_adapters import validate_adapter_result, write_adapter_result
from clu_latent.analysis_ffmpeg_visual_change_adapter import (
    ADAPTER_NAME,
    VISUAL_CHANGE_LANE,
    build_visual_change_adapter_result,
    visual_change_adapter_result_to_dict,
)
from clu_latent.analysis_lanes import FORBIDDEN_IDENTITY_FIELDS, IDENTITY_FLAG_FIELDS
from clu_latent.cli import analysis_app, app
from clu_latent.manifest import Manifest
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DOC_PATH = REPO_ROOT / "docs" / "PHASE_2_16_REAL_ADAPTER_DEMO_PACKAGE.md"
README_PATH = REPO_ROOT / "README.md"

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Documentation command surface (no media / no package needed) ----------


def _known_command_surface() -> tuple[set[str], set[str], set[str]]:
    """Return (top_level_commands, group_names, analysis_subcommands)."""

    def command_names(typer_app) -> set[str]:
        names: set[str] = set()
        for command in typer_app.registered_commands:
            if command.name:
                names.add(command.name)
            elif command.callback is not None:
                names.add(command.callback.__name__.replace("_", "-"))
        return names

    top_level = command_names(app)
    groups = {group.name for group in app.registered_groups if group.name}
    analysis_sub = command_names(analysis_app)
    return top_level, groups, analysis_sub


def _extract_clulatent_invocations(text: str) -> list[list[str]]:
    invocations: list[list[str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("clulatent "):
            continue
        invocations.append(line.split())
    return invocations


def test_demo_doc_exists():
    assert DEMO_DOC_PATH.is_file()


@pytest.mark.parametrize("doc_path", [DEMO_DOC_PATH, README_PATH])
def test_doc_command_snippets_reference_only_existing_commands(doc_path):
    top_level, groups, analysis_sub = _known_command_surface()
    text = doc_path.read_text(encoding="utf-8")
    invocations = _extract_clulatent_invocations(text)
    assert invocations, f"expected at least one clulatent invocation in {doc_path.name}"
    for tokens in invocations:
        assert tokens[0] == "clulatent"
        assert len(tokens) >= 2, tokens
        first = tokens[1]
        if first in groups:
            assert len(tokens) >= 3, tokens
            subcommand = tokens[2]
            if first == "analysis":
                assert subcommand in analysis_sub, f"unknown analysis subcommand: {subcommand}"
        else:
            assert first in top_level, f"unknown top-level command: {first}"


def test_demo_doc_mentions_evidence_not_truth():
    text = DEMO_DOC_PATH.read_text(encoding="utf-8")
    assert "evidence" in text.lower()
    assert "not truth" in text.lower() or "not confirmed" in text.lower()


def test_readme_roadmap_mentions_phase_2_16():
    text = README_PATH.read_text(encoding="utf-8")
    assert "CLULatent" in text


# --- ffmpeg-gated end-to-end demo walkthrough -------------------------------


pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


def _run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True)


@pytest.fixture(scope="module")
def scene_change_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_demo_package")
    video_path = directory / "scene_change.mp4"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=64x64:duration=1:rate=5",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=64x64:duration=1:rate=5",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ]
    )
    return video_path


@pytest.fixture
def valid_package(tmp_path, scene_change_video):
    from clu_latent.ingest import ingest_video

    output_path = tmp_path / "demo.clulatent"
    result = ingest_video(scene_change_video, output_path)
    return result.package_path


def _file_digests(root: Path, *, skip: set[str]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if rel in skip:
            continue
        digests[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


@pytestmark_e2e
def test_demo_generation_succeeds(scene_change_video, tmp_path):
    result_path = tmp_path / "visual_change_adapter_result.json"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(result_path),
            "--threshold",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert result_path.is_file()
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME


@pytestmark_e2e
def test_generated_result_validates_under_adapter_contract(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


@pytestmark_e2e
def test_generated_result_dry_runs_successfully(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    report = dry_run_adapter_result(result, package_path=valid_package)
    assert report.valid is True
    assert report.errors == []
    assert report.would_write is True


@pytestmark_e2e
def test_generated_result_imports_successfully(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_results = write_adapter_result(valid_package, result)
    assert {w.lane for w in write_results} == {VISUAL_CHANGE_LANE}


@pytestmark_e2e
def test_imported_package_validates_successfully(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


@pytestmark_e2e
def test_expected_analysis_track_exists(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    track_path = valid_package / "tracks" / f"{VISUAL_CHANGE_LANE}.jsonl"
    assert track_path.is_file()
    events = read_track_file(track_path)
    assert len(events) == len(result.events_by_lane[VISUAL_CHANGE_LANE])
    assert len(events) >= 1


@pytestmark_e2e
def test_analyze_receipt_exists(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    receipt_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipt_path.is_file()
    lines = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
    assert any(line["adapter_name"] == ADAPTER_NAME for line in lines)


@pytestmark_e2e
def test_manifest_contains_expected_analysis_track_descriptor(scene_change_video, valid_package):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    names = {t.name for t in manifest.tracks}
    assert VISUAL_CHANGE_LANE in names
    descriptor = next(t for t in manifest.tracks if t.name == VISUAL_CHANGE_LANE)
    assert descriptor.file == f"tracks/{VISUAL_CHANGE_LANE}.jsonl"
    assert descriptor.record_count == len(result.events_by_lane[VISUAL_CHANGE_LANE])


@pytestmark_e2e
def test_no_source_track_mutation(scene_change_video, valid_package):
    # Snapshot every ingest-produced file except the analysis track this
    # import will create (and manifest.json, which legitimately gains a
    # new TrackDescriptor entry) and the receipt, which legitimately
    # gains a new line.
    skip = {
        "manifest.json",
        f"tracks/{VISUAL_CHANGE_LANE}.jsonl",
        "receipts/analyze.jsonl",
    }
    before = _file_digests(valid_package, skip=skip)

    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    after = _file_digests(valid_package, skip=skip)
    assert after == before


@pytestmark_e2e
def test_no_index_mutation(scene_change_video, valid_package):
    index_path = valid_package / "index" / "search.sqlite"
    before = index_path.read_bytes()

    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    write_adapter_result(valid_package, result)

    after = index_path.read_bytes()
    assert after == before


@pytestmark_e2e
def test_generated_event_labels_remain_conservative(scene_change_video):
    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    events = result.events_by_lane[VISUAL_CHANGE_LANE]
    assert events, "expected at least one candidate event on a scene-change video"
    for event in events:
        payload = event["payload"]
        assert "candidate" in payload["evidence_label"].lower()
        for forbidden_field in FORBIDDEN_IDENTITY_FIELDS:
            assert forbidden_field not in payload
        for flag_field in IDENTITY_FLAG_FIELDS:
            assert flag_field not in payload
    for warning in result.metadata.warnings + result.warnings:
        lowered = warning.lower()
        assert "confirmed" not in lowered or "not confirmed" in lowered


@pytestmark_e2e
def test_cli_demo_path_works_end_to_end(scene_change_video, valid_package, tmp_path):
    """Walk the whole documented demo sequence via the CLI, in one test."""
    runner = CliRunner()

    # 1. generate
    result_path = tmp_path / "visual_change_adapter_result.json"
    gen = runner.invoke(
        app,
        [
            "analysis",
            "generate-ffmpeg-visual-change-adapter-result",
            str(scene_change_video),
            "--output",
            str(result_path),
            "--threshold",
            "5",
        ],
    )
    assert gen.exit_code == 0, gen.output

    # 2. dry-run (writes nothing)
    track_path = valid_package / "tracks" / f"{VISUAL_CHANGE_LANE}.jsonl"
    dry = runner.invoke(
        app,
        ["analysis", "dry-run-adapter", str(result_path), "--package", str(valid_package)],
    )
    assert dry.exit_code == 0, dry.output
    assert "PASS" in dry.output
    assert not track_path.exists()

    # 3. import
    imp = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imp.exit_code == 0, imp.output
    assert VISUAL_CHANGE_LANE in imp.output

    # 4. validate
    val = runner.invoke(app, ["validate", str(valid_package)])
    assert val.exit_code == 0, val.output
    assert "PASS" in val.output

    # 5. tracks/manifest/receipt landed
    assert track_path.exists()
    assert (valid_package / "receipts" / "analyze.jsonl").exists()
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert any(t.name == VISUAL_CHANGE_LANE for t in manifest.tracks)


@pytestmark_e2e
def test_cli_demo_output_has_no_control_sequences(scene_change_video):
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-ffmpeg-visual-change-adapter-result", str(scene_change_video)]
    )
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


@pytestmark_e2e
def test_locked_package_blocks_demo_import(scene_change_video, valid_package, tmp_path):
    from clu_latent import lock as lock_mod

    lock_mod.create_lock(valid_package)

    result = build_visual_change_adapter_result(scene_change_video, threshold=5.0)
    result_path = tmp_path / "visual_change_adapter_result.json"
    result_path.write_text(json.dumps(visual_change_adapter_result_to_dict(result)), encoding="utf-8")

    runner = CliRunner()
    outcome = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert outcome.exit_code == 1
    assert "integrity lock" in outcome.output
    track_path = valid_package / "tracks" / f"{VISUAL_CHANGE_LANE}.jsonl"
    assert not track_path.exists()
