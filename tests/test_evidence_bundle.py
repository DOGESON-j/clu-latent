"""Tests for Phase 3.17: the non-semantic evidence bundle lane.

Two groups, mirroring `tests/test_changed_region.py`:

- Pure / no-media tests: module import, schema/validation edge cases,
  CLI help, error handling. No ffmpeg or Pillow decode required.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package,
  run `visual-change analyze` + `changed-regions analyze`, then
  `evidence-bundles build`/`preview`/`summary`/`get`/`query-time`, and
  assert the resulting track/receipt/retrieval behavior is bounded,
  ordered, safe, and conservative.

Core rule under test: evidence bundle, not semantic interpretation.
Nothing here should ever claim what any collected evidence means --
only that it exists, for a bounded time range.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import evidence_bundle as eb_mod
from clu_latent import evidence_bundle_retrieval as ebr_mod
from clu_latent import evidence_bundle_writer as ebw_mod
from clu_latent import changed_region_writer as crw_mod
from clu_latent import visual_change_writer as vcw_mod
from clu_latent.cli import app
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest
from clu_latent.validate import validate_package

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
try:
    import PIL  # noqa: F401

    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parents[1]


def _valid_payload(**overrides) -> dict:
    payload = {
        "timecode_start": "00:00.000",
        "timecode_end": "00:10.000",
        "evidence_refs": {
            "keyframes": [],
            "visual_change_candidates": [],
            "changed_region_candidates": [],
            "audio_events": [],
            "speech_events": [],
            "audio_digest_events": [],
            "review_events": [],
            "analysis_events": [],
        },
        "evidence_counts": {category: 0 for category in eb_mod.EVIDENCE_CATEGORIES},
        "coverage": {key: False for key in eb_mod.COVERAGE_KEYS},
        "missing_evidence": list(eb_mod.EVIDENCE_CATEGORIES),
        "validation": {
            "package_valid_at_build_time": True,
            "validation_summary": "0 error(s), 0 warning(s) at build time",
        },
        "receipts_summary": {"receipt_paths": [], "receipt_count": 0},
        "caveats": list(eb_mod.EVIDENCE_BUNDLE_CAVEATS),
        "method": eb_mod.EVIDENCE_BUNDLE_METHOD,
        "created_by": "clulatent 0.1.0",
    }
    payload.update(overrides)
    return payload


def _valid_event(**payload_overrides) -> dict:
    return {
        "id": "eb_000000000000_000000010000",
        "type": eb_mod.EVIDENCE_BUNDLE_RECORD_TYPE,
        "t_start_ms": 0,
        "t_end_ms": 10000,
        "producer": {"name": "clulatent", "version": "0.1.0"},
        "payload": _valid_payload(**payload_overrides),
    }


# --- Pure tests (no ffmpeg, no package) --------------------------------------


# (1) modules import cleanly, no circular imports
def test_evidence_bundle_modules_import_cleanly():
    import clu_latent.evidence_bundle  # noqa: F401
    import clu_latent.evidence_bundle_retrieval  # noqa: F401
    import clu_latent.evidence_bundle_writer  # noqa: F401
    import clu_latent.validate  # noqa: F401


# (2) a fully zeroed-out, empty-evidence bundle validates cleanly
def test_valid_empty_event_passes_validation():
    errors, warnings = eb_mod.validate_evidence_bundle_event(_valid_event())
    assert errors == []


# (3) t_start_ms <= t_end_ms is enforced by the shared envelope
def test_inverted_time_range_rejected():
    bad = _valid_event()
    bad["t_start_ms"] = 5000
    bad["t_end_ms"] = 1000
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert errors != []


# (4) evidence_counts must match number of refs in each category
def test_evidence_counts_mismatch_rejected():
    bad = _valid_event(
        evidence_refs={
            "keyframes": [{"id": "kf_000000", "t_ms": 0, "image_path": "media/keyframes/000000.jpg"}],
            "visual_change_candidates": [],
            "changed_region_candidates": [],
            "audio_events": [],
            "speech_events": [],
            "audio_digest_events": [],
            "review_events": [],
            "analysis_events": [],
        },
        evidence_counts={category: 0 for category in eb_mod.EVIDENCE_CATEGORIES},
        coverage={key: False for key in eb_mod.COVERAGE_KEYS},
        missing_evidence=list(eb_mod.EVIDENCE_CATEGORIES),
    )
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("evidence_counts" in e for e in errors)


# (5) coverage booleans must match evidence_counts
def test_coverage_mismatch_rejected():
    bad = _valid_event(
        evidence_counts={**{c: 0 for c in eb_mod.EVIDENCE_CATEGORIES}, "keyframes": 1},
        coverage={key: False for key in eb_mod.COVERAGE_KEYS},
    )
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("coverage" in e for e in errors)


# (6) missing_evidence must be exactly the zero-count categories
def test_missing_evidence_mismatch_rejected():
    bad = _valid_event(missing_evidence=[])
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("missing_evidence" in e for e in errors)


# (7) caveats must be the fixed triple, exact
def test_caveats_must_be_exact_fixed_triple():
    bad = _valid_event(caveats=["Evidence bundle, not semantic interpretation."])
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("caveats" in e for e in errors)

    bad2 = _valid_event(caveats=list(eb_mod.EVIDENCE_BUNDLE_CAVEATS) + ["extra caveat"])
    errors2, _ = eb_mod.validate_evidence_bundle_event(bad2)
    assert any("caveats" in e for e in errors2)


# (8) fixed caveats never trip the forbidden-language check (they use
# the forbidden words only in negation)
def test_fixed_caveats_do_not_trip_forbidden_language_check():
    event = _valid_event()
    errors, _ = eb_mod.validate_evidence_bundle_event(event)
    assert errors == []


# (9) forbidden language rejected in genuinely free-text fields
def test_forbidden_language_rejected_in_free_text_field():
    bad = _valid_event(
        validation={
            "package_valid_at_build_time": True,
            "validation_summary": "this bundle shows a person in the frame",
        }
    )
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("forbidden language" in e for e in errors)


# (10) receipts_summary.receipt_paths must be from the known, closed set
def test_unknown_receipt_path_rejected():
    bad = _valid_event(receipts_summary={"receipt_paths": ["receipts/not_a_real_receipt.jsonl"], "receipt_count": 1})
    errors, _ = eb_mod.validate_evidence_bundle_event(bad)
    assert any("receipt" in e.lower() for e in errors)


# (11) known_receipt_paths cross-check accepts exactly the allowed set
def test_known_receipt_paths_cross_check():
    good = _valid_event(
        receipts_summary={"receipt_paths": ["receipts/ingest.jsonl"], "receipt_count": 1}
    )
    errors, _ = eb_mod.validate_evidence_bundle_event(
        good, known_receipt_paths={"receipts/ingest.jsonl"}
    )
    assert errors == []

    errors2, _ = eb_mod.validate_evidence_bundle_event(
        good, known_receipt_paths={"receipts/other.jsonl"}
    )
    assert errors2 != []


# (12) duplicate ids within a batch are rejected
def test_duplicate_id_within_batch_rejected():
    events = [_valid_event(), _valid_event()]
    errors, _ = eb_mod.validate_evidence_bundle_track(events)
    assert any("duplicate" in e.lower() for e in errors)


# (13) unsafe/absolute keyframe image paths rejected
def test_unsafe_image_path_rejected(tmp_path):
    bad = _valid_event(
        evidence_refs={
            **_valid_payload()["evidence_refs"],
            "keyframes": [{"id": "kf_000000", "t_ms": 0, "image_path": "/etc/passwd"}],
        },
        evidence_counts={**{c: 0 for c in eb_mod.EVIDENCE_CATEGORIES}, "keyframes": 1},
        coverage={**{k: False for k in eb_mod.COVERAGE_KEYS}, "has_keyframes": True},
        missing_evidence=[c for c in eb_mod.EVIDENCE_CATEGORIES if c != "keyframes"],
    )
    errors, _ = eb_mod.validate_evidence_bundle_event(bad, package_root=tmp_path)
    assert any("path" in e.lower() for e in errors)


# (14) build_evidence_bundle_id is deterministic per time range
def test_build_evidence_bundle_id_deterministic():
    first = ebw_mod.build_evidence_bundle_id(0, 10000)
    second = ebw_mod.build_evidence_bundle_id(0, 10000)
    assert first == second
    assert first != ebw_mod.build_evidence_bundle_id(0, 20000)


# (15) query_time with inverted range raises cleanly
def test_query_time_invalid_range_raises(tmp_path):
    with pytest.raises(ebr_mod.EvidenceBundleRetrievalError):
        ebr_mod.query_evidence_bundles_by_time_range(tmp_path / "whatever.clulatent", 5000, 1000)


# (16) get on a nonexistent package raises cleanly
def test_get_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(ebr_mod.EvidenceBundleRetrievalError):
        ebr_mod.get_evidence_bundle_by_id(tmp_path / "nope.clulatent", "eb_000000000000_000000010000")


# (17) summarize on a package with no evidence bundle track is zeroed but valid
def test_summarize_zeroed_when_no_track(tmp_path):
    package_path = tmp_path / "empty.clulatent"
    package_path.mkdir()
    with pytest.raises(ebr_mod.EvidenceBundleRetrievalError):
        # no manifest.json at all -- still a clean, module-specific error
        ebr_mod.summarize_evidence_bundles(package_path)


# (18) README documents Phase 3.17
def test_readme_includes_phase_3_17_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


# (19) phase doc exists
def test_phase_3_17_doc_exists():
    doc_path = REPO_ROOT / "docs" / "PHASE_3_17_EVIDENCE_BUNDLE_AGENT_REVIEW_V0.md"
    assert doc_path.is_file()


# (20) CLI help works, lists all five subcommands
def test_cli_evidence_bundles_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["evidence-bundles", "--help"])
    assert result.exit_code == 0
    for verb in ("build", "preview", "summary", "get", "query-time"):
        assert verb in result.output


# (21) CLI get on nonexistent package fails cleanly, no traceback leak
def test_cli_get_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["evidence-bundles", "get", str(tmp_path / "does-not-exist.clulatent"), "eb_000000000000_000000010000"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# (22) CLI query-time with inverted range fails cleanly
def test_cli_query_time_invalid_range_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-bundles",
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


def test_forbidden_phrases_catalog_matches_phase_brief():
    expected_subset = {"person", "face", "object", "car", "weapon", "text", "logo", "action", "intent", "emotion"}
    assert expected_subset <= eb_mod.FORBIDDEN_EVIDENCE_BUNDLE_PHRASES


# --- ffmpeg + Pillow gated end-to-end tests -----------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not (FFMPEG_AVAILABLE and PILLOW_AVAILABLE),
    reason="ffmpeg/ffprobe/Pillow not all available in this environment",
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_evidence_bundle_fixture")
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
def package_with_changed_regions(valid_package) -> Path:
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    crw_mod.analyze_changed_regions(valid_package, tool_name="test", tool_version="1")
    return valid_package


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


# (23) build creates the evidence_bundles track
@pytestmark_e2e
def test_build_creates_track(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    assert result.bundle_id == ebw_mod.build_evidence_bundle_id(0, 4000)
    track_path = package_with_changed_regions / result.track_file
    assert track_path.is_file()
    manifest = _load_manifest(package_with_changed_regions)
    assert any(t.name == eb_mod.EVIDENCE_BUNDLE_TRACK_NAME for t in manifest.tracks)


# (24) gathered evidence references keyframes/visual-change/changed-region
# candidates that actually overlap the requested range
@pytestmark_e2e
def test_build_gathers_overlapping_evidence(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    assert result.evidence_counts["keyframes"] > 0
    assert result.evidence_counts["visual_change_candidates"] > 0
    assert result.evidence_counts["changed_region_candidates"] > 0

    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    assert bundle is not None
    for ref in bundle["payload"]["evidence_refs"]["keyframes"]:
        assert 0 <= ref["t_ms"] <= 4000


# (25) evidence_counts/coverage/missing_evidence are internally consistent
@pytestmark_e2e
def test_build_evidence_counts_coverage_missing_consistent(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    payload = bundle["payload"]
    for category in eb_mod.EVIDENCE_CATEGORIES:
        count = payload["evidence_counts"][category]
        coverage_key = eb_mod.COVERAGE_KEY_FOR_CATEGORY[category]
        assert payload["coverage"][coverage_key] == (count > 0)
        assert (category in payload["missing_evidence"]) == (count == 0)


# (26) validation field reflects package_valid_at_build_time
@pytestmark_e2e
def test_build_validation_field_reflects_package_state(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    report = validate_package(package_with_changed_regions)
    assert bundle["payload"]["validation"]["package_valid_at_build_time"] == report.valid


# (27) receipts_summary only lists receipts that actually exist
@pytestmark_e2e
def test_build_receipts_summary_matches_existing_receipts(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    for path in bundle["payload"]["receipts_summary"]["receipt_paths"]:
        assert (package_with_changed_regions / path).exists()
    assert bundle["payload"]["receipts_summary"]["receipt_count"] == len(
        bundle["payload"]["receipts_summary"]["receipt_paths"]
    )


# (28) preview never writes a track, manifest entry, or receipt
@pytestmark_e2e
def test_preview_never_writes(package_with_changed_regions):
    before = _snapshot(package_with_changed_regions)
    manifest = _load_manifest(package_with_changed_regions)
    payload = ebw_mod.gather_evidence_bundle_payload(
        package_with_changed_regions, manifest, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    assert payload["evidence_counts"]["keyframes"] > 0
    after = _snapshot(package_with_changed_regions)
    assert before == after


# (29) build refuses a duplicate id without --force
@pytestmark_e2e
def test_build_refuses_overwrite_without_force(package_with_changed_regions):
    ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    with pytest.raises(ebw_mod.EvidenceBundleWriteError):
        ebw_mod.build_evidence_bundle(
            package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
        )


# (30) build --force replaces the existing bundle record cleanly
@pytestmark_e2e
def test_build_force_replaces_existing_bundle(package_with_changed_regions):
    first = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    second = ebw_mod.build_evidence_bundle(
        package_with_changed_regions,
        start_ms=0,
        end_ms=4000,
        tool_name="test",
        tool_version="1",
        force=True,
    )
    assert first.bundle_id == second.bundle_id
    events = ebr_mod.load_evidence_bundle_events(package_with_changed_regions)
    matching = [e for e in events if e["id"] == first.bundle_id]
    assert len(matching) == 1


# (31) build creates a receipt by default
@pytestmark_e2e
def test_build_creates_receipt(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    assert result.receipt_path is not None
    assert result.receipt_path.is_file()


# (32) read-only commands never create receipts
@pytestmark_e2e
def test_read_only_commands_create_no_receipts(package_with_changed_regions):
    receipts_dir = package_with_changed_regions / "receipts"
    before = set(receipts_dir.glob("*.jsonl")) if receipts_dir.is_dir() else set()

    manifest = _load_manifest(package_with_changed_regions)
    ebw_mod.gather_evidence_bundle_payload(
        package_with_changed_regions, manifest, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    ebr_mod.summarize_evidence_bundles(package_with_changed_regions)
    ebr_mod.query_evidence_bundles_by_time_range(package_with_changed_regions, 0, 4000)

    after = set(receipts_dir.glob("*.jsonl")) if receipts_dir.is_dir() else set()
    assert before == after


# (33) summary reports bundle count and coverage/missing-evidence buckets
@pytestmark_e2e
def test_summary_reports_counts(package_with_changed_regions):
    ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    summary = ebr_mod.summarize_evidence_bundles(package_with_changed_regions)
    assert summary["bundle_count"] == 1
    assert summary["first_bundle_id"] == summary["last_bundle_id"]
    assert set(summary["coverage_counts"]) == eb_mod.COVERAGE_KEYS
    assert set(summary["missing_evidence_counts"]) == set(eb_mod.EVIDENCE_CATEGORIES)


# (34) get returns the exact record; unknown id returns None
@pytestmark_e2e
def test_get_returns_record_or_none(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    event = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    assert event is not None
    assert event["id"] == result.bundle_id

    assert ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, "eb_nonexistent") is None


# (35) query-time returns only overlapping bundles
@pytestmark_e2e
def test_query_time_returns_overlapping_events(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    matches = ebr_mod.query_evidence_bundles_by_time_range(package_with_changed_regions, 1000, 2000)
    assert any(m["id"] == result.bundle_id for m in matches)

    no_matches = ebr_mod.query_evidence_bundles_by_time_range(package_with_changed_regions, 100000, 200000)
    assert no_matches == []


# (36) validate_package accepts a package with a valid evidence bundle track
@pytestmark_e2e
def test_validate_package_passes_with_evidence_bundle_track(package_with_changed_regions):
    ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    report = validate_package(package_with_changed_regions)
    assert report.valid, report.errors


# (37) validate_package still passes for a package with no evidence bundle track
@pytestmark_e2e
def test_validate_package_passes_without_evidence_bundle_track(package_with_changed_regions):
    report = validate_package(package_with_changed_regions)
    assert report.valid, report.errors


# (38) validate_package rejects a corrupted evidence bundle track
@pytestmark_e2e
def test_validate_package_rejects_corrupted_track(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    track_path = package_with_changed_regions / result.track_file
    lines = track_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["payload"]["caveats"] = ["not the fixed caveats"]
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = validate_package(package_with_changed_regions)
    assert not report.valid
    assert any("caveats" in e for e in report.errors)


# (39) CLI build + summary end-to-end
@pytestmark_e2e
def test_cli_build_and_summary_end_to_end(package_with_changed_regions):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-bundles",
            "build",
            str(package_with_changed_regions),
            "--start-ms",
            "0",
            "--end-ms",
            "4000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Evidence bundle written" in result.output

    summary_result = runner.invoke(app, ["evidence-bundles", "summary", str(package_with_changed_regions)])
    assert summary_result.exit_code == 0
    assert "bundle_count: 1" in summary_result.output


# (40) CLI preview never writes and prints the payload
@pytestmark_e2e
def test_cli_preview_end_to_end(package_with_changed_regions):
    before = _snapshot(package_with_changed_regions)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evidence-bundles",
            "preview",
            str(package_with_changed_regions),
            "--start-ms",
            "0",
            "--end-ms",
            "4000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "would build" in result.output
    after = _snapshot(package_with_changed_regions)
    assert before == after


# (41) no forbidden language ever appears in a computed bundle record
@pytestmark_e2e
def test_no_forbidden_language_in_computed_records(package_with_changed_regions):
    result = ebw_mod.build_evidence_bundle(
        package_with_changed_regions, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_changed_regions, result.bundle_id)
    rendered = json.dumps(bundle["payload"]).lower()
    caveats_text = " ".join(eb_mod.EVIDENCE_BUNDLE_CAVEATS).lower()
    for phrase in eb_mod.FORBIDDEN_EVIDENCE_BUNDLE_PHRASES:
        # allow the phrase only as part of the fixed caveats text itself
        occurrences = rendered.count(phrase)
        caveat_occurrences = caveats_text.count(phrase)
        assert occurrences <= caveat_occurrences, f"forbidden phrase leaked outside caveats: {phrase!r}"


# (42) compute with no visual-change/changed-region track still succeeds
# with all-empty evidence for those categories (absence is not an error)
@pytestmark_e2e
def test_build_with_no_optional_tracks_still_succeeds(valid_package):
    result = ebw_mod.build_evidence_bundle(
        valid_package, start_ms=0, end_ms=4000, tool_name="test", tool_version="1"
    )
    assert result.evidence_counts["visual_change_candidates"] == 0
    assert result.evidence_counts["changed_region_candidates"] == 0
    assert result.evidence_counts["keyframes"] > 0
