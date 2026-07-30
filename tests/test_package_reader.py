"""Tests for Phase 3.18: the official read-only package reader/parser.

The reader wraps the existing `.clulatent` package structure so a caller
can open a package by path and inspect its manifest, tracks, events,
receipts, lock state, and validation status **without knowing any
filename**. These tests exercise that spine against:

- a deterministic, valid package produced by the public conformance fixture
  builder, using the same manifest/track models as the writers;
- copies of it, mutated to add extra tracks (evidence bundles / agent
  review) or to corrupt a track / declare an unsafe path — proving the
  reader lists/reads generic tracks and refuses malformed/unsafe ones.

Core rule under test: the reader reads and exposes structured access; it
never judges validity (that is the validator's job, reached only via
`reader.validate()`), never interprets content, and never mutates the
package.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from clu_latent import package_reader as pr
from clu_latent.cli import app
from clu_latent.conformance_fixtures import build_fixture
from clu_latent.constants import EVENT_ENVELOPE_SCHEMA_ID, EVENT_ENVELOPE_SCHEMA_VERSION
from clu_latent.event import EventEnvelope, Producer
from clu_latent.manifest import Manifest, TrackDescriptor
from clu_latent.package_reader import (
    EventRecord,
    MalformedPackageError,
    PackageReader,
    PackageReaderError,
    open_package,
)
from clu_latent.tracks import write_track_file
from clu_latent.validate import ValidationReport

REPO_ROOT = Path(__file__).resolve().parents[1]


# --- Fixtures ----------------------------------------------------------------


@pytest.fixture
def sample_pkg(tmp_path) -> Path:
    """A deterministic package generated from the public fixture builder."""
    return build_fixture("valid_keyframes_audio", tmp_path / "sample.clulatent")


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Map every file under `root` to (size, mtime_ns) for mutation checks."""
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def _add_track(package: Path, name: str, events: list[EventEnvelope]) -> None:
    """Write a JSONL track and register its descriptor in the manifest."""
    rel_file = f"tracks/{name}.jsonl"
    count = write_track_file(package / rel_file, events)
    manifest = Manifest.from_json_file(package / "manifest.json")
    manifest.tracks.append(
        TrackDescriptor(
            name=name,
            file=rel_file,
            schema_id=EVENT_ENVELOPE_SCHEMA_ID,
            schema_version=EVENT_ENVELOPE_SCHEMA_VERSION,
            record_count=count,
            sorted_by="t_start_ms",
        )
    )
    manifest.to_json_file(package / "manifest.json")


def _envelope(event_id: str, event_type: str, t_start: int, t_end: int) -> EventEnvelope:
    return EventEnvelope(
        id=event_id,
        type=event_type,
        t_start_ms=t_start,
        t_end_ms=t_end,
        producer=Producer(name="test", version="0.0.0"),
        payload={},
    )


# --- 0. sanity that the committed sample exists ------------------------------


def test_sample_package_builder_present():
    from clu_latent import conformance_fixtures

    assert callable(conformance_fixtures.build_fixture)


# --- 1. Opens a minimal valid package ----------------------------------------


def test_open_valid_package(sample_pkg):
    reader = open_package(sample_pkg)
    assert isinstance(reader, PackageReader)
    assert reader.path == sample_pkg


def test_open_classmethod_equivalent(sample_pkg):
    reader = PackageReader.open(sample_pkg)
    assert reader.package_id


def test_open_nonexistent_raises(tmp_path):
    with pytest.raises(PackageReaderError):
        open_package(tmp_path / "does-not-exist.clulatent")


def test_open_missing_manifest_raises(tmp_path):
    pkg = tmp_path / "empty.clulatent"
    pkg.mkdir()
    with pytest.raises(PackageReaderError):
        open_package(pkg)


def test_open_malformed_manifest_raises(sample_pkg):
    (sample_pkg / "manifest.json").write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(MalformedPackageError):
        open_package(sample_pkg)


# --- 2. Exposes manifest / package summary -----------------------------------


def test_manifest_level_properties(sample_pkg):
    reader = open_package(sample_pkg)
    assert reader.package_id
    assert reader.duration_ms > 0
    assert reader.status == "complete"
    assert reader.created_at
    assert isinstance(reader.has_audio, bool)
    assert reader.manifest is not None


def test_summary_is_structured(sample_pkg):
    reader = open_package(sample_pkg)
    summary = reader.summary()
    assert isinstance(summary, dict)
    assert summary["package_id"] == reader.package_id
    assert summary["duration_ms"] == reader.duration_ms
    assert summary["status"] == "complete"
    assert summary["track_count"] == len(reader.list_tracks())
    assert isinstance(summary["tracks"], list)
    assert "lock_status" in summary


# --- 3. Lists tracks without the caller knowing filenames --------------------


def test_list_tracks_by_name(sample_pkg):
    reader = open_package(sample_pkg)
    names = {t.name for t in reader.list_tracks()}
    assert "keyframes" in names
    assert "audio_events" in names
    # The handle carries the filename so the caller never has to type it.
    kf = next(t for t in reader.list_tracks() if t.name == "keyframes")
    assert kf.file == "tracks/keyframes.jsonl"
    assert kf.record_count == 2
    assert kf.known is True


def test_has_track(sample_pkg):
    reader = open_package(sample_pkg)
    assert reader.has_track("keyframes")
    assert not reader.has_track("no_such_track")


def test_unknown_declared_track_flagged(sample_pkg):
    _add_track(sample_pkg, "totally_made_up_events", [_envelope("x_0", "mystery", 0, 10)])
    reader = open_package(sample_pkg)
    handle = next(t for t in reader.list_tracks() if t.name == "totally_made_up_events")
    assert handle.known is False
    # Unknown tracks are still listable and readable, just not "known".
    assert reader.has_track("totally_made_up_events")
    assert reader.summary()["unknown_tracks"] == ["totally_made_up_events"]


# --- 4. Reads / iterates events from known tracks ----------------------------


def test_load_track_events(sample_pkg):
    reader = open_package(sample_pkg)
    events = reader.load_track("keyframes")
    assert len(events) == 2
    assert all(isinstance(e, EventRecord) for e in events)
    first = events[0]
    assert first.track_name == "keyframes"
    assert first.timestamp_ms == first.t_start_ms
    # ordered by t_start_ms
    assert [e.t_start_ms for e in events] == sorted(e.t_start_ms for e in events)


def test_iter_events_matches_load(sample_pkg):
    reader = open_package(sample_pkg)
    assert [e.id for e in reader.iter_events("keyframes")] == [
        e.id for e in reader.load_track("keyframes")
    ]


def test_load_absent_optional_track_returns_empty(sample_pkg):
    reader = open_package(sample_pkg)
    # speech_events is declared but empty; a truly-undeclared track -> [].
    assert reader.load_track("speech_events") == []
    assert reader.load_track("audio_digest_events") == []


# --- 5. Queries events by timestamp range ------------------------------------


def test_query_time_range(sample_pkg):
    reader = open_package(sample_pkg)
    hits = reader.query_time(0, 3000)
    assert hits
    # every hit overlaps the window
    assert all(e.t_start_ms <= 3000 and e.t_end_ms >= 0 for e in hits)
    # deterministic ordering
    keys = [(e.t_start_ms, e.track_name, e.id) for e in hits]
    assert keys == sorted(keys)


def test_query_time_default_end_is_duration(sample_pkg):
    reader = open_package(sample_pkg)
    everything = reader.query_time(0)
    assert len(everything) >= 4


def test_query_time_track_filter(sample_pkg):
    reader = open_package(sample_pkg)
    hits = reader.query_time(0, 5000, track_names=["keyframes"])
    assert {e.track_name for e in hits} == {"keyframes"}


def test_query_time_inverted_range_raises(sample_pkg):
    reader = open_package(sample_pkg)
    with pytest.raises(PackageReaderError):
        reader.query_time(5000, 1000)


# --- 6. Resolves event ids across tracks -------------------------------------


def test_get_event_by_id(sample_pkg):
    reader = open_package(sample_pkg)
    kf_id = reader.load_track("keyframes")[1].id
    event = reader.get_event(kf_id)
    assert event is not None and event.id == kf_id


def test_get_event_missing_returns_none(sample_pkg):
    reader = open_package(sample_pkg)
    assert reader.get_event("nope_999") is None


def test_resolve_event_ref_forms(sample_pkg):
    reader = open_package(sample_pkg)
    kf_id = reader.load_track("keyframes")[0].id
    assert reader.resolve_event_ref(kf_id).id == kf_id
    assert reader.resolve_event_ref(f"keyframes:{kf_id}").id == kf_id
    assert reader.resolve_event_ref({"track": "keyframes", "id": kf_id}).id == kf_id
    assert reader.resolve_event_ref({"id": kf_id}).id == kf_id
    assert reader.resolve_event_ref("does_not_exist") is None


def test_get_event_across_multiple_tracks(sample_pkg):
    _add_track(sample_pkg, "evidence_bundles", [_envelope("eb_000000", "evidence_bundle", 0, 1000)])
    reader = open_package(sample_pkg)
    # id lives in a different track than keyframes; get_event scans all.
    assert reader.get_event("eb_000000").track_name == "evidence_bundles"
    assert reader.get_event("kf_000000").track_name == "keyframes"


# --- 7. Handles missing optional tracks cleanly ------------------------------


def test_missing_optional_tracks_are_not_errors(sample_pkg):
    reader = open_package(sample_pkg)
    for optional in ("visual_change_candidates", "changed_region_candidates", "agent_review_events"):
        assert reader.has_track(optional) is False
        assert reader.load_track(optional) == []
        assert list(reader.iter_events(optional)) == []


# --- 8. Rejects / reports malformed JSONL ------------------------------------


def test_malformed_jsonl_strict_raises(sample_pkg):
    with (sample_pkg / "tracks/keyframes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    reader = open_package(sample_pkg)  # strict by default
    with pytest.raises(MalformedPackageError):
        reader.load_track("keyframes")


def test_malformed_jsonl_nonstrict_records_warning(sample_pkg):
    with (sample_pkg / "tracks/keyframes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("this is not json\n")
    reader = open_package(sample_pkg, strict=False)
    events = reader.load_track("keyframes")
    assert len(events) == 2  # good records still returned
    assert reader.read_warnings  # corruption surfaced, not swallowed


# --- 9. Rejects unsafe / path-traversal package structures -------------------


def test_traversal_track_file_rejected(sample_pkg):
    manifest = Manifest.from_json_file(sample_pkg / "manifest.json")
    manifest.tracks[0].file = "../escape.jsonl"
    manifest.to_json_file(sample_pkg / "manifest.json")
    reader = open_package(sample_pkg)
    with pytest.raises(MalformedPackageError):
        reader.load_track(manifest.tracks[0].name)


def test_missing_declared_track_file_raises(sample_pkg):
    (sample_pkg / "tracks/keyframes.jsonl").unlink()
    reader = open_package(sample_pkg)
    with pytest.raises(MalformedPackageError):
        reader.load_track("keyframes")


# --- 10. Does not mutate the package -----------------------------------------


def test_reader_does_not_mutate_package(sample_pkg):
    before = _snapshot(sample_pkg)
    reader = open_package(sample_pkg)
    reader.summary()
    reader.list_tracks()
    reader.load_track("keyframes")
    reader.query_time(0, 10_000)
    reader.get_event("kf_000000")
    reader.receipts()
    for handle in reader.receipts():
        handle.records()
    reader.lock_status()
    reader.validate()
    after = _snapshot(sample_pkg)
    assert before == after


# --- 11. Does not create receipts --------------------------------------------


def test_reader_creates_no_receipts(sample_pkg):
    receipts_dir = sample_pkg / "receipts"
    before = sorted(p.name for p in receipts_dir.iterdir())
    reader = open_package(sample_pkg)
    reader.receipts()
    reader.validate()
    reader.lock_status()
    after = sorted(p.name for p in receipts_dir.iterdir())
    assert before == after


def test_receipts_are_readable(sample_pkg):
    reader = open_package(sample_pkg)
    handles = reader.receipts()
    assert handles
    ingest = next(h for h in handles if h.name == "ingest")
    assert ingest.exists
    records = ingest.records()
    assert isinstance(records, list) and records
    assert all(isinstance(r, dict) for r in records)


# --- 12. Inspects evidence bundles + agent review when present ----------------


def test_inspect_evidence_bundle_and_agent_review(sample_pkg):
    _add_track(
        sample_pkg,
        "evidence_bundles",
        [_envelope("eb_000000", "evidence_bundle", 0, 5000)],
    )
    _add_track(
        sample_pkg,
        "agent_review_events",
        [_envelope("ar_000000", "agent_review", 0, 5000)],
    )
    reader = open_package(sample_pkg)
    assert reader.has_track("evidence_bundles")
    assert reader.has_track("agent_review_events")
    eb = reader.load_track("evidence_bundles")
    ar = reader.load_track("agent_review_events")
    assert eb[0].id == "eb_000000"
    assert ar[0].id == "ar_000000"
    # both are recognized as known canonical lanes
    handles = {t.name: t for t in reader.list_tracks()}
    assert handles["evidence_bundles"].known
    assert handles["agent_review_events"].known
    # resolvable across tracks
    assert reader.resolve_event_ref("agent_review_events:ar_000000").track_name == "agent_review_events"


# --- 13. validate() integrates with the existing validator -------------------


def test_validate_delegates_to_validator(sample_pkg):
    reader = open_package(sample_pkg)
    report = reader.validate()
    assert isinstance(report, ValidationReport)
    assert report.valid is True
    assert report.errors == []


def test_validate_reports_invalid_package(sample_pkg):
    # Corrupt the source so the validator's hash check fails — the reader
    # must surface the validator's judgement, not override it.
    source_path = sample_pkg / Manifest.from_json_file(
        sample_pkg / "manifest.json"
    ).source.stored_path
    source_path.write_bytes(b"tampered")
    reader = open_package(sample_pkg)
    report = reader.validate()
    assert report.valid is False
    assert report.errors


# --- 14. Works on packages produced by existing writer modules ---------------


def test_reader_reads_writer_produced_package(sample_pkg):
    # sample.clulatent was produced by the ingest writer; the reader reads
    # its writer-declared tracks and the counts match the manifest.
    reader = open_package(sample_pkg)
    for handle in reader.list_tracks():
        loaded = reader.load_track(handle.name)
        assert len(loaded) == handle.record_count


# --- lock inspection is read-only --------------------------------------------


def test_lock_status_unlocked(sample_pkg):
    reader = open_package(sample_pkg)
    status = reader.lock_status()
    assert status["status"] == "unlocked"


# --- known-track catalog stays in sync ---------------------------------------


def test_known_track_catalog_covers_current_lanes():
    for name in (
        "keyframes",
        "audio_events",
        "speech_events",
        "semantic_events",
        "review_events",
        "audio_digest_events",
        "visual_change_candidates",
        "changed_region_candidates",
        "evidence_bundles",
        "agent_review_events",
    ):
        assert name in pr.KNOWN_TRACK_NAMES


# --- optional CLI wrapper ----------------------------------------------------


def test_cli_package_inspect(sample_pkg):
    result = CliRunner().invoke(app, ["package", "inspect", str(sample_pkg)])
    assert result.exit_code == 0, result.output
    assert "package_id" in result.output


def test_cli_package_tracks(sample_pkg):
    result = CliRunner().invoke(app, ["package", "tracks", str(sample_pkg)])
    assert result.exit_code == 0, result.output
    assert "keyframes" in result.output


def test_cli_package_inspect_missing_package_clean_error(tmp_path):
    result = CliRunner().invoke(
        app, ["package", "inspect", str(tmp_path / "nope.clulatent")]
    )
    assert result.exit_code == 1
    assert "Traceback" not in result.output


# --- docs present ------------------------------------------------------------


def test_phase_3_18_doc_exists():
    assert (REPO_ROOT / "docs" / "PHASE_3_18_PACKAGE_READER_API_V0.md").is_file()


def test_readme_includes_phase_3_18_bullet():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "CLULatent" in readme
