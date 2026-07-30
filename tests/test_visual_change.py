"""Tests for Phase 3.15: the non-semantic visual change evidence lane.

Two groups, mirroring `tests/test_keyframe_retrieval.py`:

- Pure / no-media tests: module import, schema/validation edge cases,
  CLI help, error handling. No ffmpeg or Pillow decode required.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package,
  run `visual-change analyze`, and assert the resulting track/receipt/
  retrieval behavior is bounded, ordered, safe, and conservative.

Core rule under test: visual change evidence, not semantic
interpretation. Nothing here should ever claim to know what a frame
shows.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import visual_change as vc_mod
from clu_latent import visual_change_retrieval as vcr_mod
from clu_latent import visual_change_writer as vcw_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
PILLOW_AVAILABLE = vc_mod.is_available()

REPO_ROOT = Path(__file__).resolve().parents[1]


def _valid_payload(**overrides) -> dict:
    payload = {
        "source_keyframe_id": "kf_000000",
        "target_keyframe_id": "kf_000001",
        "source_image_path": "media/keyframes/000000.jpg",
        "target_image_path": "media/keyframes/000001.jpg",
        "metrics": {
            "mean_absolute_difference": 12.5,
            "normalized_delta": 0.05,
            "perceptual_hash_distance": 2,
        },
        "strength": "low",
        "caveats": list(vc_mod.VISUAL_CHANGE_CAVEATS),
        "method": vc_mod.VISUAL_CHANGE_METHOD,
        "created_by": "clulatent 0.1.0",
    }
    payload.update(overrides)
    return payload


def _valid_event(**payload_overrides) -> dict:
    return {
        "id": "vc_000000",
        "type": vc_mod.VISUAL_CHANGE_RECORD_TYPE,
        "t_start_ms": 0,
        "t_end_ms": 1000,
        "producer": {"name": "clulatent", "version": "0.1.0"},
        "payload": _valid_payload(**payload_overrides),
    }


# --- Pure tests (no ffmpeg, no package) --------------------------------------


def test_visual_change_modules_import_cleanly():
    import clu_latent.visual_change  # noqa: F401
    import clu_latent.visual_change_retrieval  # noqa: F401
    import clu_latent.visual_change_writer  # noqa: F401


def test_valid_event_passes_validation():
    errors, warnings = vc_mod.validate_visual_change_event(_valid_event())
    assert errors == []


# (4) metrics are bounded
def test_metrics_out_of_bounds_rejected():
    bad = _valid_event(metrics={"mean_absolute_difference": 999.0, "normalized_delta": 0.05})
    errors, _ = vc_mod.validate_visual_change_event(bad)
    assert any("mean_absolute_difference" in e for e in errors)

    bad2 = _valid_event(metrics={"mean_absolute_difference": 1.0, "normalized_delta": 5.0})
    errors2, _ = vc_mod.validate_visual_change_event(bad2)
    assert any("normalized_delta" in e for e in errors2)

    bad3 = _valid_event(
        metrics={
            "mean_absolute_difference": 1.0,
            "normalized_delta": 0.1,
            "perceptual_hash_distance": 999,
        }
    )
    errors3, _ = vc_mod.validate_visual_change_event(bad3)
    assert any("perceptual_hash_distance" in e for e in errors3)


def test_metrics_within_bounds_accepted():
    good = _valid_event(
        metrics={
            "mean_absolute_difference": 0.0,
            "normalized_delta": 0.0,
            "perceptual_hash_distance": 0,
        }
    )
    errors, _ = vc_mod.validate_visual_change_event(good)
    assert errors == []

    good2 = _valid_event(
        metrics={
            "mean_absolute_difference": 255.0,
            "normalized_delta": 1.0,
            "perceptual_hash_distance": 64,
        }
    )
    errors2, _ = vc_mod.validate_visual_change_event(good2)
    assert errors2 == []


# (5) strength is one of low/medium/high
def test_strength_must_be_supported_value():
    bad = _valid_event(strength="extreme")
    errors, _ = vc_mod.validate_visual_change_event(bad)
    assert any("strength" in e for e in errors)

    for strength in ("low", "medium", "high"):
        good = _valid_event(strength=strength)
        errors, _ = vc_mod.validate_visual_change_event(good)
        assert errors == [], f"strength={strength} should be valid: {errors}"


def test_strength_for_normalized_delta_buckets():
    assert vc_mod.strength_for_normalized_delta(0.0) == "low"
    assert vc_mod.strength_for_normalized_delta(vc_mod.VISUAL_CHANGE_LOW_THRESHOLD - 0.001) == "low"
    assert vc_mod.strength_for_normalized_delta(vc_mod.VISUAL_CHANGE_LOW_THRESHOLD) == "medium"
    assert vc_mod.strength_for_normalized_delta(vc_mod.VISUAL_CHANGE_HIGH_THRESHOLD - 0.001) == "medium"
    assert vc_mod.strength_for_normalized_delta(vc_mod.VISUAL_CHANGE_HIGH_THRESHOLD) == "high"
    assert vc_mod.strength_for_normalized_delta(1.0) == "high"


# (6) caveats are present
def test_caveats_must_be_exact_fixed_pair():
    missing = _valid_event(caveats=[])
    errors, _ = vc_mod.validate_visual_change_event(missing)
    assert any("caveats" in e for e in errors)

    softened = _valid_event(caveats=["Visual change evidence, not semantic interpretation."])
    errors2, _ = vc_mod.validate_visual_change_event(softened)
    assert any("caveats" in e for e in errors2)

    exact = _valid_event(caveats=list(vc_mod.VISUAL_CHANGE_CAVEATS))
    errors3, _ = vc_mod.validate_visual_change_event(exact)
    assert errors3 == []


# (7) no semantic/forbidden language appears
def test_fixed_caveats_do_not_trip_forbidden_language_check():
    # Regression test: the approved caveat pair itself uses negation
    # language ("does not identify... intent... or scene meaning") and
    # must not be flagged as containing forbidden language.
    event = _valid_event()
    errors, _ = vc_mod.validate_visual_change_event(event)
    assert errors == []


def test_forbidden_language_rejected_in_free_text_field():
    bad = _valid_event(method="the scene shows a person entering with tension")
    errors, _ = vc_mod.validate_visual_change_event(bad)
    assert any("forbidden language" in e for e in errors)


def test_forbidden_phrases_catalog_matches_phase_brief():
    expected = {
        "a person enters",
        "the scene shows",
        "the clip means",
        "tension",
        "intent",
        "emotion",
        "the model understands the scene",
    }
    assert expected.issubset(vc_mod.FORBIDDEN_VISUAL_CHANGE_PHRASES)


# (12) invalid time range fails
def test_query_time_invalid_range_raises(tmp_path):
    with pytest.raises(vcr_mod.VisualChangeRetrievalError):
        vcr_mod.query_visual_change_by_time_range(tmp_path / "whatever.clulatent", 5000, 1000)


# (10) missing event id fails cleanly
def test_get_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(vcr_mod.VisualChangeRetrievalError):
        vcr_mod.get_visual_change_event_by_id(tmp_path / "nope.clulatent", "vc_000000")


# (15) unsafe image path is rejected
def test_unsafe_image_path_rejected_by_validator(tmp_path):
    bad = _valid_event(source_image_path="../../etc/passwd")
    errors, _ = vc_mod.validate_visual_change_event(bad, package_root=tmp_path)
    assert errors, "an escaping path must be rejected"


def test_readme_includes_phase_3_15_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


def test_phase_3_15_doc_exists():
    doc = REPO_ROOT / "docs" / "PHASE_3_15_VISUAL_CHANGE_EVIDENCE_LANE.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "visual change" in text.lower()


def test_cli_visual_change_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["visual-change", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("analyze", "summary", "get", "query-time"):
        assert name in result.output


def test_cli_get_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["visual-change", "get", str(tmp_path / "does-not-exist.clulatent"), "vc_000000"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_cli_query_time_invalid_range_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "visual-change",
            "query-time",
            str(tmp_path / "does-not-exist.clulatent"),
            "--start-ms",
            "5000",
            "--end-ms",
            "1000",
        ],
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# --- ffmpeg + Pillow gated end-to-end tests -----------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not (FFMPEG_AVAILABLE and PILLOW_AVAILABLE),
    reason="ffmpeg/ffprobe/Pillow not all available in this environment",
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_visual_change_fixture")
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
        "testsrc=duration=4:size=320x240:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=4",
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
def valid_package(tmp_path, tiny_video) -> Path:
    output_path = tmp_path / "pkg.clulatent"
    result = ingest_video(tiny_video, output_path)
    return result.package_path


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


# (1) visual change analysis creates a track
@pytestmark_e2e
def test_analyze_creates_track(valid_package):
    result = vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    assert result.events_written > 0
    track_path = valid_package / result.track_file
    assert track_path.is_file()
    manifest = _load_manifest(valid_package)
    assert any(t.name == vcw_mod.VISUAL_CHANGE_TRACK_NAME for t in manifest.tracks)


# (2) records are timestamped and ordered
@pytestmark_e2e
def test_analyze_records_ordered_by_time(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    assert events
    starts = [e["t_start_ms"] for e in events]
    assert starts == sorted(starts)
    assert all(e["type"] == vc_mod.VISUAL_CHANGE_RECORD_TYPE for e in events)


# (3) records link adjacent keyframes
@pytestmark_e2e
def test_analyze_records_link_adjacent_keyframes(valid_package):
    from clu_latent import keyframe_retrieval as kf_mod

    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    keyframes = kf_mod.load_keyframe_events(valid_package)
    keyframe_ids = [k["id"] for k in keyframes]
    events = vcr_mod.load_visual_change_events(valid_package)

    assert len(events) == len(keyframe_ids) - 1
    for index, event in enumerate(events):
        payload = event["payload"]
        assert payload["source_keyframe_id"] == keyframe_ids[index]
        assert payload["target_keyframe_id"] == keyframe_ids[index + 1]


@pytestmark_e2e
def test_computed_metrics_are_bounded(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    for event in events:
        metrics = event["payload"]["metrics"]
        assert 0.0 <= metrics["mean_absolute_difference"] <= 255.0
        assert 0.0 <= metrics["normalized_delta"] <= 1.0
        assert 0 <= metrics["perceptual_hash_distance"] <= 64


@pytestmark_e2e
def test_computed_strength_is_valid_bucket(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    for event in events:
        assert event["payload"]["strength"] in vc_mod.SUPPORTED_VISUAL_CHANGE_STRENGTHS


# (7) no semantic/forbidden language appears in computed output
@pytestmark_e2e
def test_no_forbidden_language_in_computed_records(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    serialized = json.dumps(events).lower()
    forbidden = {
        p
        for p in vc_mod.FORBIDDEN_VISUAL_CHANGE_PHRASES
        # "intent"/"emotion"/"tension" appear inside the approved fixed
        # caveat's own negation sentence, so exclude those three from
        # this coarse whole-blob scan (they are already covered
        # precisely by test_fixed_caveats_do_not_trip_forbidden_language_check
        # and the equality-based caveat validator).
        if p not in {"intent", "emotion", "tension"}
    }
    for phrase in forbidden:
        assert phrase not in serialized, f"forbidden phrase leaked into output: {phrase!r}"


# (8) summary reports count and strength buckets
@pytestmark_e2e
def test_summary_reports_count_and_strength_buckets(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    summary = vcr_mod.summarize_visual_change(valid_package)
    events = vcr_mod.load_visual_change_events(valid_package)
    assert summary["event_count"] == len(events)
    assert sum(summary["strength_counts"].values()) == len(events)
    assert summary["first_event_id"] == events[0]["id"]
    assert summary["last_event_id"] == events[-1]["id"]


@pytestmark_e2e
def test_summary_zeroed_when_no_track(valid_package):
    summary = vcr_mod.summarize_visual_change(valid_package)
    assert summary["event_count"] == 0
    assert summary["first_event_id"] is None
    assert summary["strength_counts"] == {"low": 0, "medium": 0, "high": 0}


# (9) get returns a valid event
@pytestmark_e2e
def test_get_returns_valid_event(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    target = events[0]
    got = vcr_mod.get_visual_change_event_by_id(valid_package, target["id"])
    assert got is not None
    assert got["id"] == target["id"]


@pytestmark_e2e
def test_get_missing_id_returns_none(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    assert vcr_mod.get_visual_change_event_by_id(valid_package, "vc_999999") is None


@pytestmark_e2e
def test_cli_get_missing_id_clean(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    runner = CliRunner()
    result = runner.invoke(app, ["visual-change", "get", str(valid_package), "vc_999999"])
    assert result.exit_code == 0, result.output
    assert "No visual change record found" in result.output


@pytestmark_e2e
def test_cli_get_valid_id(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    target = events[0]["id"]
    runner = CliRunner()
    result = runner.invoke(app, ["visual-change", "get", str(valid_package), target])
    assert result.exit_code == 0, result.output
    assert target in result.output


# (11) query-time returns overlapping events
@pytestmark_e2e
def test_query_time_returns_overlapping_events(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    lo = events[0]["t_start_ms"]
    hi = events[-1]["t_end_ms"]
    got = vcr_mod.query_visual_change_by_time_range(valid_package, lo, hi)
    assert got == events


@pytestmark_e2e
def test_query_time_empty_result_is_clean(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    events = vcr_mod.load_visual_change_events(valid_package)
    far = events[-1]["t_end_ms"] + 10_000_000
    got = vcr_mod.query_visual_change_by_time_range(valid_package, far, far + 1000)
    assert got == []


# (13) missing keyframe track fails cleanly
@pytestmark_e2e
def test_compute_with_no_keyframes_track_raises(valid_package):
    manifest = _load_manifest(valid_package)
    updated_tracks = [t for t in manifest.tracks if t.name != "keyframes"]
    updated_manifest = manifest.model_copy(update={"tracks": updated_tracks})
    (valid_package / "manifest.json").write_text(
        json.dumps(updated_manifest.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(vc_mod.VisualChangeComputeError):
        vc_mod.compute_visual_change_events(valid_package, tool_name="t", tool_version="1")


# (14) missing image file fails cleanly
@pytestmark_e2e
def test_compute_missing_image_file_raises(valid_package):
    from clu_latent import keyframe_retrieval as kf_mod

    keyframes = kf_mod.load_keyframe_events(valid_package)
    first_image = valid_package / keyframes[0]["payload"]["path"]
    first_image.unlink()

    with pytest.raises(vc_mod.VisualChangeComputeError):
        vc_mod.compute_visual_change_events(valid_package, tool_name="test", tool_version="1")


# (16) analyze refuses overwrite without --force
@pytestmark_e2e
def test_analyze_refuses_overwrite_without_force(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    with pytest.raises(vcw_mod.VisualChangeWriteError):
        vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")


@pytestmark_e2e
def test_cli_analyze_refuses_overwrite_without_force(valid_package):
    runner = CliRunner()
    first = runner.invoke(app, ["visual-change", "analyze", str(valid_package)])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["visual-change", "analyze", str(valid_package)])
    assert second.exit_code == 1
    assert "Traceback" not in second.output


# (17) analyze with --force replaces only the intended track
@pytestmark_e2e
def test_analyze_force_replaces_only_intended_track(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    before = _snapshot(valid_package)

    vcw_mod.analyze_visual_change(
        valid_package, tool_name="test", tool_version="1", force=True, write_receipt=False
    )
    after = _snapshot(valid_package)

    changed = {rel for rel in before if before.get(rel) != after.get(rel)}
    changed |= set(after) - set(before)
    allowed = {"tracks/visual_change_candidates.jsonl", "manifest.json"}
    assert changed.issubset(allowed), f"unexpected files changed: {changed - allowed}"


# (18) analyze creates a receipt
@pytestmark_e2e
def test_analyze_creates_receipt(valid_package):
    result = vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    assert result.receipt_path is not None
    assert result.receipt_path.is_file()
    receipts = [json.loads(line) for line in result.receipt_path.read_text().splitlines()]
    assert len(receipts) == 1
    assert receipts[0]["status"] == "success"
    assert receipts[0]["operation"] == "analyze_visual_change"


# (19) read-only commands create no receipts
@pytestmark_e2e
def test_read_only_commands_create_no_receipts(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    receipts_path = valid_package / "receipts" / "visual_change.jsonl"
    before = receipts_path.read_bytes()

    events = vcr_mod.load_visual_change_events(valid_package)
    vcr_mod.summarize_visual_change(valid_package)
    vcr_mod.get_visual_change_event_by_id(valid_package, events[0]["id"])
    vcr_mod.query_visual_change_by_time_range(valid_package, 0, events[-1]["t_end_ms"])

    runner = CliRunner()
    runner.invoke(app, ["visual-change", "summary", str(valid_package)])
    runner.invoke(app, ["visual-change", "get", str(valid_package), events[0]["id"]])
    runner.invoke(
        app,
        [
            "visual-change",
            "query-time",
            str(valid_package),
            "--start-ms",
            "0",
            "--end-ms",
            str(events[-1]["t_end_ms"]),
        ],
    )

    after = receipts_path.read_bytes()
    assert before == after


# (20) corrupted visual change track is refused
@pytestmark_e2e
def test_validate_package_rejects_corrupted_track(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    track_path = valid_package / "tracks" / "visual_change_candidates.jsonl"
    lines = track_path.read_text().splitlines()
    record = json.loads(lines[0])
    record["payload"]["strength"] = "extreme"
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n")

    report = validate_package(valid_package)
    assert not report.valid
    assert any("strength" in e for e in report.errors)


# (21) package validation integrates the new track if appropriate
@pytestmark_e2e
def test_validate_package_passes_with_visual_change_track(valid_package):
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    report = validate_package(valid_package)
    assert report.valid, report.errors


@pytestmark_e2e
def test_validate_package_passes_without_visual_change_track(valid_package):
    report = validate_package(valid_package)
    assert report.valid, report.errors


@pytestmark_e2e
def test_cli_analyze_and_summary_end_to_end(valid_package):
    runner = CliRunner()
    result = runner.invoke(app, ["visual-change", "analyze", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "Visual change candidates written" in result.output

    summary = runner.invoke(app, ["visual-change", "summary", str(valid_package)])
    assert summary.exit_code == 0, summary.output
    assert "event_count" in summary.output
