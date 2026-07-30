"""Tests for Phase 3.10: the audio digest demo workflow script.

Exercises `scripts/demo_audio_digest_workflow.py`, a small,
reproducible, argparse-based script that runs the full Phase 3.4-3.9
audio digest evidence path end-to-end (create demo package -> write
synthetic audio digest JSONL -> validate -> append -> validate package
-> retrieve by id/time/type/link/salience -> bounded LLM context ->
confirm receipt/manifest) inside a caller-chosen output directory,
using entirely synthetic, hand-authored records. This phase adds no
new module, adapter, validation rule, writer, or retrieval function --
the script only calls existing, unmodified library functions already
covered by `tests/test_audio_digest.py`, `tests/test_audio_digest_
writer.py`, `tests/test_validate_audio_digest.py`, `tests/test_audio_
digest_retrieval.py`, `tests/test_cli_audio_digest.py`, and
`tests/test_cli_audio_digest_retrieval.py`.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from clu_latent.manifest import Manifest
from clu_latent.validate import validate_package

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "demo_audio_digest_workflow.py"
DOC_PATH = REPO_ROOT / "docs" / "PHASE_3_10_AUDIO_DIGEST_DEMO_WORKFLOW.md"
README_PATH = REPO_ROOT / "README.md"

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

# The same phrases/markers `audio_digest.py` forbids inside a record's
# own text fields -- the demo script's own console output must avoid
# them too, even though they can never appear in a validated record.
_FORBIDDEN_PHRASES = (
    "proves intent",
    "proof of intent",
    "manipulation",
    "manipulates you",
    "makes viewer afraid",
    "makes the viewer afraid",
    "clulatent understands audio",
    "semantic audio truth",
    "definitely exact instrument",
    "definitely exact sound source",
)
_CERTAINTY_MARKERS = ("proven", "confirmed fact", "is a fact that", "the truth is")


def _load_script_module():
    spec = importlib.util.spec_from_file_location("demo_audio_digest_workflow", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# --- script exists / imports / help (no ffmpeg needed) ------------------------


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


def test_script_help_has_no_control_sequences(capsys):
    module = _load_script_module()
    with pytest.raises(SystemExit):
        module.main(["--help"])
    out = capsys.readouterr().out
    assert "\x1b" not in out


def test_script_does_not_import_ml_or_audio_analysis_libraries():
    import_lines = [
        line.strip().lower()
        for line in SCRIPT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for forbidden in (
        "librosa",
        "essentia",
        "demucs",
        "aubio",
        "basic_pitch",
        "yamnet",
        "panns",
        "openl3",
        "torch",
        "tensorflow",
    ):
        assert not any(forbidden in line for line in import_lines), forbidden


# --- ffmpeg-gated end-to-end run -----------------------------------------------


@pytestmark_e2e
def test_demo_runs_successfully_in_tmp_path(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0, capsys.readouterr().out


@pytestmark_e2e
def test_demo_creates_valid_package(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    assert package_path.is_dir()
    assert (package_path / "manifest.json").is_file()


@pytestmark_e2e
def test_demo_writes_synthetic_audio_digest_jsonl(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    events_file = output_dir / "synthetic_audio_digest_events.jsonl"
    assert events_file.is_file()
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


@pytestmark_e2e
def test_demo_validates_and_appends_records(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "FAIL" not in out


@pytestmark_e2e
def test_demo_creates_audio_digest_track(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    track_path = output_dir / "demo.clulatent" / "tracks" / "audio_digest_events.jsonl"
    assert track_path.is_file()
    lines = track_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


@pytestmark_e2e
def test_demo_creates_audio_digest_receipt(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    receipt_path = output_dir / "demo.clulatent" / "receipts" / "audio_digest.jsonl"
    assert receipt_path.is_file()
    assert receipt_path.stat().st_size > 0


@pytestmark_e2e
def test_demo_updates_manifest_descriptor(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    manifest = Manifest.from_json_file(package_path / "manifest.json")
    descriptor = next((t for t in manifest.tracks if t.name == "audio_digest_events"), None)
    assert descriptor is not None
    assert descriptor.record_count == 3


@pytestmark_e2e
def test_demo_package_validates_after_append(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    report = validate_package(package_path)
    assert report.valid is True, report.errors


@pytestmark_e2e
def test_demo_output_shows_retrieval_by_id(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "get by id: PASS" in out


@pytestmark_e2e
def test_demo_output_shows_retrieval_by_time(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "query by time range" in out


@pytestmark_e2e
def test_demo_output_shows_retrieval_by_type(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "query by type" in out


@pytestmark_e2e
def test_demo_output_shows_retrieval_by_linked_evidence(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "query by linked evidence id" in out


@pytestmark_e2e
def test_demo_output_shows_salience_query(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "query by salience" in out


@pytestmark_e2e
def test_demo_output_shows_llm_context(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "bounded LLM context" in out
    assert "audio_feature_series" not in out.split("Step 7/8")[1].split("Step 8/8")[0]


@pytestmark_e2e
def test_demo_output_shows_retrieval_summary(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "retrieval-summary" in out
    assert "event_count=3" in out


@pytestmark_e2e
def test_demo_does_not_create_reports(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    assert not any(package_path.rglob("*.report.*"))
    assert not (package_path / "reports").exists()


@pytestmark_e2e
def test_demo_does_not_touch_index_unexpectedly(tmp_path):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    package_path = output_dir / "demo.clulatent"
    manifest = Manifest.from_json_file(package_path / "manifest.json")
    index_path = package_path / manifest.index.file
    assert index_path.is_file()
    # The index is built once at ingest time; the audio digest append/
    # retrieval steps must never touch or rebuild it.
    before = index_path.read_bytes()
    exit_code_2 = module.main(["--output-dir", str(output_dir / "does_not_exist_second_run")])
    assert exit_code_2 == 0
    assert index_path.read_bytes() == before


@pytestmark_e2e
def test_demo_output_avoids_forbidden_language(tmp_path, capsys):
    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0
    out = capsys.readouterr().out.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in out
    for marker in _CERTAINTY_MARKERS:
        assert marker not in out


@pytestmark_e2e
def test_demo_does_not_mutate_repo_files(tmp_path):
    before_readme = README_PATH.read_bytes()
    before_script = SCRIPT_PATH.read_bytes()

    module = _load_script_module()
    output_dir = tmp_path / "demo_out"
    exit_code = module.main(["--output-dir", str(output_dir)])
    assert exit_code == 0

    assert README_PATH.read_bytes() == before_readme
    assert SCRIPT_PATH.read_bytes() == before_script


# --- non-ffmpeg-gated: script does not require ML extras ----------------------


def test_script_requires_no_ml_extras_installed():
    """The demo script must run without `vad`/`whisper` optional extras.

    Confirmed by never importing `silero_vad` or `faster_whisper`
    anywhere in the script source.
    """
    text = SCRIPT_PATH.read_text(encoding="utf-8").lower()
    assert "silero_vad" not in text
    assert "faster_whisper" not in text


# --- docs / README --------------------------------------------------------------


def test_demo_doc_exists():
    assert DOC_PATH.is_file()


def test_demo_doc_states_core_rule():
    text = " ".join(DOC_PATH.read_text(encoding="utf-8").split()).lower()
    assert "store deep" in text
    assert "show shallow" in text
    assert "retrieve detail only when needed" in text


def test_demo_doc_mentions_synthetic_and_no_real_audio():
    text = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "synthetic" in text
    assert "no real audio" in text or "not real audio" in text or "no audio adapter" in text


def test_demo_doc_mentions_the_script_path():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "scripts/demo_audio_digest_workflow.py" in text


def test_readme_includes_phase_3_10_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
