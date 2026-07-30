"""Tests for Phase 3.7: audio digest track/receipt validation wired into
`clulatent validate` / `validate_package`.

Uses the Phase 3.5 writer (`audio_digest_writer.append_audio_digest_events`)
to produce a genuinely valid on-disk audio digest track + manifest entry
(and, where noted, a receipt), then tampers with the raw track/receipt
file content the same way `tests/test_validate_analysis_lanes.py` already
tampers lane track files -- the writer itself refuses to write an invalid
event, so an on-disk invalid record can only be produced by editing the
file directly after a valid write.

ffmpeg-gated: mirrors test_validate_analysis_lanes.py / test_audio_digest_writer.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import audio_digest_writer as writer_mod
from clu_latent.cli import app
from clu_latent.constants import AUDIO_DIGEST_RECEIPTS_FILE, AUDIO_DIGEST_TRACK_FILE
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_7_AUDIO_DIGEST_PACKAGE_VALIDATION_INTEGRATION.md"

_WRITER_KWARGS = dict(tool_name="test-digest-writer", tool_version="0.0.1")


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_validate_audio_digest_fixture")
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


def _context_packet(**overrides) -> dict:
    event = {
        "id": "packet_000001",
        "type": "audio_llm_context_packet",
        "t_start_ms": 0,
        "t_end_ms": 2000,
        "producer": _producer(),
        "confidence": 0.75,
        "payload": {
            "time_range": {"t_start_ms": 0, "t_end_ms": 2000},
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


def _tamper_track_file(package_path, *events) -> None:
    """Overwrite tracks/audio_digest_events.jsonl with raw records.

    Bypasses the Phase 3.5 writer's own validation entirely -- the only
    way to get a genuinely invalid record onto disk, since the writer
    refuses to write one.
    """
    track_path = package_path / AUDIO_DIGEST_TRACK_FILE
    lines = "\n".join(json.dumps(event) for event in events)
    track_path.write_text(lines + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- happy paths -----------------------------------------------------------


def test_validate_ignores_absent_audio_digest_track(valid_package):
    manifest_before = valid_package / "manifest.json"
    assert "audio_digest_events" not in manifest_before.read_text(encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is True
    assert report.errors == []


def test_validate_passes_with_valid_feature_series_track(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


def test_validate_passes_with_valid_digest_segment_track(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


def test_validate_passes_with_valid_context_packet_track(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_context_packet()], **_WRITER_KWARGS)

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


def test_validate_passes_with_mixed_track(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series(), _digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


# --- record-level failure modes ---------------------------------------------


def test_validate_fails_with_invalid_audio_digest_record(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(valid_package, _feature_series(t_start_ms="not-an-int"))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("t_start_ms" in err for err in report.errors)


def test_validate_fails_with_unsupported_audio_digest_type(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(valid_package, _feature_series(type="not_a_real_digest_type"))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("not a supported audio digest record type" in err for err in report.errors)


def test_validate_fails_with_duplicate_audio_digest_ids(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(
        valid_package,
        _feature_series(id="dup_000001"),
        _digest_segment(id="dup_000001"),
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("duplicate audio digest record id" in err for err in report.errors)


def test_validate_fails_with_raw_dense_array_in_payload(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    bad_event = _feature_series()
    bad_event["payload"]["values"] = [1, 2, 3]
    _tamper_track_file(valid_package, bad_event)

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("raw dense-array field" in err for err in report.errors)


def test_validate_fails_with_forbidden_truth_language(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)
    _tamper_track_file(
        valid_package,
        _digest_segment(payload={**_digest_segment()["payload"], "label": "This is definitely a fact."}),
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("truthy certainty marker" in err for err in report.errors)


def test_validate_fails_with_absolute_data_path(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    bad_event = _feature_series()
    bad_event["payload"]["data_path"] = "/etc/passwd"
    _tamper_track_file(valid_package, bad_event)

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("payload.data_path" in err for err in report.errors)


def test_validate_fails_with_parent_traversal_data_path(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    bad_event = _feature_series()
    bad_event["payload"]["data_path"] = "../../etc/escape.jsonl"
    _tamper_track_file(valid_package, bad_event)

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("payload.data_path" in err for err in report.errors)


def test_validate_fails_with_timestamp_beyond_duration(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(valid_package, _feature_series(t_start_ms=0, t_end_ms=999_999))

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("exceeds source duration_ms" in err for err in report.errors)


# --- manifest-consistency ----------------------------------------------------


def test_validate_fails_when_manifest_references_missing_audio_digest_track_file(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    (valid_package / AUDIO_DIGEST_TRACK_FILE).unlink()

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("track file listed in manifest is missing" in err for err in report.errors)


def test_validate_ignores_audio_digest_track_without_manifest_descriptor(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest_path = valid_package / "manifest.json"
    manifest_dict = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_dict["tracks"] = [
        t for t in manifest_dict["tracks"] if t["name"] != "audio_digest_events"
    ]
    manifest_path.write_text(json.dumps(manifest_dict, indent=2), encoding="utf-8")
    assert (valid_package / AUDIO_DIGEST_TRACK_FILE).exists()

    # Following the existing analysis-lane convention: validate_package()
    # only ever inspects tracks declared in the manifest, so a track file
    # present on disk but not referenced by the manifest is silently
    # ignored rather than causing a failure.
    report = validate_package(valid_package)
    assert report.valid is True, report.errors


# --- receipt validation -------------------------------------------------------


def test_validate_handles_audio_digest_receipt_if_present(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    assert (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()

    report = validate_package(valid_package)
    assert report.valid is True, report.errors


def test_validate_fails_cleanly_on_malformed_audio_digest_receipt(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    receipts_path = valid_package / AUDIO_DIGEST_RECEIPTS_FILE
    bad_receipt = {
        # tool_name missing entirely -- required field.
        "operation": "append_audio_digest_events",
        "tool_version": "0.0.1",
        "status": "not-a-real-status",
        "output_track": AUDIO_DIGEST_TRACK_FILE,
        "event_count": 1,
    }
    receipts_path.write_text(json.dumps(bad_receipt) + "\n", encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("tool_name is required" in err for err in report.errors)
    assert any("status must be one of" in err for err in report.errors)


def test_validate_fails_cleanly_on_malformed_audio_digest_receipt_jsonl(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    receipts_path = valid_package / AUDIO_DIGEST_RECEIPTS_FILE
    receipts_path.write_text("{not valid json\n", encoding="utf-8")

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("invalid JSON" in err for err in report.errors)


def test_validate_fails_with_wrong_output_track_path_in_receipt(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    receipts_path = valid_package / AUDIO_DIGEST_RECEIPTS_FILE
    entries = _read_jsonl(receipts_path)
    entries[0]["output_track"] = "tracks/wrong_track.jsonl"
    receipts_path.write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8"
    )

    report = validate_package(valid_package)
    assert report.valid is False
    assert any("output_track must be" in err for err in report.errors)


# --- non-mutation / non-index / non-report -----------------------------------


def test_validate_does_not_mutate_package(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest_before = (valid_package / "manifest.json").read_bytes()
    track_before = (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes()
    receipt_before = (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).read_bytes()

    validate_package(valid_package)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes() == track_before
    assert (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).read_bytes() == receipt_before


def test_validate_does_not_touch_index(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    index_path = valid_package / manifest.index.file
    before = index_path.read_bytes()

    validate_package(valid_package)

    assert index_path.read_bytes() == before


def test_validate_does_not_create_receipts_or_reports(valid_package):
    reports_dir = valid_package / "reports"
    assert not reports_dir.exists()

    validate_package(valid_package)

    assert not reports_dir.exists()
    assert not (valid_package / "receipts" / "validate.jsonl").exists()


# --- interaction with existing review validation -----------------------------


def test_validate_review_track_still_validates_alongside_audio_digest(valid_package):
    from clu_latent import review_writer as review_writer_mod
    from clu_latent.constants import REVIEW_EVENTS_TRACK_FILE
    from clu_latent.tracks import read_track_file

    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    source_track = manifest.tracks[0]
    target_id = read_track_file(valid_package / source_track.file)[0].id

    review_writer_mod.append_review_event(
        valid_package,
        "review_approval",
        target_event_id=target_id,
        reviewer_id="alice",
        reviewed_at="2026-07-08T00:00:00Z",
    )

    report = validate_package(valid_package)
    assert report.valid is True, report.errors
    assert (valid_package / REVIEW_EVENTS_TRACK_FILE).exists()


# --- CLI surfacing (Phase 3.7 requirement: `clulatent validate` surfaces these) ---


def test_cli_validate_surfaces_audio_digest_failure(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(valid_package, _feature_series(t_start_ms="not-an-int"))

    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(valid_package)])
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_cli_validate_passes_with_valid_audio_digest_track(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    runner = CliRunner()
    result = runner.invoke(app, ["validate", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


# --- regression: existing audio-digest / audio-digest-writer / CLI suites ----


def test_audio_digest_module_imports_cleanly():
    from clu_latent import audio_digest as audio_digest_mod

    assert audio_digest_mod is not None


def test_audio_digest_writer_module_imports_cleanly():
    assert writer_mod is not None


# --- docs / README ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_states_core_rule():
    text = " ".join(PHASE_DOC.read_text(encoding="utf-8").split()).lower()
    assert "store deep" in text
    assert "show shallow" in text
    assert "retrieve detail only when needed" in text


def test_readme_includes_phase_3_7_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
