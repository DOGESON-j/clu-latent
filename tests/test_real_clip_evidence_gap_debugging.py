"""Tests for Phase 3.11: the real clip evidence gap debugging/audit harness.

Exercises `scripts/debug_real_clip_evidence_gap.py`, a read-only,
argparse-based diagnostic that reports what CLULatent can and cannot
determine about a clip from the package alone. For every package this
codebase produces today, the honest answer is NO -- insufficient
evidence -- and this suite pins that behavior down.

This phase adds no new module, adapter, CLI command, validation rule,
retrieval function, or dependency: the script only reads a package and
calls existing, unmodified library functions
(`validate.validate_package`, the Phase 3.8 audio digest retrieval
primitives, and the manifest loader).

Prefer stable tests over brittle prose tests: assertions check for
exit codes, structured JSON, honest NO answers, gap/risk presence, and
read-only/no-mutation behavior -- not exact wording. No test depends on
a local user media file; the only media is a tiny synthetic ffmpeg clip
generated per-session.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clu_latent import audio_digest_writer as writer_mod
from clu_latent.constants import (
    AUDIO_DIGEST_RECEIPTS_FILE,
    AUDIO_DIGEST_TRACK_FILE,
)
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "debug_real_clip_evidence_gap.py"
DOC_PATH = REPO_ROOT / "docs" / "PHASE_3_11_REAL_CLIP_EVIDENCE_GAP_DEBUGGING.md"
README_PATH = REPO_ROOT / "README.md"

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark_e2e = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

_WRITER_KWARGS = dict(tool_name="demo-audio-digest-workflow", tool_version="0.0.1")

# Copied from audio_digest.py's own constants so a change there is a
# deliberate, visible test update rather than a silent drift.
_FORBIDDEN_PHRASES = (
    "proves intent",
    "proof of intent",
    "manipulation",
    "manipulates",
    "semantic truth",
    "understands the clip",
    "clulatent understands audio",
    "definitely shows",
    "definitely means",
    "real audio analysis shows",
    "the clip has tension",
    "the audio proves",
)


def _load_script_module():
    name = "debug_real_clip_evidence_gap"
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec so the script's dataclasses can resolve their
    # own module during field-annotation introspection.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def script():
    return _load_script_module()


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_evidence_gap_fixture")
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


@pytest.fixture()
def valid_package(tmp_path, tiny_video) -> Path:
    result = ingest_video(tiny_video, tmp_path / "pkg.clulatent")
    return result.package_path


def _run(module, args, capsys):
    code = module.main([str(a) for a in args])
    out = capsys.readouterr().out
    return code, out


def _producer() -> dict:
    return {"name": "demo-audio-digest-workflow", "version": "0.0.1"}


def _feature_series(**overrides) -> dict:
    event = {
        "id": "series_000001",
        "type": "audio_feature_series",
        "t_start_ms": 0,
        "t_end_ms": 2000,
        "producer": _producer(),
        "confidence": 0.9,
        "payload": {
            "feature": "loudness_rms",
            "window_ms": 20,
            "hop_ms": 20,
            "units": "dbfs",
            "data_path": "media/audio_features/loudness_rms.jsonl",
            "summary": {"min": -40.0, "max": -6.0, "mean": -18.5, "count": 3000},
        },
    }
    event.update(overrides)
    return event


def _digest_segment(**overrides) -> dict:
    event = {
        "id": "seg_000001",
        "type": "audio_digest_segment",
        "t_start_ms": 500,
        "t_end_ms": 1500,
        "producer": _producer(),
        "confidence": 0.8,
        "payload": {
            "label": "tension-like buildup candidate",
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "linked_event_ids": ["energy_000012"],
            "linked_feature_series_ids": ["series_000001"],
            "salience": 0.81,
            "recommended_for_llm_context": True,
            "caveats": ["This is evidence, not truth; it does not establish intent."],
        },
    }
    event.update(overrides)
    return event


def _append(package_path, *events, **kwargs) -> None:
    writer_mod.append_audio_digest_events(
        package_path, list(events), **{**_WRITER_KWARGS, **kwargs}
    )


def _write_digest_track_raw(package_path: Path, text: str) -> None:
    (package_path / AUDIO_DIGEST_TRACK_FILE).write_text(text, encoding="utf-8")


def _write_digest_track_records(package_path: Path, *events) -> None:
    _write_digest_track_raw(
        package_path, "\n".join(json.dumps(e) for e in events) + "\n"
    )


# --- 1-2: script exists / has help ------------------------------------------


def test_script_exists():
    assert SCRIPT_PATH.is_file()


def test_script_help_exits_zero_and_mentions_audit(capsys):
    module = _load_script_module()
    with pytest.raises(SystemExit) as exc_info:
        module.main(["--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out.lower()
    assert "\x1b" not in out
    assert ("evidence" in out and "gap" in out) or "audit" in out or "debug" in out


def test_script_imports_cleanly():
    module = _load_script_module()
    assert hasattr(module, "main")
    assert hasattr(module, "audit_package")
    assert hasattr(module, "AuditError")


# --- 3-4: audits a minimal valid package; inventory basics ------------------


@pytestmark_e2e
def test_audits_minimal_valid_package(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Validation: PASS" in out


@pytestmark_e2e
def test_evidence_inventory_includes_basics(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert str(valid_package) in out or "Package id:" in out
    assert "Validation: PASS" in out
    assert "Duration:" in out
    assert "Track inventory:" in out


# --- 5: keyframes present but not interpreted -------------------------------


@pytestmark_e2e
def test_keyframes_present_but_not_interpreted(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Keyframes: present" in out
    # Presence must never be promoted to visual interpretation.
    assert "Can describe visual events: NO" in out


# --- 6: visual gap is explicit ----------------------------------------------


@pytestmark_e2e
def test_visual_gap_is_explicit(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Can describe visual events: NO" in out
    lower = out.lower()
    for lane_word in ("scene", "object", "ocr", "motion", "semantic"):
        assert lane_word in lower


# --- 7: audio presence is not audio understanding ---------------------------


@pytestmark_e2e
def test_audio_presence_is_not_audio_understanding(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Audio: present" in out
    assert "Can describe audio meaning: NO" in out
    assert "the audio proves" not in out.lower()
    assert "real audio analysis shows" not in out.lower()


# --- 8: audio gap is explicit -----------------------------------------------


@pytestmark_e2e
def test_audio_gap_is_explicit(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "real audio feature" in lower
    assert "audio energy" in lower  # recommends real audio adapter work


# --- 9-10: synthetic audio digest is flagged; contamination guard -----------


@pytestmark_e2e
def test_synthetic_audio_digest_is_flagged(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "synthetic" in lower or "demo" in lower
    assert "audio digest" in lower


@pytestmark_e2e
def test_synthetic_contamination_guard(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    # A clear warning must be present.
    assert "synthetic/demo evidence" in lower or "not real clip" in lower
    # And no false real-analysis claims.
    assert "real audio analysis shows" not in lower
    assert "the clip has tension" not in lower
    assert "the audio proves" not in lower
    assert "clulatent understands audio" not in lower


# --- 11: answer "what happens?" honestly ------------------------------------


@pytestmark_e2e
def test_answers_what_happens_honestly(script, valid_package, capsys):
    code, out = _run(script, [valid_package, "--question", "what happens in this clip?"], capsys)
    assert code == 0
    lower = out.lower()
    assert "insufficient evidence" in lower or "cannot determine" in lower


# --- 12: forbidden language --------------------------------------------------


@pytestmark_e2e
def test_output_avoids_forbidden_language(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    _, out = _run(script, [valid_package], capsys)
    lower = out.lower()
    for phrase in _FORBIDDEN_PHRASES:
        assert phrase not in lower, phrase


# --- 13: report trust language ----------------------------------------------


@pytestmark_e2e
def test_output_preserves_evidence_not_truth_language(script, valid_package, capsys):
    _, out = _run(script, [valid_package], capsys)
    lower = out.lower()
    assert "evidence" in lower
    assert "not truth" in lower


# --- 14: missing audio digest handled cleanly -------------------------------


@pytestmark_e2e
def test_missing_audio_digest_handled_cleanly(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "no audio digest track" in out.lower()


# --- 15: missing/empty semantic events handled cleanly ----------------------


@pytestmark_e2e
def test_empty_semantic_events_handled_cleanly(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Semantic evidence: empty" in out


# --- 16: empty speech events handled cleanly --------------------------------


@pytestmark_e2e
def test_empty_speech_events_handled_cleanly(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "Speech evidence: empty" in out
    # No transcript-based inference of clip meaning.
    assert "Can answer" in out
    assert "what happens in this clip" in out


# --- 17: read-only behavior --------------------------------------------------


@pytestmark_e2e
def test_audit_is_read_only(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    index_path = valid_package / manifest.index.file

    manifest_before = (valid_package / "manifest.json").read_bytes()
    track_before = (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes()
    receipt_before = (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).read_bytes()
    index_before = index_path.read_bytes()

    _run(script, [valid_package], capsys)
    _run(script, [valid_package, "--json"], capsys)
    _run(script, [valid_package, "--question", "what happens in this clip?"], capsys)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes() == track_before
    assert (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).read_bytes() == receipt_before
    assert index_path.read_bytes() == index_before


@pytestmark_e2e
def test_audit_does_not_create_receipt(script, valid_package, capsys):
    # No audio digest appended -> no receipt should ever be created.
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()
    _run(script, [valid_package], capsys)
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()


# --- 18-19: corrupt / bad JSONL reported safely -----------------------------


@pytestmark_e2e
def test_corrupted_audio_digest_reported_safely(script, valid_package, capsys):
    _append(valid_package, _feature_series())
    # Overwrite with a structurally-invalid record (bad timestamp type).
    _write_digest_track_records(valid_package, _feature_series(t_start_ms="not-an-int"))
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "invalid" in lower or "unreadable" in lower


@pytestmark_e2e
def test_bad_jsonl_line_reported_safely(script, valid_package, capsys):
    _append(valid_package, _feature_series())
    _write_digest_track_raw(valid_package, "this is not json\n")
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "invalid" in lower or "unreadable" in lower


# --- 20: duplicate ids reported safely --------------------------------------


@pytestmark_e2e
def test_duplicate_audio_digest_ids_reported_safely(script, valid_package, capsys):
    _append(valid_package, _feature_series())
    _write_digest_track_records(
        valid_package, _feature_series(id="dup"), _feature_series(id="dup")
    )
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "invalid" in lower or "unreadable" in lower
    # No semantic inference despite the presence of digest records.
    assert "Can answer" in out


# --- 21: path traversal in data_path reported safely ------------------------


@pytestmark_e2e
def test_path_traversal_in_data_path_reported_safely(script, valid_package, capsys):
    _append(valid_package, _feature_series())
    evil = _feature_series()
    evil["payload"]["data_path"] = "../../etc/passwd"
    _write_digest_track_records(valid_package, evil)
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "invalid" in lower or "unreadable" in lower or "suspicious" in lower


# --- 22: dense arrays in packet rejected/warned -----------------------------


@pytestmark_e2e
def test_dense_arrays_rejected_or_warned(script, valid_package, capsys):
    _append(valid_package, _feature_series())
    dense = _feature_series()
    dense["payload"]["values"] = [0.1, 0.2, 0.3, 0.4]  # forbidden raw dense array
    _write_digest_track_records(valid_package, dense)
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    lower = out.lower()
    assert "invalid" in lower or "unreadable" in lower or "not safe" in lower


# --- 23: missing receipt warning --------------------------------------------


@pytestmark_e2e
def test_missing_receipt_warning(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment(), write_receipt=False)
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "receipt" in out.lower()


# --- 24: manifest mismatch warning ------------------------------------------


@pytestmark_e2e
def test_manifest_mismatch_warning(script, valid_package, capsys):
    # A digest track file exists on disk, but no manifest descriptor declares it.
    _write_digest_track_records(valid_package, _feature_series())
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    assert all(t.name != writer_mod.AUDIO_DIGEST_TRACK_NAME for t in manifest.tracks)
    code, out = _run(script, [valid_package], capsys)
    assert code == 0
    assert "manifest" in out.lower()


# --- 25: bounded output ------------------------------------------------------


@pytestmark_e2e
def test_output_is_bounded(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    _, out = _run(script, [valid_package], capsys)
    assert len(out) < 8000
    # It must not dump raw dense arrays or full payloads.
    assert "loudness_rms.jsonl" not in out
    assert "[0.1, 0.2" not in out


# --- 26: JSON output mode ----------------------------------------------------


@pytestmark_e2e
def test_json_output_mode(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    code, out = _run(script, [valid_package, "--json"], capsys)
    assert code == 0
    data = json.loads(out)
    for key in (
        "validation_status",
        "can_describe_visual_events",
        "can_describe_audio_meaning",
        "can_answer_what_happens",
        "evidence_inventory",
        "gaps",
        "risks",
        "recommended_next_work",
    ):
        assert key in data, key


# --- 27: JSON says NO for current evidence ----------------------------------


@pytestmark_e2e
def test_json_says_no_for_current_evidence(script, valid_package, capsys):
    _append(valid_package, _feature_series(), _digest_segment())
    code, out = _run(script, [valid_package, "--json"], capsys)
    data = json.loads(out)
    assert data["can_describe_visual_events"] is False
    assert data["can_describe_audio_meaning"] is False
    assert data["can_answer_what_happens"] is False


# --- 28: recommended next work exists ---------------------------------------


@pytestmark_e2e
def test_recommended_next_work_exists(script, valid_package, capsys):
    code, out = _run(script, [valid_package], capsys)
    lower = out.lower()
    assert "keyframe preview" in lower
    assert "audio energy" in lower
    assert "scene-change" in lower or "scene change" in lower
    assert "ocr" in lower
    assert "motion" in lower


@pytestmark_e2e
def test_recommended_next_work_in_json(script, valid_package, capsys):
    code, out = _run(script, [valid_package, "--json"], capsys)
    data = json.loads(out)
    assert len(data["recommended_next_work"]) >= 5


# --- missing package handled with clean error -------------------------------


def test_missing_package_fails_cleanly(script, tmp_path, capsys):
    code = script.main([str(tmp_path / "does_not_exist.clulatent")])
    assert code == 1
    err = capsys.readouterr().err
    assert "Error:" in err


# --- 29-30: docs / README / suite -------------------------------------------


def test_doc_exists():
    assert DOC_PATH.is_file()


def test_doc_mentions_evidence_gap_and_script_path():
    text = DOC_PATH.read_text(encoding="utf-8")
    lower = text.lower()
    assert "evidence gap" in lower or "evidence-gap" in lower
    assert "scripts/debug_real_clip_evidence_gap.py" in text


def test_doc_mentions_read_only_and_no_audio_understanding():
    lower = DOC_PATH.read_text(encoding="utf-8").lower()
    assert "read-only" in lower or "read only" in lower
    assert "does not" in lower and "audio" in lower


def test_readme_includes_phase_3_11_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
