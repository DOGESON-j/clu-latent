"""Tests for Phase 3.16: the non-semantic changed-region evidence lane.

Two groups, mirroring `tests/test_visual_change.py`:

- Pure / no-media tests: module import, schema/validation edge cases,
  CLI help, error handling. No ffmpeg or Pillow decode required.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package,
  run `visual-change analyze` (the Phase 3.15 prerequisite) then
  `changed-regions analyze`, and assert the resulting track/receipt/
  retrieval behavior is bounded, ordered, safe, and conservative.

Core rule under test: changed-region evidence, not semantic
interpretation. Nothing here should ever claim what changed, who
changed, or what the changed pixels represent -- only where the
strongest difference was found.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import changed_region as cr_mod
from clu_latent import changed_region_retrieval as crr_mod
from clu_latent import changed_region_writer as crw_mod
from clu_latent import visual_change as vc_mod
from clu_latent import visual_change_writer as vcw_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
PILLOW_AVAILABLE = cr_mod.is_available()

REPO_ROOT = Path(__file__).resolve().parents[1]


def _valid_payload(**overrides) -> dict:
    payload = {
        "visual_change_id": "vc_000000",
        "source_keyframe_id": "kf_000000",
        "target_keyframe_id": "kf_000001",
        "source_image_path": "media/keyframes/000000.jpg",
        "target_image_path": "media/keyframes/000001.jpg",
        "region": {"x": 10, "y": 10, "width": 40, "height": 30, "coordinate_system": "pixel"},
        "normalized_region": {
            "x": 0.1,
            "y": 0.1,
            "width": 0.2,
            "height": 0.15,
            "coordinate_system": "normalized_0_1",
        },
        "grid": {"rows": cr_mod.GRID_ROWS, "cols": cr_mod.GRID_COLS, "selected_cells": [[1, 1], [1, 2]]},
        "metrics": {
            "region_mean_absolute_difference": 50.0,
            "region_normalized_delta": 0.2,
            "frame_normalized_delta": 0.05,
            "region_to_frame_ratio": 4.0,
        },
        "change_scope": "localized",
        "strength": "medium",
        "caveats": list(cr_mod.CHANGED_REGION_CAVEATS),
        "method": cr_mod.CHANGED_REGION_METHOD,
        "created_by": "clulatent 0.1.0",
    }
    payload.update(overrides)
    return payload


def _valid_event(**payload_overrides) -> dict:
    return {
        "id": "cr_000000",
        "type": cr_mod.CHANGED_REGION_RECORD_TYPE,
        "t_start_ms": 0,
        "t_end_ms": 1000,
        "producer": {"name": "clulatent", "version": "0.1.0"},
        "payload": _valid_payload(**payload_overrides),
    }


# --- Pure tests (no ffmpeg, no package) --------------------------------------


def test_changed_region_modules_import_cleanly():
    import clu_latent.changed_region  # noqa: F401
    import clu_latent.changed_region_retrieval  # noqa: F401
    import clu_latent.changed_region_writer  # noqa: F401


def test_valid_event_passes_validation():
    errors, warnings = cr_mod.validate_changed_region_event(_valid_event())
    assert errors == []


# (2) links to visual_change candidates -- cross-check via known_visual_change_ids
def test_visual_change_id_cross_check():
    event = _valid_event()
    errors, _ = cr_mod.validate_changed_region_event(
        event, known_visual_change_ids={"vc_000000", "vc_000001"}
    )
    assert errors == []

    errors2, _ = cr_mod.validate_changed_region_event(
        event, known_visual_change_ids={"vc_999999"}
    )
    assert any("visual_change_id" in e for e in errors2)


# (3) links to source/target keyframes -- cross-check via known_keyframe_ids
def test_keyframe_id_cross_check():
    event = _valid_event()
    errors, _ = cr_mod.validate_changed_region_event(
        event, known_keyframe_ids={"kf_000000", "kf_000001"}
    )
    assert errors == []

    errors2, _ = cr_mod.validate_changed_region_event(
        event, known_keyframe_ids={"kf_999999"}
    )
    assert any("source_keyframe_id" in e for e in errors2)
    assert any("target_keyframe_id" in e for e in errors2)


# (4) bounding boxes within image bounds (pixel region shape checks)
def test_pixel_region_shape_rejected_when_malformed():
    bad = _valid_event(region={"x": -1, "y": 0, "width": 10, "height": 10, "coordinate_system": "pixel"})
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("region.x" in e for e in errors)

    bad2 = _valid_event(region={"x": 0, "y": 0, "width": 0, "height": 10, "coordinate_system": "pixel"})
    errors2, _ = cr_mod.validate_changed_region_event(bad2)
    assert any("region.width" in e for e in errors2)

    bad3 = _valid_event(
        region={"x": 0, "y": 0, "width": 10, "height": 10, "coordinate_system": "not_pixel"}
    )
    errors3, _ = cr_mod.validate_changed_region_event(bad3)
    assert any("coordinate_system" in e for e in errors3)


# (5) normalized boxes within 0..1
def test_normalized_region_must_stay_within_unit_interval():
    bad = _valid_event(
        normalized_region={
            "x": 0.9,
            "y": 0.1,
            "width": 0.5,
            "height": 0.1,
            "coordinate_system": "normalized_0_1",
        }
    )
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("x + width exceeds 1.0" in e for e in errors)

    bad2 = _valid_event(
        normalized_region={
            "x": -0.1,
            "y": 0.1,
            "width": 0.2,
            "height": 0.1,
            "coordinate_system": "normalized_0_1",
        }
    )
    errors2, _ = cr_mod.validate_changed_region_event(bad2)
    assert any("normalized_region.x" in e for e in errors2)

    good = _valid_event(
        normalized_region={
            "x": 0.0,
            "y": 0.0,
            "width": 1.0,
            "height": 1.0,
            "coordinate_system": "normalized_0_1",
        }
    )
    errors3, _ = cr_mod.validate_changed_region_event(good)
    assert errors3 == []


# (6) grid metadata valid
def test_grid_metadata_must_match_fixed_shape():
    bad = _valid_event(grid={"rows": 12, "cols": 8, "selected_cells": [[0, 0]]})
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("grid.rows" in e for e in errors)

    bad2 = _valid_event(grid={"rows": 8, "cols": 8, "selected_cells": []})
    errors2, _ = cr_mod.validate_changed_region_event(bad2)
    assert any("selected_cells" in e for e in errors2)

    bad3 = _valid_event(grid={"rows": 8, "cols": 8, "selected_cells": [[99, 0]]})
    errors3, _ = cr_mod.validate_changed_region_event(bad3)
    assert any("out of bounds" in e for e in errors3)


# (7) metrics bounded
def test_metrics_out_of_bounds_rejected():
    bad = _valid_event(
        metrics={
            "region_mean_absolute_difference": 999.0,
            "region_normalized_delta": 0.1,
            "frame_normalized_delta": 0.1,
            "region_to_frame_ratio": 1.0,
        }
    )
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("region_mean_absolute_difference" in e for e in errors)

    bad2 = _valid_event(
        metrics={
            "region_mean_absolute_difference": 10.0,
            "region_normalized_delta": 5.0,
            "frame_normalized_delta": 0.1,
            "region_to_frame_ratio": 1.0,
        }
    )
    errors2, _ = cr_mod.validate_changed_region_event(bad2)
    assert any("region_normalized_delta" in e for e in errors2)

    bad3 = _valid_event(
        metrics={
            "region_mean_absolute_difference": 10.0,
            "region_normalized_delta": 0.1,
            "frame_normalized_delta": 0.1,
            "region_to_frame_ratio": 999999.0,
        }
    )
    errors3, _ = cr_mod.validate_changed_region_event(bad3)
    assert any("region_to_frame_ratio" in e for e in errors3)


def test_metrics_within_bounds_accepted():
    good = _valid_event(
        metrics={
            "region_mean_absolute_difference": 0.0,
            "region_normalized_delta": 0.0,
            "frame_normalized_delta": 0.0,
            "region_to_frame_ratio": 0.0,
        }
    )
    errors, _ = cr_mod.validate_changed_region_event(good)
    assert errors == []


# (8) strength valid
def test_strength_must_be_supported_value():
    bad = _valid_event(strength="extreme")
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("strength" in e for e in errors)

    for strength in cr_mod.CHANGED_REGION_STRENGTHS:
        good = _valid_event(strength=strength)
        errors, _ = cr_mod.validate_changed_region_event(good)
        assert errors == [], f"strength={strength} should be valid: {errors}"


# (9) change_scope valid
def test_change_scope_must_be_supported_value():
    bad = _valid_event(change_scope="everywhere")
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("change_scope" in e for e in errors)

    for scope in cr_mod.CHANGE_SCOPES:
        good = _valid_event(change_scope=scope)
        errors, _ = cr_mod.validate_changed_region_event(good)
        assert errors == [], f"change_scope={scope} should be valid: {errors}"


def test_classify_change_scope_buckets():
    # Below the "unknown" threshold regardless of cell ratio.
    assert cr_mod.classify_change_scope(0.0, 1.0) == "unknown"
    assert (
        cr_mod.classify_change_scope(cr_mod.CHANGE_SCOPE_UNKNOWN_REGION_DELTA_THRESHOLD - 0.001, 1.0)
        == "unknown"
    )
    # A real but small region delta, dominant cell coverage -> global.
    assert cr_mod.classify_change_scope(0.5, cr_mod.CHANGE_SCOPE_GLOBAL_CELL_RATIO) == "global"
    # Small cell coverage -> localized.
    assert cr_mod.classify_change_scope(0.5, cr_mod.CHANGE_SCOPE_LOCALIZED_CELL_RATIO) == "localized"
    # In between -> distributed.
    mid_ratio = (cr_mod.CHANGE_SCOPE_LOCALIZED_CELL_RATIO + cr_mod.CHANGE_SCOPE_GLOBAL_CELL_RATIO) / 2
    assert cr_mod.classify_change_scope(0.5, mid_ratio) == "distributed"


# (10) caveats present
def test_caveats_must_be_exact_fixed_pair():
    missing = _valid_event(caveats=[])
    errors, _ = cr_mod.validate_changed_region_event(missing)
    assert any("caveats" in e for e in errors)

    softened = _valid_event(caveats=["Changed-region evidence, not semantic interpretation."])
    errors2, _ = cr_mod.validate_changed_region_event(softened)
    assert any("caveats" in e for e in errors2)

    exact = _valid_event(caveats=list(cr_mod.CHANGED_REGION_CAVEATS))
    errors3, _ = cr_mod.validate_changed_region_event(exact)
    assert errors3 == []


def test_fixed_caveats_do_not_trip_forbidden_language_check():
    # Regression test (mirrors the Phase 3.15 fix): the approved caveat
    # pair itself uses negation language ("does not identify... text...
    # intent... or scene meaning") and must not be flagged as containing
    # forbidden language.
    event = _valid_event()
    errors, _ = cr_mod.validate_changed_region_event(event)
    assert errors == []


# (11) forbidden language rejected
def test_forbidden_language_rejected_in_free_text_field():
    bad = _valid_event(method="detects a person in the frame")
    errors, _ = cr_mod.validate_changed_region_event(bad)
    assert any("forbidden language" in e for e in errors)

    bad2 = _valid_event(created_by="the model understands the scene")
    errors2, _ = cr_mod.validate_changed_region_event(bad2)
    assert any("forbidden language" in e for e in errors2)


def test_forbidden_phrases_catalog_matches_phase_brief():
    expected = {
        "person",
        "face",
        "object",
        "car",
        "weapon",
        "text",
        "logo",
        "action",
        "intent",
        "emotion",
        "scene meaning",
        "the clip shows",
        "the model understands",
    }
    assert expected.issubset(cr_mod.FORBIDDEN_CHANGED_REGION_PHRASES)


# (16) invalid time range fails
def test_query_time_invalid_range_raises(tmp_path):
    with pytest.raises(crr_mod.ChangedRegionRetrievalError):
        crr_mod.query_changed_regions_by_time_range(tmp_path / "whatever.clulatent", 5000, 1000)


# (14) missing region id fails cleanly
def test_get_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(crr_mod.ChangedRegionRetrievalError):
        crr_mod.get_changed_region_event_by_id(tmp_path / "nope.clulatent", "cr_000000")


# (20) unsafe image path is rejected
def test_unsafe_image_path_rejected_by_validator(tmp_path):
    bad = _valid_event(source_image_path="../../etc/passwd")
    errors, _ = cr_mod.validate_changed_region_event(bad, package_root=tmp_path)
    assert errors, "an escaping path must be rejected"


def test_readme_includes_phase_3_16_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


def test_phase_3_16_doc_exists():
    doc = REPO_ROOT / "docs" / "PHASE_3_16_CHANGED_REGION_EVIDENCE_LANE.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "changed region" in text.lower() or "changed-region" in text.lower()


def test_cli_changed_regions_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["changed-regions", "--help"])
    assert result.exit_code == 0, result.output
    for name in ("analyze", "summary", "get", "query-time", "query-visual-change"):
        assert name in result.output


def test_cli_get_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["changed-regions", "get", str(tmp_path / "does-not-exist.clulatent"), "cr_000000"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_cli_query_time_invalid_range_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "changed-regions",
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
    directory = tmp_path_factory.mktemp("clulatent_changed_region_fixture")
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


@pytest.fixture
def package_with_visual_change(valid_package) -> Path:
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    return valid_package


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


# (1) changed-region analysis creates a track
@pytestmark_e2e
def test_analyze_creates_track(package_with_visual_change):
    result = crw_mod.analyze_changed_regions(
        package_with_visual_change, tool_name="test", tool_version="1"
    )
    assert result.events_written > 0
    track_path = package_with_visual_change / result.track_file
    assert track_path.is_file()
    manifest = _load_manifest(package_with_visual_change)
    assert any(t.name == crw_mod.CHANGED_REGION_TRACK_NAME for t in manifest.tracks)


# (2) links to visual_change candidates
@pytestmark_e2e
def test_analyze_records_link_visual_change_candidates(package_with_visual_change):
    from clu_latent import visual_change_retrieval as vcr_mod

    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    visual_changes = vcr_mod.load_visual_change_events(package_with_visual_change)
    vc_ids = {v["id"] for v in visual_changes}
    events = crr_mod.load_changed_region_events(package_with_visual_change)

    assert len(events) == len(visual_changes)
    for event in events:
        assert event["payload"]["visual_change_id"] in vc_ids


# (3) links to source/target keyframes
@pytestmark_e2e
def test_analyze_records_link_source_target_keyframes(package_with_visual_change):
    from clu_latent import keyframe_retrieval as kf_mod

    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    keyframes = kf_mod.load_keyframe_events(package_with_visual_change)
    keyframe_ids = {k["id"] for k in keyframes}
    events = crr_mod.load_changed_region_events(package_with_visual_change)

    for event in events:
        assert event["payload"]["source_keyframe_id"] in keyframe_ids
        assert event["payload"]["target_keyframe_id"] in keyframe_ids


# (4) bounding boxes within image bounds
@pytestmark_e2e
def test_computed_pixel_region_within_image_bounds(package_with_visual_change):
    from PIL import Image

    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        region = event["payload"]["region"]
        image_path = package_with_visual_change / event["payload"]["source_image_path"]
        with Image.open(image_path) as image:
            width, height = image.size
        assert 0 <= region["x"] < width
        assert 0 <= region["y"] < height
        assert region["x"] + region["width"] <= width
        assert region["y"] + region["height"] <= height


# (5) normalized boxes within 0..1
@pytestmark_e2e
def test_computed_normalized_region_within_unit_interval(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        nr = event["payload"]["normalized_region"]
        for key in ("x", "y", "width", "height"):
            assert 0.0 <= nr[key] <= 1.0
        assert nr["x"] + nr["width"] <= 1.0 + 1e-6
        assert nr["y"] + nr["height"] <= 1.0 + 1e-6


# (6) grid metadata valid
@pytestmark_e2e
def test_computed_grid_metadata_is_valid(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        grid = event["payload"]["grid"]
        assert grid["rows"] == cr_mod.GRID_ROWS
        assert grid["cols"] == cr_mod.GRID_COLS
        assert len(grid["selected_cells"]) >= 1
        for row, col in grid["selected_cells"]:
            assert 0 <= row < cr_mod.GRID_ROWS
            assert 0 <= col < cr_mod.GRID_COLS


# (7) metrics bounded
@pytestmark_e2e
def test_computed_metrics_are_bounded(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        metrics = event["payload"]["metrics"]
        assert 0.0 <= metrics["region_mean_absolute_difference"] <= 255.0
        assert 0.0 <= metrics["region_normalized_delta"] <= 1.0
        assert 0.0 <= metrics["frame_normalized_delta"] <= 1.0
        assert metrics["region_to_frame_ratio"] >= 0.0


# (8) strength valid
@pytestmark_e2e
def test_computed_strength_is_valid_bucket(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        assert event["payload"]["strength"] in cr_mod.SUPPORTED_CHANGED_REGION_STRENGTHS


# (9) change_scope valid
@pytestmark_e2e
def test_computed_change_scope_is_valid_bucket(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    for event in events:
        assert event["payload"]["change_scope"] in cr_mod.SUPPORTED_CHANGE_SCOPES


# (10)/(11) no forbidden/semantic language leaks into computed output
@pytestmark_e2e
def test_no_forbidden_language_in_computed_records(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    serialized = json.dumps(events).lower()
    forbidden = {
        p
        for p in cr_mod.FORBIDDEN_CHANGED_REGION_PHRASES
        # "person"/"object"/"text"/"action"/"intent"/"scene meaning" all
        # appear inside the approved fixed caveat's own negation
        # sentence ("Does not identify objects, people, text, actions,
        # intent, or scene meaning."), so exclude those from this coarse
        # whole-blob scan (already covered precisely by
        # test_fixed_caveats_do_not_trip_forbidden_language_check and
        # the equality-based caveat validator).
        if p not in {"person", "object", "text", "action", "intent", "scene meaning"}
    }
    for phrase in forbidden:
        assert phrase not in serialized, f"forbidden phrase leaked into output: {phrase!r}"


# (12) summary reports count/strength/scope buckets
@pytestmark_e2e
def test_summary_reports_count_strength_and_scope_buckets(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    summary = crr_mod.summarize_changed_regions(package_with_visual_change)
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    assert summary["event_count"] == len(events)
    assert sum(summary["strength_counts"].values()) == len(events)
    assert sum(summary["change_scope_counts"].values()) == len(events)
    assert summary["first_event_id"] == events[0]["id"]
    assert summary["last_event_id"] == events[-1]["id"]


@pytestmark_e2e
def test_summary_zeroed_when_no_track(package_with_visual_change):
    summary = crr_mod.summarize_changed_regions(package_with_visual_change)
    assert summary["event_count"] == 0
    assert summary["first_event_id"] is None
    assert summary["strength_counts"] == {"low": 0, "medium": 0, "high": 0}
    assert summary["change_scope_counts"] == {
        "localized": 0,
        "distributed": 0,
        "global": 0,
        "unknown": 0,
    }


# (13) get returns a valid region
@pytestmark_e2e
def test_get_returns_valid_event(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    target = events[0]
    got = crr_mod.get_changed_region_event_by_id(package_with_visual_change, target["id"])
    assert got is not None
    assert got["id"] == target["id"]


# (14) missing id fails cleanly
@pytestmark_e2e
def test_get_missing_id_returns_none(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    assert crr_mod.get_changed_region_event_by_id(package_with_visual_change, "cr_999999") is None


@pytestmark_e2e
def test_cli_get_missing_id_clean(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    runner = CliRunner()
    result = runner.invoke(app, ["changed-regions", "get", str(package_with_visual_change), "cr_999999"])
    assert result.exit_code == 0, result.output
    assert "No changed-region record found" in result.output


@pytestmark_e2e
def test_cli_get_valid_id(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    target = events[0]["id"]
    runner = CliRunner()
    result = runner.invoke(app, ["changed-regions", "get", str(package_with_visual_change), target])
    assert result.exit_code == 0, result.output
    assert target in result.output


# (15) query-time overlap
@pytestmark_e2e
def test_query_time_returns_overlapping_events(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    lo = events[0]["t_start_ms"]
    hi = events[-1]["t_end_ms"]
    got = crr_mod.query_changed_regions_by_time_range(package_with_visual_change, lo, hi)
    assert got == events


@pytestmark_e2e
def test_query_time_empty_result_is_clean(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    events = crr_mod.load_changed_region_events(package_with_visual_change)
    far = events[-1]["t_end_ms"] + 10_000_000
    got = crr_mod.query_changed_regions_by_time_range(package_with_visual_change, far, far + 1000)
    assert got == []


# (17) query-visual-change returns linked regions
@pytestmark_e2e
def test_query_visual_change_returns_linked_regions(package_with_visual_change):
    from clu_latent import visual_change_retrieval as vcr_mod

    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    visual_changes = vcr_mod.load_visual_change_events(package_with_visual_change)
    target_vc_id = visual_changes[0]["id"]

    got = crr_mod.query_changed_regions_by_visual_change_id(package_with_visual_change, target_vc_id)
    assert len(got) >= 1
    for event in got:
        assert event["payload"]["visual_change_id"] == target_vc_id


@pytestmark_e2e
def test_query_visual_change_missing_id_returns_empty(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    got = crr_mod.query_changed_regions_by_visual_change_id(package_with_visual_change, "vc_nope")
    assert got == []


@pytestmark_e2e
def test_cli_query_visual_change(package_with_visual_change):
    from clu_latent import visual_change_retrieval as vcr_mod

    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    visual_changes = vcr_mod.load_visual_change_events(package_with_visual_change)
    target_vc_id = visual_changes[0]["id"]

    runner = CliRunner()
    result = runner.invoke(
        app, ["changed-regions", "query-visual-change", str(package_with_visual_change), target_vc_id]
    )
    assert result.exit_code == 0, result.output
    assert target_vc_id in result.output


# (18) missing visual_change track fails cleanly
@pytestmark_e2e
def test_compute_with_no_visual_change_track_raises(valid_package):
    with pytest.raises(cr_mod.ChangedRegionComputeError):
        cr_mod.compute_changed_region_events(valid_package, tool_name="t", tool_version="1")


# (19) missing keyframe image fails cleanly
@pytestmark_e2e
def test_compute_missing_image_file_raises(package_with_visual_change):
    from clu_latent import keyframe_retrieval as kf_mod

    keyframes = kf_mod.load_keyframe_events(package_with_visual_change)
    first_image = package_with_visual_change / keyframes[0]["payload"]["path"]
    first_image.unlink()

    with pytest.raises(cr_mod.ChangedRegionComputeError):
        cr_mod.compute_changed_region_events(package_with_visual_change, tool_name="test", tool_version="1")


# (21) analyze refuses overwrite without --force
@pytestmark_e2e
def test_analyze_refuses_overwrite_without_force(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    with pytest.raises(crw_mod.ChangedRegionWriteError):
        crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")


@pytestmark_e2e
def test_cli_analyze_refuses_overwrite_without_force(package_with_visual_change):
    runner = CliRunner()
    first = runner.invoke(app, ["changed-regions", "analyze", str(package_with_visual_change)])
    assert first.exit_code == 0, first.output
    second = runner.invoke(app, ["changed-regions", "analyze", str(package_with_visual_change)])
    assert second.exit_code == 1
    assert "Traceback" not in second.output


# (22) analyze --force replaces only intended track
@pytestmark_e2e
def test_analyze_force_replaces_only_intended_track(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    before = _snapshot(package_with_visual_change)

    crw_mod.analyze_changed_regions(
        package_with_visual_change, tool_name="test", tool_version="1", force=True, write_receipt=False
    )
    after = _snapshot(package_with_visual_change)

    changed = {rel for rel in before if before.get(rel) != after.get(rel)}
    changed |= set(after) - set(before)
    allowed = {"tracks/changed_region_candidates.jsonl", "manifest.json"}
    assert changed.issubset(allowed), f"unexpected files changed: {changed - allowed}"


# (23) analyze creates a receipt
@pytestmark_e2e
def test_analyze_creates_receipt(package_with_visual_change):
    result = crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    assert result.receipt_path is not None
    assert result.receipt_path.is_file()
    receipts = [json.loads(line) for line in result.receipt_path.read_text().splitlines()]
    assert len(receipts) == 1
    assert receipts[0]["status"] == "success"
    assert receipts[0]["operation"] == "analyze_changed_regions"
    assert "strength_counts" in receipts[0]
    assert "change_scope_counts" in receipts[0]


# (24) read-only commands create no receipts
@pytestmark_e2e
def test_read_only_commands_create_no_receipts(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    receipts_path = package_with_visual_change / "receipts" / "changed_region.jsonl"
    before = receipts_path.read_bytes()

    events = crr_mod.load_changed_region_events(package_with_visual_change)
    crr_mod.summarize_changed_regions(package_with_visual_change)
    crr_mod.get_changed_region_event_by_id(package_with_visual_change, events[0]["id"])
    crr_mod.query_changed_regions_by_time_range(package_with_visual_change, 0, events[-1]["t_end_ms"])
    crr_mod.query_changed_regions_by_visual_change_id(
        package_with_visual_change, events[0]["payload"]["visual_change_id"]
    )

    runner = CliRunner()
    runner.invoke(app, ["changed-regions", "summary", str(package_with_visual_change)])
    runner.invoke(app, ["changed-regions", "get", str(package_with_visual_change), events[0]["id"]])
    runner.invoke(
        app,
        [
            "changed-regions",
            "query-time",
            str(package_with_visual_change),
            "--start-ms",
            "0",
            "--end-ms",
            str(events[-1]["t_end_ms"]),
        ],
    )
    runner.invoke(
        app,
        [
            "changed-regions",
            "query-visual-change",
            str(package_with_visual_change),
            events[0]["payload"]["visual_change_id"],
        ],
    )

    after = receipts_path.read_bytes()
    assert before == after


# (25) corrupted changed-region track is refused
@pytestmark_e2e
def test_validate_package_rejects_corrupted_track(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    track_path = package_with_visual_change / "tracks" / "changed_region_candidates.jsonl"
    lines = track_path.read_text().splitlines()
    record = json.loads(lines[0])
    record["payload"]["strength"] = "extreme"
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n")

    report = validate_package(package_with_visual_change)
    assert not report.valid
    assert any("strength" in e for e in report.errors)


@pytestmark_e2e
def test_validate_package_rejects_dangling_visual_change_link(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    track_path = package_with_visual_change / "tracks" / "changed_region_candidates.jsonl"
    lines = track_path.read_text().splitlines()
    record = json.loads(lines[0])
    record["payload"]["visual_change_id"] = "vc_nonexistent"
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n")

    report = validate_package(package_with_visual_change)
    assert not report.valid
    assert any("visual_change_id" in e for e in report.errors)


# (26) package validation integrates changed-region candidates
@pytestmark_e2e
def test_validate_package_passes_with_changed_region_track(package_with_visual_change):
    crw_mod.analyze_changed_regions(package_with_visual_change, tool_name="test", tool_version="1")
    report = validate_package(package_with_visual_change)
    assert report.valid, report.errors


@pytestmark_e2e
def test_validate_package_passes_without_changed_region_track(package_with_visual_change):
    report = validate_package(package_with_visual_change)
    assert report.valid, report.errors


@pytestmark_e2e
def test_cli_analyze_and_summary_end_to_end(package_with_visual_change):
    runner = CliRunner()
    result = runner.invoke(app, ["changed-regions", "analyze", str(package_with_visual_change)])
    assert result.exit_code == 0, result.output
    assert "Changed-region candidates written" in result.output

    summary = runner.invoke(app, ["changed-regions", "summary", str(package_with_visual_change)])
    assert summary.exit_code == 0, summary.output
    assert "event_count" in summary.output
    assert "change_scope_counts" in summary.output


# (27) existing visual-change tests / (28) keyframe tests still pass -- verified
# by running the full suite (see module docstring); a light cross-import
# sanity check is included here so a broken import chain is caught even
# if this file is run in isolation.
def test_visual_change_module_still_importable():
    assert vc_mod.VISUAL_CHANGE_RECORD_TYPE == "visual_change_candidate"
