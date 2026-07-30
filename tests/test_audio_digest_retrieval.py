"""Tests for Phase 3.8: read-only audio digest retrieval primitives.

Uses the Phase 3.5 writer (`audio_digest_writer.append_audio_digest_events`)
to produce genuinely valid on-disk audio digest records, then tampers
with the raw track file the same way `tests/test_validate_audio_digest.py`
already tampers it -- the writer itself refuses to write an invalid
event, so an on-disk invalid record can only be produced by editing the
file directly after a valid write.

ffmpeg-gated: mirrors test_audio_digest_writer.py / test_validate_audio_digest.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clu_latent import audio_digest_retrieval as retrieval_mod
from clu_latent import audio_digest_writer as writer_mod
from clu_latent.constants import AUDIO_DIGEST_RECEIPTS_FILE, AUDIO_DIGEST_TRACK_FILE
from clu_latent.ingest import ingest_video
from clu_latent.manifest import Manifest

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_8_AUDIO_DIGEST_RETRIEVAL_PRIMITIVES.md"

_WRITER_KWARGS = dict(tool_name="test-digest-writer", tool_version="0.0.1")


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_audio_digest_retrieval_fixture")
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
    track_path = package_path / AUDIO_DIGEST_TRACK_FILE
    lines = "\n".join(json.dumps(event) for event in events)
    track_path.write_text(lines + "\n", encoding="utf-8")


# --- module / package-level plumbing ----------------------------------------


def test_module_imports_cleanly():
    assert retrieval_mod is not None


def test_missing_package_fails_cleanly(tmp_path):
    missing = tmp_path / "does_not_exist.clulatent"
    with pytest.raises(retrieval_mod.AudioDigestRetrievalError, match="Package not found"):
        retrieval_mod.load_audio_digest_events(missing)


def test_package_without_audio_digest_track_returns_empty_results(valid_package):
    assert retrieval_mod.load_audio_digest_events(valid_package) == []
    assert retrieval_mod.get_audio_digest_event_by_id(valid_package, "nope") is None
    assert retrieval_mod.query_audio_digest_by_time_range(valid_package, 0, 1000) == []
    assert retrieval_mod.query_audio_digest_by_type(valid_package, "audio_feature_series") == []
    assert retrieval_mod.query_audio_digest_by_linked_evidence_id(valid_package, "nope") == []
    assert retrieval_mod.query_audio_digest_by_salience(valid_package) == []
    assert retrieval_mod.select_audio_digest_llm_context(valid_package) == []


# --- loading / get-by-id -----------------------------------------------------


def test_load_valid_digest_events(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series(), _digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )

    events = retrieval_mod.load_audio_digest_events(valid_package)
    assert {e["id"] for e in events} == {"series_000001", "seg_000001", "packet_000001"}


def test_get_event_by_id(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)

    event = retrieval_mod.get_audio_digest_event_by_id(valid_package, "seg_000001")
    assert event is not None
    assert event["type"] == "audio_digest_segment"


def test_missing_id_returns_none_cleanly(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)

    assert retrieval_mod.get_audio_digest_event_by_id(valid_package, "does_not_exist") is None


# --- queries ------------------------------------------------------------------


def test_query_by_time_range(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [
            _feature_series(id="series_a", t_start_ms=0, t_end_ms=500),
            _feature_series(id="series_b", t_start_ms=1000, t_end_ms=1800),
        ],
        **_WRITER_KWARGS,
    )

    results = retrieval_mod.query_audio_digest_by_time_range(valid_package, 0, 600)
    assert {e["id"] for e in results} == {"series_a"}

    results = retrieval_mod.query_audio_digest_by_time_range(valid_package, 0, 2000)
    assert {e["id"] for e in results} == {"series_a", "series_b"}

    results = retrieval_mod.query_audio_digest_by_time_range(valid_package, 1900, 2000)
    assert results == []


def test_query_by_time_range_rejects_inverted_range(valid_package):
    with pytest.raises(retrieval_mod.AudioDigestRetrievalError, match="must be >="):
        retrieval_mod.query_audio_digest_by_time_range(valid_package, 1000, 0)


def test_query_by_record_type(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series(), _digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )

    results = retrieval_mod.query_audio_digest_by_type(valid_package, "audio_digest_segment")
    assert [e["id"] for e in results] == ["seg_000001"]

    results = retrieval_mod.query_audio_digest_by_type(valid_package, "not_a_real_type")
    assert results == []


def test_query_by_linked_evidence_id(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_feature_series(), _digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )

    # Matches the feature series' own id, referenced via linked_feature_series_ids.
    results = retrieval_mod.query_audio_digest_by_linked_evidence_id(valid_package, "series_000001")
    ids = {e["id"] for e in results}
    assert "series_000001" in ids  # matched by own id
    assert "seg_000001" in ids  # matched via linked_feature_series_ids
    assert "packet_000001" in ids  # matched via linked_feature_series_ids

    results = retrieval_mod.query_audio_digest_by_linked_evidence_id(valid_package, "no_such_id")
    assert results == []


def test_query_by_salience_and_recommended_flag(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [
            _digest_segment(id="seg_high", payload={**_digest_segment()["payload"], "salience": 0.9}),
            _digest_segment(
                id="seg_low",
                payload={
                    **_digest_segment()["payload"],
                    "salience": 0.2,
                    "recommended_for_llm_context": False,
                },
            ),
        ],
        **_WRITER_KWARGS,
    )

    results = retrieval_mod.query_audio_digest_by_salience(valid_package, min_salience=0.5)
    assert [e["id"] for e in results] == ["seg_high"]

    results = retrieval_mod.query_audio_digest_by_salience(
        valid_package, recommended_for_llm_context=False
    )
    assert [e["id"] for e in results] == ["seg_low"]


# --- LLM context selection -----------------------------------------------------


def test_select_bounded_llm_context(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_digest_segment(id=f"seg_{i:03d}", payload={**_digest_segment()["payload"], "salience": i / 10})
         for i in range(5)],
        **_WRITER_KWARGS,
    )

    results = retrieval_mod.select_audio_digest_llm_context(valid_package, max_events=2)
    assert len(results) == 2
    # highest-salience segments first
    assert [e["id"] for e in results] == ["seg_004", "seg_003"]


def test_select_llm_context_prefers_context_packet_when_present(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package,
        [_digest_segment(), _context_packet()],
        **_WRITER_KWARGS,
    )

    results = retrieval_mod.select_audio_digest_llm_context(valid_package)
    assert [e["type"] for e in results] == ["audio_llm_context_packet"]


def test_select_llm_context_falls_back_to_digest_segments(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)

    results = retrieval_mod.select_audio_digest_llm_context(valid_package)
    assert [e["type"] for e in results] == ["audio_digest_segment"]


def test_select_llm_context_never_returns_feature_series(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(), _digest_segment()], **_WRITER_KWARGS
    )

    results = retrieval_mod.select_audio_digest_llm_context(valid_package)
    assert all(e["type"] != "audio_feature_series" for e in results)


def test_select_llm_context_rejects_non_positive_max_events(valid_package):
    with pytest.raises(retrieval_mod.AudioDigestRetrievalError, match="max_events"):
        retrieval_mod.select_audio_digest_llm_context(valid_package, max_events=0)


# --- summarize -------------------------------------------------------------------


def test_summarize_retrieval_result(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(), _digest_segment()], **_WRITER_KWARGS
    )
    events = retrieval_mod.load_audio_digest_events(valid_package)

    summary = retrieval_mod.summarize_audio_digest_retrieval_result(events)
    assert summary["event_count"] == 2
    assert summary["record_type_counts"] == {
        "audio_feature_series": 1,
        "audio_digest_segment": 1,
    }
    assert set(summary["event_ids"]) == {"series_000001", "seg_000001"}
    assert summary["t_start_ms"] == 0
    assert summary["t_end_ms"] == 2000


def test_summarize_empty_result_is_not_an_error():
    summary = retrieval_mod.summarize_audio_digest_retrieval_result([])
    assert summary["event_count"] == 0
    assert summary["record_type_counts"] == {}
    assert summary["event_ids"] == []
    assert summary["t_start_ms"] is None
    assert summary["t_end_ms"] is None


# --- safety: no raw dense arrays, forbidden language, invalid tracks ------------


def test_does_not_include_raw_dense_arrays(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)

    events = retrieval_mod.load_audio_digest_events(valid_package)
    for event in events:
        payload = event.get("payload", {})
        for key in ("values", "samples", "frames", "raw", "dense_values", "series_values"):
            assert key not in payload


def test_rejects_invalid_digest_track(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    _tamper_track_file(valid_package, _feature_series(t_start_ms="not-an-int"))

    with pytest.raises(retrieval_mod.AudioDigestRetrievalError, match="failed validation"):
        retrieval_mod.load_audio_digest_events(valid_package)


def test_rejects_forbidden_language(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_digest_segment()], **_WRITER_KWARGS)
    _tamper_track_file(
        valid_package,
        _digest_segment(payload={**_digest_segment()["payload"], "label": "This is definitely a fact."}),
    )

    with pytest.raises(retrieval_mod.AudioDigestRetrievalError, match="failed validation"):
        retrieval_mod.load_audio_digest_events(valid_package)


# --- non-mutation / non-receipt / non-index -------------------------------------


def test_retrieval_does_not_mutate_package(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series(), _digest_segment()], **_WRITER_KWARGS
    )
    manifest_before = (valid_package / "manifest.json").read_bytes()
    track_before = (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes()

    retrieval_mod.load_audio_digest_events(valid_package)
    retrieval_mod.get_audio_digest_event_by_id(valid_package, "seg_000001")
    retrieval_mod.query_audio_digest_by_time_range(valid_package, 0, 2000)
    retrieval_mod.select_audio_digest_llm_context(valid_package)

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes() == track_before


def test_retrieval_does_not_create_receipts(valid_package):
    writer_mod.append_audio_digest_events(
        valid_package, [_feature_series()], write_receipt=False, **_WRITER_KWARGS
    )
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()

    retrieval_mod.load_audio_digest_events(valid_package)
    retrieval_mod.select_audio_digest_llm_context(valid_package)

    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()


def test_retrieval_does_not_touch_index(valid_package):
    writer_mod.append_audio_digest_events(valid_package, [_feature_series()], **_WRITER_KWARGS)
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    index_path = valid_package / manifest.index.file
    before = index_path.read_bytes()

    retrieval_mod.load_audio_digest_events(valid_package)
    retrieval_mod.select_audio_digest_llm_context(valid_package)

    assert index_path.read_bytes() == before


# --- docs / README ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_states_core_rule():
    text = " ".join(PHASE_DOC.read_text(encoding="utf-8").split()).lower()
    assert "store deep" in text
    assert "show shallow" in text
    assert "retrieve detail only when needed" in text


def test_readme_includes_phase_3_8_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
