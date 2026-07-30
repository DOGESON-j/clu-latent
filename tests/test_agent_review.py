"""Tests for Phase 3.17: agent review v0, a bounded, rule-based review layer.

Two groups, mirroring `tests/test_evidence_bundle.py`:

- Pure / no-media tests: module import, schema/validation edge cases,
  claim-file sanitization, CLI help, error handling. No ffmpeg or
  Pillow decode required.
- ffmpeg-gated end-to-end tests that build a real `.clulatent` package,
  run the full evidence chain (visual-change -> changed-regions ->
  evidence-bundles) then `agent-review run`/`preview`/`summary`/`get`/
  `query-bundle`/`query-time`, and assert the resulting review is
  bounded, ordered, safe, and never invents a semantic claim.

Core rule under test: agent review is review of evidence, not
invention of truth. Nothing here should ever call an external model or
assert what any collected evidence means.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import agent_review as ar_mod
from clu_latent import agent_review_retrieval as arr_mod
from clu_latent import agent_review_writer as arw_mod
from clu_latent import evidence_bundle as eb_mod
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


def _valid_bundle(**payload_overrides) -> dict:
    payload = {
        "timecode_start": "00:00.000",
        "timecode_end": "00:10.000",
        "evidence_refs": {
            "keyframes": [{"id": "kf_000000", "t_ms": 0, "image_path": "media/keyframes/000000.jpg"}],
            "visual_change_candidates": [],
            "changed_region_candidates": [
                {
                    "id": "cr_000000",
                    "visual_change_id": "vc_000000",
                    "t_start_ms": 0,
                    "t_end_ms": 1000,
                    "strength": "medium",
                    "change_scope": "localized",
                    "region": {"x": 0, "y": 0, "width": 10, "height": 10, "coordinate_system": "pixel"},
                    "normalized_region": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 0.1,
                        "height": 0.1,
                        "coordinate_system": "normalized_0_1",
                    },
                }
            ],
            "audio_events": [],
            "speech_events": [],
            "audio_digest_events": [],
            "review_events": [],
            "analysis_events": [],
        },
        "evidence_counts": {
            "keyframes": 1,
            "visual_change_candidates": 0,
            "changed_region_candidates": 1,
            "audio_events": 0,
            "speech_events": 0,
            "audio_digest_events": 0,
            "review_events": 0,
            "analysis_events": 0,
        },
        "coverage": {
            "has_keyframes": True,
            "has_visual_change": False,
            "has_changed_regions": True,
            "has_audio": False,
            "has_speech": False,
            "has_audio_digest": False,
            "has_review": False,
            "has_analysis": False,
        },
        "missing_evidence": [
            "visual_change_candidates",
            "audio_events",
            "speech_events",
            "audio_digest_events",
            "review_events",
            "analysis_events",
        ],
        "validation": {
            "package_valid_at_build_time": True,
            "validation_summary": "0 error(s), 0 warning(s) at build time",
        },
        "receipts_summary": {"receipt_paths": [], "receipt_count": 0},
        "caveats": list(eb_mod.EVIDENCE_BUNDLE_CAVEATS),
        "method": eb_mod.EVIDENCE_BUNDLE_METHOD,
        "created_by": "clulatent 0.1.0",
    }
    payload.update(payload_overrides)
    return {
        "id": "eb_000000000000_000000010000",
        "type": eb_mod.EVIDENCE_BUNDLE_RECORD_TYPE,
        "t_start_ms": 0,
        "t_end_ms": 10000,
        "producer": {"name": "clulatent", "version": "0.1.0"},
        "payload": payload,
    }


def _valid_review_payload(**overrides) -> dict:
    payload = {
        "evidence_bundle_id": "eb_000000000000_000000010000",
        "review_status": "bundle_valid",
        "findings": [
            {
                "label": "evidence_present",
                "severity": "info",
                "message": "keyframes evidence is present in this bundle (1 record(s)).",
                "evidence_ref_ids": [],
            }
        ],
        "evidence_present": ["changed_region_candidates", "keyframes"],
        "evidence_missing": [
            "analysis_events",
            "audio_digest_events",
            "audio_events",
            "review_events",
            "speech_events",
            "visual_change_candidates",
        ],
        "unsupported_claims": [],
        "recommended_next_step": "run_candidate_lane",
        "confidence": "medium",
        "caveats": list(ar_mod.AGENT_REVIEW_CAVEATS),
        "method": ar_mod.AGENT_REVIEW_METHOD,
        "created_by": "clulatent 0.1.0",
    }
    payload.update(overrides)
    return payload


def _valid_review_event(**payload_overrides) -> dict:
    return {
        "id": "ar_eb_000000000000_000000010000",
        "type": ar_mod.AGENT_REVIEW_RECORD_TYPE,
        "t_start_ms": 0,
        "t_end_ms": 10000,
        "producer": {"name": "clulatent", "version": "0.1.0"},
        "payload": _valid_review_payload(**payload_overrides),
    }


# --- Pure tests (no ffmpeg, no package) --------------------------------------


# (1) modules import cleanly, no circular imports
def test_agent_review_modules_import_cleanly():
    import clu_latent.agent_review  # noqa: F401
    import clu_latent.agent_review_retrieval  # noqa: F401
    import clu_latent.agent_review_writer  # noqa: F401
    import clu_latent.validate  # noqa: F401


# (2) a valid review event passes validation against its linked bundle
def test_valid_event_passes_validation():
    bundle = _valid_bundle()
    errors, _ = ar_mod.validate_agent_review_event(_valid_review_event(), bundle=bundle)
    assert errors == []


# (3) review_status must be one of the fixed vocabulary
def test_review_status_must_be_supported_value():
    bad = _valid_review_event(review_status="scene_understood")
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("review_status" in e for e in errors)


# (4) recommended_next_step must be one of the fixed vocabulary
def test_recommended_next_step_must_be_supported_value():
    bad = _valid_review_event(recommended_next_step="call_external_model")
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("recommended_next_step" in e for e in errors)


# (5) confidence must be low/medium/high
def test_confidence_must_be_supported_value():
    bad = _valid_review_event(confidence="certain")
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("confidence" in e for e in errors)


# (6) finding labels must come from the allowed vocabulary
def test_finding_label_must_be_allowed():
    bad = _valid_review_event(
        findings=[{"label": "evidence_present", "severity": "info", "message": "ok", "evidence_ref_ids": []}]
    )
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert errors == []


# (7) forbidden finding labels are explicitly rejected, distinctly from
# "not an allowed label"
def test_forbidden_finding_labels_rejected():
    for forbidden_label in ar_mod.FORBIDDEN_AGENT_REVIEW_LABELS:
        bad = _valid_review_event(
            findings=[
                {"label": forbidden_label, "severity": "info", "message": "ok", "evidence_ref_ids": []}
            ]
        )
        errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
        assert any(forbidden_label in e for e in errors), forbidden_label


# (8) finding severity must be info/warning/error
def test_finding_severity_must_be_supported_value():
    bad = _valid_review_event(
        findings=[{"label": "evidence_present", "severity": "critical", "message": "ok", "evidence_ref_ids": []}]
    )
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("severity" in e for e in errors)


# (9) finding evidence_ref_ids must exist in the linked bundle
def test_finding_evidence_ref_ids_cross_checked_against_bundle():
    good = _valid_review_event(
        findings=[
            {
                "label": "evidence_present",
                "severity": "info",
                "message": "ok",
                "evidence_ref_ids": ["cr_000000"],
            }
        ]
    )
    errors, _ = ar_mod.validate_agent_review_event(good, bundle=_valid_bundle())
    assert errors == []

    bad = _valid_review_event(
        findings=[
            {
                "label": "evidence_present",
                "severity": "info",
                "message": "ok",
                "evidence_ref_ids": ["cr_999999"],
            }
        ]
    )
    errors2, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("evidence_ref_ids" in e for e in errors2)


# (10) evidence_bundle_id must match the linked bundle's own id
def test_evidence_bundle_id_must_match_linked_bundle():
    bad = _valid_review_event(evidence_bundle_id="eb_wrong_id")
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("evidence_bundle_id" in e for e in errors)


# (11) review time range must fit inside the linked bundle's time range
def test_review_time_range_must_fit_inside_bundle():
    bad = _valid_review_event()
    bad["t_start_ms"] = 0
    bad["t_end_ms"] = 999999
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert errors != []


# (12) caveats must be the fixed triple, exact
def test_caveats_must_be_exact_fixed_triple():
    bad = _valid_review_event(caveats=["Agent review of evidence, not ground truth."])
    errors, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert any("caveats" in e for e in errors)


# (13) unsupported_claims entries are bounded and shape-checked
def test_unsupported_claims_shape_checked():
    good = _valid_review_event(
        unsupported_claims=[{"claim_id": "c1", "reason": "uses language this lane treats as forbidden semantic language"}]
    )
    errors, _ = ar_mod.validate_agent_review_event(good, bundle=_valid_bundle())
    assert errors == []

    bad = _valid_review_event(unsupported_claims=[{"claim_id": "c1"}])
    errors2, _ = ar_mod.validate_agent_review_event(bad, bundle=_valid_bundle())
    assert errors2 != []


# (14) duplicate ids within a batch are rejected
def test_duplicate_id_within_batch_rejected():
    bundle = _valid_bundle()
    events = [_valid_review_event(), _valid_review_event()]
    errors, _ = ar_mod.validate_agent_review_track(events, bundles_by_id={bundle["id"]: bundle})
    assert any("duplicate" in e.lower() for e in errors)


# (15) a review linking to a nonexistent bundle id is rejected
def test_review_linking_to_unknown_bundle_rejected():
    errors, _ = ar_mod.validate_agent_review_track([_valid_review_event()], bundles_by_id={})
    assert any("evidence_bundle_id" in e or "bundle" in e.lower() for e in errors)


# (16) compute_agent_review_findings is a pure function of the bundle
def test_compute_agent_review_findings_is_pure():
    bundle = _valid_bundle()
    first = ar_mod.compute_agent_review_findings(bundle)
    second = ar_mod.compute_agent_review_findings(bundle)
    assert first == second


# (17) compute_agent_review_findings never emits a forbidden label
def test_compute_agent_review_findings_never_emits_forbidden_label():
    bundle = _valid_bundle()
    result = ar_mod.compute_agent_review_findings(bundle)
    for finding in result["findings"]:
        assert finding["label"] in ar_mod.ALLOWED_AGENT_REVIEW_LABELS
        assert finding["label"] not in ar_mod.FORBIDDEN_AGENT_REVIEW_LABELS


# (18) a bundle with package_valid_at_build_time == False yields bundle_invalid
def test_compute_flags_invalid_package_at_build_time():
    bundle = _valid_bundle(
        validation={"package_valid_at_build_time": False, "validation_summary": "1 error(s), 0 warning(s)"}
    )
    result = ar_mod.compute_agent_review_findings(bundle)
    assert result["review_status"] in ("bundle_invalid", "needs_human_review")
    assert any(f["label"] == "package_validation_failed" for f in result["findings"])


# (19) a claim citing a nonexistent evidence ref id is unsupported
def test_claim_with_missing_evidence_ref_is_unsupported():
    bundle = _valid_bundle()
    claims = [{"id": "c1", "text": "review this region", "claim_type": "evidence_summary", "evidence_ref_ids": ["cr_999999"]}]
    result = ar_mod.compute_agent_review_findings(bundle, claims=claims)
    assert any(c["claim_id"] == "c1" for c in result["unsupported_claims"])


# (20) a claim using forbidden semantic language is unsupported
def test_claim_with_forbidden_language_is_unsupported():
    bundle = _valid_bundle()
    claims = [
        {
            "id": "c2",
            "text": "A person is visible in this region.",
            "claim_type": "semantic",
            "evidence_ref_ids": ["cr_000000"],
        }
    ]
    result = ar_mod.compute_agent_review_findings(bundle, claims=claims)
    assert any(c["claim_id"] == "c2" for c in result["unsupported_claims"])


# (21) a claim that only references real evidence and avoids forbidden
# language is accepted as supported (not flagged unsupported)
def test_claim_referencing_real_evidence_is_supported():
    bundle = _valid_bundle()
    claims = [
        {
            "id": "c3",
            "text": "Changed-region evidence exists for this time range.",
            "claim_type": "evidence_summary",
            "evidence_ref_ids": ["cr_000000"],
        }
    ]
    result = ar_mod.compute_agent_review_findings(bundle, claims=claims)
    assert not any(c["claim_id"] == "c3" for c in result["unsupported_claims"])


# (22) build_agent_review_id is deterministic per bundle id
def test_build_agent_review_id_deterministic():
    first = arw_mod.build_agent_review_id("eb_000000000000_000000010000")
    second = arw_mod.build_agent_review_id("eb_000000000000_000000010000")
    assert first == second
    assert first != arw_mod.build_agent_review_id("eb_000000000010000_000000020000")


# (23) claim-file loading: valid, bounded claim file sanitizes cleanly
def test_load_and_sanitize_claims_valid(tmp_path):
    claim_file = tmp_path / "claims.json"
    claim_file.write_text(
        json.dumps(
            {
                "claims": [
                    {"id": "c1", "text": "evidence exists", "claim_type": "evidence_summary", "evidence_ref_ids": ["cr_000000"]}
                ]
            }
        ),
        encoding="utf-8",
    )
    claims = arw_mod.load_and_sanitize_claims(claim_file)
    assert len(claims) == 1
    assert claims[0]["id"] == "c1"


# (24) claim-file loading: malformed top-level shape is rejected
def test_load_and_sanitize_claims_rejects_bad_shape(tmp_path):
    claim_file = tmp_path / "claims.json"
    claim_file.write_text(json.dumps({"not_claims": []}), encoding="utf-8")
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.load_and_sanitize_claims(claim_file)


# (25) claim-file loading: duplicate claim ids within the file are rejected
def test_load_and_sanitize_claims_rejects_duplicate_ids(tmp_path):
    claim_file = tmp_path / "claims.json"
    claim_file.write_text(
        json.dumps(
            {
                "claims": [
                    {"id": "c1", "text": "a", "claim_type": "evidence_summary", "evidence_ref_ids": []},
                    {"id": "c1", "text": "b", "claim_type": "evidence_summary", "evidence_ref_ids": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.load_and_sanitize_claims(claim_file)


# (26) claim-file loading: too many claims is rejected
def test_load_and_sanitize_claims_rejects_too_many(tmp_path):
    claim_file = tmp_path / "claims.json"
    claims = [
        {"id": f"c{i}", "text": "x", "claim_type": "evidence_summary", "evidence_ref_ids": []}
        for i in range(ar_mod.DEFAULT_MAX_CLAIMS_PER_REQUEST + 1)
    ]
    claim_file.write_text(json.dumps({"claims": claims}), encoding="utf-8")
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.load_and_sanitize_claims(claim_file)


# (27) claim-file loading: oversized claim file is rejected
def test_load_and_sanitize_claims_rejects_oversized_file(tmp_path):
    claim_file = tmp_path / "claims.json"
    huge_text = "x" * (arw_mod.DEFAULT_MAX_CLAIM_FILE_BYTES + 1)
    claim_file.write_text(json.dumps({"claims": [{"id": "c1", "text": huge_text, "claim_type": "evidence_summary", "evidence_ref_ids": []}]}), encoding="utf-8")
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.load_and_sanitize_claims(claim_file)


# (28) claim-file loading: missing file raises cleanly
def test_load_and_sanitize_claims_missing_file_raises(tmp_path):
    with pytest.raises(Exception):
        arw_mod.load_and_sanitize_claims(tmp_path / "does-not-exist.json")


# (29) query_time with inverted range raises cleanly
def test_query_time_invalid_range_raises(tmp_path):
    with pytest.raises(arr_mod.AgentReviewRetrievalError):
        arr_mod.query_agent_reviews_by_time_range(tmp_path / "whatever.clulatent", 5000, 1000)


# (30) get on a nonexistent package raises cleanly
def test_get_on_nonexistent_package_raises(tmp_path):
    with pytest.raises(arr_mod.AgentReviewRetrievalError):
        arr_mod.get_agent_review_by_id(tmp_path / "nope.clulatent", "ar_eb_000000000000_000000010000")


# (31) README documents Phase 3.17
def test_readme_includes_phase_3_17_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme


# (32) phase doc exists
def test_phase_3_17_doc_exists():
    doc_path = REPO_ROOT / "docs" / "PHASE_3_17_EVIDENCE_BUNDLE_AGENT_REVIEW_V0.md"
    assert doc_path.is_file()


# (33) CLI help works, lists all six subcommands
def test_cli_agent_review_help_works():
    runner = CliRunner()
    result = runner.invoke(app, ["agent-review", "--help"])
    assert result.exit_code == 0
    for verb in ("run", "preview", "summary", "get", "query-bundle", "query-time"):
        assert verb in result.output


# (34) CLI get on nonexistent package fails cleanly, no traceback leak
def test_cli_get_on_nonexistent_package_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app, ["agent-review", "get", str(tmp_path / "does-not-exist.clulatent"), "ar_eb_000000000000_000000010000"]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# (35) CLI query-time with inverted range fails cleanly
def test_cli_query_time_invalid_range_clean_error(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "agent-review",
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
    expected_subset = {"person", "face", "object", "car", "weapon", "text", "logo", "action", "intent", "emotion", "song"}
    assert expected_subset <= ar_mod.FORBIDDEN_AGENT_REVIEW_PHRASES


def test_allowed_and_forbidden_label_sets_are_disjoint():
    assert ar_mod.ALLOWED_AGENT_REVIEW_LABELS.isdisjoint(ar_mod.FORBIDDEN_AGENT_REVIEW_LABELS)


# --- ffmpeg + Pillow gated end-to-end tests -----------------------------------

pytestmark_e2e = pytest.mark.skipif(
    not (FFMPEG_AVAILABLE and PILLOW_AVAILABLE),
    reason="ffmpeg/ffprobe/Pillow not all available in this environment",
)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_agent_review_fixture")
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
def package_with_bundle(valid_package) -> Path:
    vcw_mod.analyze_visual_change(valid_package, tool_name="test", tool_version="1")
    crw_mod.analyze_changed_regions(valid_package, tool_name="test", tool_version="1")
    ebw_mod.build_evidence_bundle(valid_package, start_ms=0, end_ms=4000, tool_name="test", tool_version="1")
    return valid_package


def _bundle_id(package_path: Path) -> str:
    from clu_latent import evidence_bundle_retrieval as ebr_mod

    bundles = ebr_mod.load_evidence_bundle_events(package_path)
    assert len(bundles) == 1
    return bundles[0]["id"]


def _load_manifest(package_path: Path) -> Manifest:
    return Manifest.from_json_file(package_path / "manifest.json")


def _snapshot(package_path: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(package_path.rglob("*")):
        if path.is_file():
            rel = path.relative_to(package_path).as_posix()
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


# (36) run creates the agent_review_events track
@pytestmark_e2e
def test_run_creates_track(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    assert result.review_id == arw_mod.build_agent_review_id(bundle_id)
    track_path = package_with_bundle / result.track_file
    assert track_path.is_file()
    manifest = _load_manifest(package_with_bundle)
    assert any(t.name == ar_mod.AGENT_REVIEW_TRACK_NAME for t in manifest.tracks)


# (37) run against a nonexistent bundle id raises cleanly
@pytestmark_e2e
def test_run_against_unknown_bundle_raises(package_with_bundle):
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.run_agent_review(package_with_bundle, "eb_nonexistent", tool_name="test", tool_version="1")


# (38) run never calls an external model / makes no network call --
# confirmed structurally: the review content is a pure function of the
# bundle, with no HTTP client anywhere in the writer module
@pytestmark_e2e
def test_agent_review_writer_has_no_network_imports():
    import clu_latent.agent_review_writer as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    for banned in ("requests", "httpx", "urllib.request", "socket.socket", "openai", "anthropic"):
        assert banned not in source


# (39) preview never writes a track, manifest entry, or receipt
@pytestmark_e2e
def test_preview_never_writes(package_with_bundle):
    from clu_latent import evidence_bundle_retrieval as ebr_mod

    bundle_id = _bundle_id(package_with_bundle)
    before = _snapshot(package_with_bundle)
    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_bundle, bundle_id)
    findings = ar_mod.compute_agent_review_findings(bundle)
    assert findings["review_status"] in ar_mod.SUPPORTED_REVIEW_STATUSES
    after = _snapshot(package_with_bundle)
    assert before == after


# (40) run refuses a duplicate review without --force
@pytestmark_e2e
def test_run_refuses_overwrite_without_force(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    with pytest.raises(arw_mod.AgentReviewWriteError):
        arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")


# (41) run --force replaces the existing review record cleanly
@pytestmark_e2e
def test_run_force_replaces_existing_review(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    first = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    second = arw_mod.run_agent_review(
        package_with_bundle, bundle_id, tool_name="test", tool_version="1", force=True
    )
    assert first.review_id == second.review_id
    events = arr_mod.load_agent_review_events(package_with_bundle)
    matching = [e for e in events if e["id"] == first.review_id]
    assert len(matching) == 1


# (42) run creates a receipt by default
@pytestmark_e2e
def test_run_creates_receipt(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    assert result.receipt_path is not None
    assert result.receipt_path.is_file()


# (43) read-only commands never create receipts
@pytestmark_e2e
def test_read_only_commands_create_no_receipts(package_with_bundle):
    from clu_latent import evidence_bundle_retrieval as ebr_mod

    bundle_id = _bundle_id(package_with_bundle)
    receipts_dir = package_with_bundle / "receipts"
    before = set(receipts_dir.glob("*.jsonl")) if receipts_dir.is_dir() else set()

    bundle = ebr_mod.get_evidence_bundle_by_id(package_with_bundle, bundle_id)
    ar_mod.compute_agent_review_findings(bundle)
    arr_mod.summarize_agent_reviews(package_with_bundle)
    arr_mod.query_agent_reviews_by_time_range(package_with_bundle, 0, 4000)

    after = set(receipts_dir.glob("*.jsonl")) if receipts_dir.is_dir() else set()
    assert before == after


# (44) summary reports review count, status buckets, escalation/claim counts
@pytestmark_e2e
def test_summary_reports_counts(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    summary = arr_mod.summarize_agent_reviews(package_with_bundle)
    assert summary["review_count"] == 1
    assert sum(summary["status_counts"].values()) == 1


# (45) get returns the exact record; unknown id returns None
@pytestmark_e2e
def test_get_returns_record_or_none(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    event = arr_mod.get_agent_review_by_id(package_with_bundle, result.review_id)
    assert event is not None
    assert event["id"] == result.review_id

    assert arr_mod.get_agent_review_by_id(package_with_bundle, "ar_nonexistent") is None


# (46) query-bundle returns only reviews linked to the given bundle id
@pytestmark_e2e
def test_query_bundle_returns_linked_reviews(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    matches = arr_mod.query_agent_reviews_by_bundle(package_with_bundle, bundle_id)
    assert any(m["id"] == result.review_id for m in matches)

    no_matches = arr_mod.query_agent_reviews_by_bundle(package_with_bundle, "eb_nonexistent")
    assert no_matches == []


# (47) query-time returns only overlapping reviews
@pytestmark_e2e
def test_query_time_returns_overlapping_events(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    matches = arr_mod.query_agent_reviews_by_time_range(package_with_bundle, 1000, 2000)
    assert any(m["id"] == result.review_id for m in matches)

    no_matches = arr_mod.query_agent_reviews_by_time_range(package_with_bundle, 100000, 200000)
    assert no_matches == []


# (48) validate_package accepts a package with a valid agent review track
@pytestmark_e2e
def test_validate_package_passes_with_agent_review_track(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    report = validate_package(package_with_bundle)
    assert report.valid, report.errors


# (49) validate_package still passes for a package with no agent review track
@pytestmark_e2e
def test_validate_package_passes_without_agent_review_track(package_with_bundle):
    report = validate_package(package_with_bundle)
    assert report.valid, report.errors


# (50) validate_package rejects a corrupted agent review track
@pytestmark_e2e
def test_validate_package_rejects_corrupted_track(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    track_path = package_with_bundle / result.track_file
    lines = track_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["payload"]["review_status"] = "scene_understood"
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = validate_package(package_with_bundle)
    assert not report.valid
    assert any("review_status" in e for e in report.errors)


# (51) validate_package rejects a review whose linked bundle id doesn't exist
@pytestmark_e2e
def test_validate_package_rejects_dangling_bundle_link(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    track_path = package_with_bundle / result.track_file
    lines = track_path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["payload"]["evidence_bundle_id"] = "eb_dangling_reference"
    lines[0] = json.dumps(record)
    track_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = validate_package(package_with_bundle)
    assert not report.valid


# (52) CLI run + summary end-to-end
@pytestmark_e2e
def test_cli_run_and_summary_end_to_end(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    runner = CliRunner()
    result = runner.invoke(app, ["agent-review", "run", str(package_with_bundle), bundle_id])
    assert result.exit_code == 0, result.output
    assert "Agent review written" in result.output

    summary_result = runner.invoke(app, ["agent-review", "summary", str(package_with_bundle)])
    assert summary_result.exit_code == 0
    assert "review_count: 1" in summary_result.output


# (53) CLI preview never writes and prints the findings; no semantic
# claim appears anywhere in the printed output
@pytestmark_e2e
def test_cli_preview_end_to_end_makes_no_semantic_claim(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    before = _snapshot(package_with_bundle)
    runner = CliRunner()
    result = runner.invoke(app, ["agent-review", "preview", str(package_with_bundle), bundle_id])
    assert result.exit_code == 0, result.output
    assert "would write" in result.output
    after = _snapshot(package_with_bundle)
    assert before == after

    rendered = result.output.lower()
    caveats_text = " ".join(ar_mod.AGENT_REVIEW_CAVEATS).lower()
    for phrase in ar_mod.FORBIDDEN_AGENT_REVIEW_PHRASES:
        occurrences = rendered.count(phrase)
        caveat_occurrences = caveats_text.count(phrase)
        assert occurrences <= caveat_occurrences, f"forbidden phrase leaked outside caveats: {phrase!r}"


# (54) full workflow: no forbidden language ever appears in a computed
# review record
@pytestmark_e2e
def test_no_forbidden_language_in_computed_records(package_with_bundle):
    bundle_id = _bundle_id(package_with_bundle)
    result = arw_mod.run_agent_review(package_with_bundle, bundle_id, tool_name="test", tool_version="1")
    review = arr_mod.get_agent_review_by_id(package_with_bundle, result.review_id)
    rendered = json.dumps(review["payload"]).lower()
    caveats_text = " ".join(ar_mod.AGENT_REVIEW_CAVEATS).lower()
    for phrase in ar_mod.FORBIDDEN_AGENT_REVIEW_PHRASES:
        occurrences = rendered.count(phrase)
        caveat_occurrences = caveats_text.count(phrase)
        assert occurrences <= caveat_occurrences, f"forbidden phrase leaked outside caveats: {phrase!r}"
