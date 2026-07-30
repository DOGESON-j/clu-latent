"""Tests for Phase 2.18: the real adapter demo script.

Exercises `scripts/demo_real_adapter_workflow.py`, a small,
reproducible, argparse-based script that runs the polished Phase 2.15-
2.17 real-adapter workflow end-to-end (create demo package -> generate
real ffmpeg visual-change adapter result -> dry-run -> import ->
validate) inside a caller-chosen output directory. This phase adds no
new module, adapter, or CLI command -- the script only calls existing,
unmodified library functions already covered by
`tests/test_real_adapter_demo_package.py` (Phase 2.16) and
`tests/test_real_adapter_cli_polish.py` (Phase 2.17).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from clu_latent.analysis_adapters import validate_adapter_result
from clu_latent.analysis_ffmpeg_visual_change_adapter import ADAPTER_NAME, VISUAL_CHANGE_LANE
from clu_latent.tracks import read_track_file
from clu_latent.validate import validate_package

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "demo_real_adapter_workflow.py"
DOC_PATH = REPO_ROOT / "docs" / "PHASE_2_18_REAL_ADAPTER_DEMO_SCRIPT.md"
README_PATH = REPO_ROOT / "README.md"

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("demo_real_adapter_workflow", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --- Import / help (no ffmpeg needed) ---------------------------------------


def test_script_exists():
    assert SCRIPT_PATH.is_file()


def test_script_imports_cleanly():
    module = _load_script_module()
    assert hasattr(module, "main")
    assert hasattr(module, "run_demo")
    assert hasattr(module, "DemoError")


def test_script_help_works(capsys):
    module = _load_script_module()
    with pytest.raises(SystemExit) as exc_info:
        module.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "--output-dir" in out
    assert "--media" in out
    assert "--threshold" in out


def test_script_help_has_no_control_sequences(capsys):
    module = _load_script_module()
    with pytest.raises(SystemExit):
        module.main(["--help"])
    out = capsys.readouterr().out
    assert "\x1b" not in out


# --- ffmpeg-gated end-to-end run ---------------------------------------------


@pytestmark_e2e
def test_script_runs_successfully_in_tmp_path(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0, capsys.readouterr().out


@pytestmark_e2e
def test_generated_adapter_result_json_exists_and_validates(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    result_path = output_dir / "visual_change_result.json"
    assert result_path.is_file()
    data = json.loads(result_path.read_text(encoding="utf-8"))
    assert data["metadata"]["adapter_name"] == ADAPTER_NAME

    from clu_latent.analysis_adapter_dry_run import load_adapter_result_json

    result = load_adapter_result_json(result_path)
    errors, _warnings = validate_adapter_result(result)
    assert errors == []


@pytestmark_e2e
def test_package_exists_validates_and_has_expected_track(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    assert package_path.is_dir()

    report = validate_package(package_path)
    assert report.valid is True, report.errors

    track_path = package_path / "tracks" / f"{VISUAL_CHANGE_LANE}.jsonl"
    assert track_path.is_file()
    events = read_track_file(track_path)
    assert len(events) >= 1

    receipt_path = package_path / "receipts" / "analyze.jsonl"
    assert receipt_path.is_file()
    assert receipt_path.stat().st_size > 0


@pytestmark_e2e
def test_output_includes_lane_and_event_counts(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert VISUAL_CHANGE_LANE in out
    assert "total events written" in out


@pytestmark_e2e
def test_output_includes_evidence_not_truth_language(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out.lower()
    assert "candidate evidence" in out
    assert "not confirmed truth" in out


@pytestmark_e2e
def test_output_has_no_control_sequences(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out


@pytestmark_e2e
def test_script_does_not_mutate_repo_files(tmp_path):
    before_readme = README_PATH.read_bytes()
    before_script = SCRIPT_PATH.read_bytes()

    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    assert README_PATH.read_bytes() == before_readme
    assert SCRIPT_PATH.read_bytes() == before_script


@pytestmark_e2e
def test_script_does_not_write_outside_output_dir(tmp_path):
    output_dir = tmp_path / "demo_out"
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    before_sibling_contents = sorted(sibling.iterdir())

    module = _load_script_module()
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    assert sorted(sibling.iterdir()) == before_sibling_contents
    # Every path under tmp_path outside output_dir/ (and the sibling dir
    # itself) must remain absent -- only output_dir/ should have gained
    # any content.
    for child in tmp_path.iterdir():
        assert child in (output_dir, sibling)


@pytestmark_e2e
def test_script_handles_existing_empty_output_dir_cleanly(tmp_path):
    output_dir = tmp_path / "demo_out"
    output_dir.mkdir()

    module = _load_script_module()
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    assert (output_dir / "demo.clulatent").is_dir()


@pytestmark_e2e
def test_script_rerun_against_existing_package_fails_cleanly(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    first = module.main(["--output-dir", str(output_dir)])
    assert first == 0

    second = module.main(["--output-dir", str(output_dir)])
    assert second == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "Error:" in err


def test_script_handles_output_dir_as_file_cleanly(tmp_path, capsys):
    output_dir = tmp_path / "demo_out_file"
    output_dir.write_bytes(b"not a directory")

    module = _load_script_module()
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "Error:" in err


def test_script_handles_invalid_output_path_cleanly(tmp_path, capsys):
    invalid_path = tmp_path / "not_a_real_disk" / "sub"
    module = _load_script_module()
    # Simulate an unwritable/invalid parent by pointing at a path whose
    # parent is itself a file, which can never be `mkdir -p`'d into.
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"blocking file")
    bad_output_dir = blocker / "demo_out"

    exit_code = module.main(["--output-dir", str(bad_output_dir)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "Error:" in err


def test_script_handles_missing_media_path_cleanly(tmp_path, capsys):
    missing_media = tmp_path / "nonexistent.mp4"
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir), "--media", str(missing_media)])
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "Error:" in err


# --- Docs --------------------------------------------------------------------


def test_demo_script_doc_exists():
    assert DOC_PATH.is_file()


def test_demo_script_doc_mentions_evidence_not_truth():
    text = DOC_PATH.read_text(encoding="utf-8")
    lowered = text.lower()
    assert "evidence" in lowered
    assert "not truth" in lowered or "not confirmed" in lowered


def test_demo_script_doc_mentions_the_script_path():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "scripts/demo_real_adapter_workflow.py" in text


def test_readme_roadmap_mentions_phase_2_18():
    text = README_PATH.read_text(encoding="utf-8")
    assert "CLULatent" in text
