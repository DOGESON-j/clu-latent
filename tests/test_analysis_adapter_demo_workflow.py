"""Tests for Phase 2.14: the analysis adapter demo workflow.

Two groups of tests:

- Pure / no-media tests that exercise the tracked example JSON, the
  generator, and the documentation's command surface. These need no
  ffmpeg and no real package.
- ffmpeg-gated end-to-end tests that walk the documented workflow
  (generate -> dry-run -> import -> validate -> inspect) against a real
  `.clulatent` package, mirroring the fixture pattern already used in
  `tests/test_analysis_adapter_import.py`.

This phase adds no new module and no new CLI command, so these tests
only ever call commands that already existed before Phase 2.14.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import lock as lock_mod
from clu_latent.analysis_adapter_dry_run import (
    dry_run_adapter_result,
    load_adapter_result_json,
)
from clu_latent.analysis_adapters import validate_adapter_result, write_adapter_result
from clu_latent.analysis_fixture_adapter import (
    FIXTURE_ADAPTER_NAME,
    FIXTURE_LANES,
    fixture_adapter_result_json,
)
from clu_latent.cli import analysis_app, app
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_JSON_PATH = REPO_ROOT / "examples" / "fixture_adapter_result.json"
DEMO_DOC_PATH = REPO_ROOT / "docs" / "PHASE_2_14_ANALYSIS_ADAPTER_DEMO_WORKFLOW.md"
README_PATH = REPO_ROOT / "README.md"

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Tracked example JSON (no media / no package needed) ---------------------


def test_committed_example_exists():
    assert EXAMPLE_JSON_PATH.is_file()


def test_committed_example_is_valid_json():
    data = json.loads(EXAMPLE_JSON_PATH.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == FIXTURE_ADAPTER_NAME


def test_committed_example_matches_generator():
    # The tracked example must never drift from what the generator
    # produces (byte-for-byte, including the trailing newline the
    # --output CLI mode writes).
    expected = fixture_adapter_result_json() + "\n"
    actual = EXAMPLE_JSON_PATH.read_text(encoding="utf-8")
    assert actual == expected


def test_committed_example_validates_against_adapter_contracts():
    result = load_adapter_result_json(EXAMPLE_JSON_PATH)
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


def test_committed_example_dry_runs_successfully():
    result = load_adapter_result_json(EXAMPLE_JSON_PATH)
    report = dry_run_adapter_result(result, package_path=None)
    assert report.valid is True
    assert report.errors == []


# --- CLI generation (step 2 of the documented workflow) ----------------------


def test_documented_generation_emits_valid_json():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["metadata"]["adapter_name"] == FIXTURE_ADAPTER_NAME


def test_documented_generation_output_has_no_control_sequences():
    runner = CliRunner()
    result = runner.invoke(app, ["analysis", "generate-fixture-adapter-result"])
    assert result.exit_code == 0, result.output
    assert "\x1b" not in result.output


def test_documented_generation_to_file_matches_committed_example(tmp_path):
    out = tmp_path / "fixture_adapter_result.json"
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "generate-fixture-adapter-result", "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8") == EXAMPLE_JSON_PATH.read_text(encoding="utf-8")


# --- Documentation command surface -------------------------------------------


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
        # Only consider lines inside fenced code that are actual
        # `clulatent ...` invocations (skip prose, comments, and shell
        # variable assignments like `PKG=...`).
        if not line.startswith("clulatent "):
            continue
        tokens = line.split()
        invocations.append(tokens)
    return invocations


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


# --- ffmpeg-gated end-to-end walkthrough -------------------------------------


pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    if not FFMPEG_AVAILABLE:
        pytest.skip("ffmpeg/ffprobe not available in this environment")
    directory = tmp_path_factory.mktemp("clulatent_demo_workflow")
    video_path = directory / "tiny.mp4"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    subprocess.run(command, check=True, capture_output=True)
    return video_path


@pytest.fixture
def valid_package(tmp_path, tiny_video):
    from clu_latent.ingest import ingest_video

    output_path = tmp_path / "demo.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def _write_example(tmp_path: Path) -> Path:
    result_path = tmp_path / "fixture_adapter_result.json"
    result_path.write_text(fixture_adapter_result_json() + "\n", encoding="utf-8")
    return result_path


def _fixture_track_paths(package_path: Path) -> list[Path]:
    return [package_path / "tracks" / f"{lane}.jsonl" for lane in FIXTURE_LANES]


@pytestmark_e2e
def test_dry_run_succeeds_before_import(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(result_path), "--package", str(valid_package)]
    )
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


@pytestmark_e2e
def test_dry_run_does_not_mutate_package(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    before = {p.name for p in (valid_package / "tracks").iterdir()}
    receipt_before = (valid_package / "receipts" / "analyze.jsonl").exists()

    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(result_path), "--package", str(valid_package)]
    )
    assert result.exit_code == 0, result.output

    after = {p.name for p in (valid_package / "tracks").iterdir()}
    assert after == before
    for track_path in _fixture_track_paths(valid_package):
        assert not track_path.exists()
    # dry-run must not have created a receipt or a lock file
    assert (valid_package / "receipts" / "analyze.jsonl").exists() == receipt_before
    status, _report = lock_mod.lock_status(valid_package)
    assert status == "unlocked"


@pytestmark_e2e
def test_import_succeeds_after_dry_run(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()

    dry = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(result_path), "--package", str(valid_package)]
    )
    assert dry.exit_code == 0, dry.output

    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imported.exit_code == 0, imported.output
    assert "total events written: 4" in imported.output


@pytestmark_e2e
def test_import_creates_expected_tracks(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()
    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imported.exit_code == 0, imported.output

    for track_path in _fixture_track_paths(valid_package):
        assert track_path.exists()
        assert len(read_track_file(track_path)) == 1


@pytestmark_e2e
def test_import_creates_analyze_receipt(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()
    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imported.exit_code == 0, imported.output

    receipt_path = valid_package / "receipts" / "analyze.jsonl"
    assert receipt_path.exists()
    lines = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines()]
    assert any(line["adapter_name"] == FIXTURE_ADAPTER_NAME for line in lines)


@pytestmark_e2e
def test_package_validates_after_import(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()
    imported = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert imported.exit_code == 0, imported.output

    # Via the library and via the CLI, both must pass.
    report = validate_package(valid_package)
    assert report.valid is True, report.errors

    validated = runner.invoke(app, ["validate", str(valid_package)])
    assert validated.exit_code == 0, validated.output
    assert "PASS" in validated.output


@pytestmark_e2e
def test_import_shows_up_in_inspect(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()
    runner.invoke(app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)])

    inspected = runner.invoke(app, ["inspect", str(valid_package)])
    assert inspected.exit_code == 0, inspected.output
    for lane in FIXTURE_LANES:
        assert lane in inspected.output


@pytestmark_e2e
def test_repeated_import_is_refused_on_duplicate_ids(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    runner = CliRunner()

    first = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert second.exit_code == 1
    # Rich may soft-wrap the console message; normalize whitespace
    # before checking the refusal phrase.
    normalized = " ".join(second.output.split())
    assert "already exists" in normalized

    # Every fixture lane still holds exactly one event -- the refused
    # re-import wrote nothing new.
    for track_path in _fixture_track_paths(valid_package):
        assert len(read_track_file(track_path)) == 1


@pytestmark_e2e
def test_locked_package_blocks_import(valid_package, tmp_path):
    result_path = _write_example(tmp_path)
    lock_mod.create_lock(valid_package)

    runner = CliRunner()
    result = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(result_path)]
    )
    assert result.exit_code == 1
    assert "integrity lock" in result.output
    for track_path in _fixture_track_paths(valid_package):
        assert not track_path.exists()


@pytestmark_e2e
def test_full_documented_workflow_end_to_end(valid_package, tmp_path):
    """Walk the whole documented sequence in one test, via the CLI."""
    runner = CliRunner()

    # 2. generate
    out_path = tmp_path / "fixture_adapter_result.json"
    gen = runner.invoke(
        app, ["analysis", "generate-fixture-adapter-result", "--output", str(out_path)]
    )
    assert gen.exit_code == 0, gen.output

    # 3. dry-run (writes nothing)
    dry = runner.invoke(
        app, ["analysis", "dry-run-adapter", str(out_path), "--package", str(valid_package)]
    )
    assert dry.exit_code == 0, dry.output
    for track_path in _fixture_track_paths(valid_package):
        assert not track_path.exists()

    # 4. import
    imp = runner.invoke(
        app, ["analysis", "import-adapter-result", str(valid_package), str(out_path)]
    )
    assert imp.exit_code == 0, imp.output

    # 5. validate
    val = runner.invoke(app, ["validate", str(valid_package)])
    assert val.exit_code == 0, val.output

    # 6/7. tracks + receipt exist
    for track_path in _fixture_track_paths(valid_package):
        assert track_path.exists()
    assert (valid_package / "receipts" / "analyze.jsonl").exists()


@pytestmark_e2e
def test_write_adapter_result_library_path_matches_cli(valid_package):
    # The demo doc claims import goes through write_adapter_result; make
    # sure the library entry point produces the same committed tracks.
    result = load_adapter_result_json(EXAMPLE_JSON_PATH)
    write_results = write_adapter_result(valid_package, result)
    assert {w.lane for w in write_results} == set(FIXTURE_LANES)
    assert validate_package(valid_package).valid is True
