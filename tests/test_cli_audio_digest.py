"""Tests for Phase 3.6: the `clulatent audio-digest <subcommand>` CLI
commands built on the Phase 3.4 schema primitives and the Phase 3.5
writer.

ffmpeg-gated (for every test that needs a real ingested package to
write into): mirrors test_cli_analysis.py / test_audio_digest_writer.py.
`types` and `validate-file` never need a package, so those tests run
unconditionally.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import audio_digest as audio_digest_mod
from clu_latent import lock as lock_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.tracks import read_track_file

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_6_AUDIO_DIGEST_CLI_COMMANDS.md"


def _producer() -> dict:
    return {"name": "test-producer", "version": "0.0.1"}


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
        "t_start_ms": 12000,
        "t_end_ms": 18500,
        "producer": _producer(),
        "confidence": 0.8,
        "payload": {
            "label": "tension-like buildup candidate",
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "linked_event_ids": ["energy_000012", "rhythm_000006"],
            "linked_feature_series_ids": ["series_000001"],
            "salience": 0.81,
            "recommended_for_llm_context": True,
            "caveats": ["This is evidence, not truth; it does not establish intent."],
        },
    }
    event.update(overrides)
    return event


def _context_packet(**overrides) -> dict:
    event = {
        "id": "packet_000001",
        "type": "audio_llm_context_packet",
        "t_start_ms": 0,
        "t_end_ms": 120000,
        "producer": _producer(),
        "confidence": 0.75,
        "payload": {
            "time_range": {"t_start_ms": 0, "t_end_ms": 120000},
            "budget_tokens_estimate": 500,
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "top_evidence": ["compression/limiting evidence in segment seg_000001"],
            "warnings": ["Some lower-salience segments were omitted."],
            "caveats": [
                "This packet describes evidence, not truth, and does not establish "
                "intent, meaning, or a listener's emotional response."
            ],
            "linked_event_ids": ["energy_000012"],
            "linked_digest_segment_ids": ["seg_000001"],
            "linked_feature_series_ids": ["series_000001"],
            "omitted_detail_reason": "Lower-salience segments omitted to stay within budget.",
            "retrieval_hints": {
                "by_time_range": "retrieve tracks/audio_digest_segment.jsonl records overlapping the range",
                "by_evidence_id": "retrieve any linked id by exact match",
            },
        },
    }
    event.update(overrides)
    return event


# --- imports / help -----------------------------------------------------------


def test_cli_imports_cleanly():
    assert app is not None


def test_cli_audio_digest_help_exists():
    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "--help"])
    assert result.exit_code == 0, result.output
    assert "types" in result.output
    assert "validate-file" in result.output
    assert "append" in result.output
    assert "append-file" in result.output


# --- types (never needs ffmpeg or a package) ----------------------------------


def test_cli_audio_digest_types_lists_all_supported_types():
    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "types"])
    assert result.exit_code == 0, result.output
    for name in audio_digest_mod.AUDIO_DIGEST_RECORD_TYPES:
        assert name in result.output


# --- validate-file (never needs ffmpeg or a package) --------------------------


def test_cli_audio_digest_validate_file_valid_feature_series_passes(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_audio_digest_validate_file_valid_digest_segment_passes(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_digest_segment()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_audio_digest_validate_file_valid_context_packet_passes(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_context_packet()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_cli_audio_digest_validate_file_invalid_json_fails(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("not json at all\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 1


def test_cli_audio_digest_validate_file_unsupported_type_fails(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series(type="not_a_real_type")) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_audio_digest_validate_file_rejects_raw_dense_arrays(tmp_path):
    event = _feature_series()
    event["payload"]["values"] = [1.0, 2.0, 3.0]
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(event) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_audio_digest_validate_file_does_not_mutate_or_create_files(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 0, result.output
    assert not any(tmp_path.rglob("manifest.json"))
    assert not any(tmp_path.rglob("*.jsonl.tmp"))
    assert not (tmp_path / "receipts").exists()


def test_cli_audio_digest_validate_file_missing_file_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.jsonl"
    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(missing)])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_cli_audio_digest_validate_file_empty_file_clean_error(tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "validate-file", str(events_file)])
    assert result.exit_code == 1
    assert "empty" in result.output


# --- append / append-file (need a real ingested package) ---------------------


pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_cli_audio_digest_fixture")
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
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def test_cli_audio_digest_append_single_valid_event(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "audio-digest",
            "append",
            str(valid_package),
            "--event-json",
            json.dumps(_feature_series()),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Audio digest events written" in result.output

    events = read_track_file(valid_package / "tracks/audio_digest_events.jsonl")
    assert len(events) == 1
    assert events[0].id == "series_000001"


def test_cli_audio_digest_append_file_writes_multiple_valid_events(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            json.dumps(event)
            for event in (_feature_series(), _digest_segment(), _context_packet())
        )
        + "\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 0, result.output

    events = read_track_file(valid_package / "tracks/audio_digest_events.jsonl")
    assert len(events) == 3
    assert {event.type for event in events} == {
        "audio_feature_series",
        "audio_digest_segment",
        "audio_llm_context_packet",
    }


def test_cli_audio_digest_append_file_writes_receipt(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 0, result.output

    receipt_path = valid_package / "receipts" / "audio_digest.jsonl"
    assert receipt_path.exists()
    receipt_lines = receipt_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(receipt_lines) == 1
    receipt = json.loads(receipt_lines[0])
    assert receipt["operation"] == "append_audio_digest_events"
    assert receipt["status"] == "success"
    assert receipt["event_count"] == 1


def test_cli_audio_digest_append_file_updates_manifest_descriptor(valid_package, tmp_path):
    manifest_before = Manifest.from_json_file(valid_package / "manifest.json")
    assert not any(t.name == "audio_digest_events" for t in manifest_before.tracks)

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 0, result.output

    manifest_after = Manifest.from_json_file(valid_package / "manifest.json")
    descriptor = next(t for t in manifest_after.tracks if t.name == "audio_digest_events")
    assert descriptor.file == "tracks/audio_digest_events.jsonl"
    assert descriptor.record_count == 1


def test_cli_audio_digest_append_file_rejects_empty_file(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 1
    assert "empty" in result.output
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


def test_cli_audio_digest_append_file_rejects_invalid_jsonl(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        json.dumps(_feature_series()) + "\nnot valid json\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 1
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


def test_cli_audio_digest_append_file_rejects_duplicate_ids(valid_package, tmp_path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(json.dumps(_feature_series()) for _ in range(2)) + "\n", encoding="utf-8"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 1
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


def test_cli_audio_digest_append_file_rejects_locked_package(valid_package, tmp_path):
    lock_mod.create_lock(valid_package)
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series()) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 1
    assert "integrity lock" in result.output
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


def test_cli_audio_digest_append_file_does_not_mutate_package_on_failure(valid_package, tmp_path):
    manifest_before = (valid_package / "manifest.json").read_bytes()

    events_file = tmp_path / "events.jsonl"
    events_file.write_text(json.dumps(_feature_series(type="not_a_real_type")) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "append-file", str(valid_package), str(events_file)])
    assert result.exit_code == 1
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()
    assert (valid_package / "manifest.json").read_bytes() == manifest_before


def test_cli_audio_digest_append_rejects_malformed_json(valid_package):
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "append", str(valid_package), "--event-json", "not-json"],
    )
    assert result.exit_code == 1
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


def test_cli_audio_digest_append_on_nonexistent_package_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "append", str(missing), "--event-json", json.dumps(_feature_series())],
    )
    assert result.exit_code == 1
    assert "Package not found" in result.output


def test_cli_audio_digest_append_sanitizes_control_character_errors(valid_package):
    ansi_payload = "\x1b[31mred\x1b[0m"
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "audio-digest",
            "append",
            str(valid_package),
            "--event-json",
            json.dumps(_feature_series(type=f"not_a_real_type{ansi_payload}")),
        ],
    )
    assert result.exit_code == 1
    assert "\x1b[31m" not in result.output
    assert not (valid_package / "tracks/audio_digest_events.jsonl").exists()


# --- docs / README -------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_readme_includes_phase_3_6_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
