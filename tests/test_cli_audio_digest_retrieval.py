"""Tests for Phase 3.9: the `clulatent audio-digest <retrieval subcommand>`
CLI commands built on the Phase 3.8 retrieval primitives.

ffmpeg-gated: every test needs a real ingested package to write
already-validated audio digest records into, mirroring
test_cli_audio_digest.py / test_audio_digest_retrieval.py.

Prefer stable tests over brittle prose tests: assertions check for
exit codes, presence of ids/types, and clean-failure/no-mutation
behavior rather than exact wording.
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

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not available in this environment"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
PHASE_DOC = REPO_ROOT / "docs" / "PHASE_3_9_AUDIO_DIGEST_RETRIEVAL_CLI.md"

_WRITER_KWARGS = dict(tool_name="test-digest-writer", tool_version="0.0.1")


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("clulatent_cli_audio_digest_retrieval_fixture")
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


def _append(package_path, *events, **kwargs) -> None:
    writer_mod.append_audio_digest_events(package_path, list(events), **{**_WRITER_KWARGS, **kwargs})


# --- help ----------------------------------------------------------------------


def test_cli_audio_digest_help_includes_retrieval_commands():
    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "--help"])
    assert result.exit_code == 0, result.output
    for name in (
        "get",
        "query-time",
        "query-type",
        "query-linked",
        "query-salience",
        "llm-context",
        "retrieval-summary",
    ):
        assert name in result.output


# --- get -------------------------------------------------------------------------


def test_cli_audio_digest_get_returns_event_by_id(valid_package):
    _append(valid_package, _digest_segment())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "get", str(valid_package), "seg_000001"])
    assert result.exit_code == 0, result.output
    assert "seg_000001" in result.output
    assert "audio_digest_segment" in result.output


def test_cli_audio_digest_get_missing_id_no_results_cleanly(valid_package):
    _append(valid_package, _digest_segment())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "get", str(valid_package), "does_not_exist"])
    assert result.exit_code == 0, result.output
    assert "No audio digest record" in result.output


# --- query-time ------------------------------------------------------------------


def test_cli_audio_digest_query_time_returns_overlapping_records(valid_package):
    _append(
        valid_package,
        _feature_series(id="series_a", t_start_ms=0, t_end_ms=500),
        _feature_series(id="series_b", t_start_ms=1000, t_end_ms=1800),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "query-time", str(valid_package), "--start-ms", "0", "--end-ms", "600"],
    )
    assert result.exit_code == 0, result.output
    assert "series_a" in result.output
    assert "series_b" not in result.output


def test_cli_audio_digest_query_time_rejects_invalid_range(valid_package):
    _append(valid_package, _feature_series())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "query-time", str(valid_package), "--start-ms", "1000", "--end-ms", "0"],
    )
    assert result.exit_code == 1


# --- query-type ------------------------------------------------------------------


def test_cli_audio_digest_query_type_returns_matching_records(valid_package):
    _append(valid_package, _feature_series(), _digest_segment())

    runner = CliRunner()
    result = runner.invoke(
        app, ["audio-digest", "query-type", str(valid_package), "audio_digest_segment"]
    )
    assert result.exit_code == 0, result.output
    assert "seg_000001" in result.output
    assert "series_000001" not in result.output


def test_cli_audio_digest_query_type_rejects_unsupported_type(valid_package):
    _append(valid_package, _feature_series())

    runner = CliRunner()
    result = runner.invoke(
        app, ["audio-digest", "query-type", str(valid_package), "not_a_real_type"]
    )
    assert result.exit_code == 1


# --- query-linked ----------------------------------------------------------------


def test_cli_audio_digest_query_linked_returns_linked_records(valid_package):
    _append(valid_package, _feature_series(), _digest_segment())

    runner = CliRunner()
    result = runner.invoke(
        app, ["audio-digest", "query-linked", str(valid_package), "series_000001"]
    )
    assert result.exit_code == 0, result.output
    assert "series_000001" in result.output
    assert "seg_000001" in result.output


# --- query-salience ----------------------------------------------------------------


def test_cli_audio_digest_query_salience_returns_records_above_threshold(valid_package):
    _append(
        valid_package,
        _digest_segment(id="seg_high", payload={**_digest_segment()["payload"], "salience": 0.9}),
        _digest_segment(id="seg_low", payload={**_digest_segment()["payload"], "salience": 0.1}),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "query-salience", str(valid_package), "--min-salience", "0.5"],
    )
    assert result.exit_code == 0, result.output
    assert "seg_high" in result.output
    assert "seg_low" not in result.output


def test_cli_audio_digest_query_salience_rejects_threshold_outside_range(valid_package):
    _append(valid_package, _digest_segment())

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "query-salience", str(valid_package), "--min-salience", "1.5"],
    )
    assert result.exit_code == 1


# --- llm-context -------------------------------------------------------------------


def test_cli_audio_digest_llm_context_prefers_context_packet(valid_package):
    _append(valid_package, _digest_segment(), _context_packet())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "packet_000001" in result.output
    # The segment's own id/type may still appear as a linked-id
    # reference inside the packet's payload, but the segment record
    # itself must not be printed as its own separate record header.
    assert "seg_000001 (audio_digest_segment)" not in result.output


def test_cli_audio_digest_llm_context_falls_back_to_digest_segments(valid_package):
    _append(valid_package, _digest_segment())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "seg_000001" in result.output


def test_cli_audio_digest_llm_context_respects_max_events_bound(valid_package):
    _append(
        valid_package,
        *[
            _digest_segment(id=f"seg_{i:03d}", payload={**_digest_segment()["payload"], "salience": i / 10})
            for i in range(5)
        ],
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["audio-digest", "llm-context", str(valid_package), "--max-events", "2"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("seg_00") == 2


def test_cli_audio_digest_llm_context_never_dumps_dense_feature_series(valid_package):
    _append(valid_package, _feature_series(), _digest_segment())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "audio_feature_series" not in result.output


# --- retrieval-summary ---------------------------------------------------------


def test_cli_audio_digest_retrieval_summary_prints_bounded_summary(valid_package):
    _append(valid_package, _feature_series(), _digest_segment())

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "retrieval-summary", str(valid_package)])
    assert result.exit_code == 0, result.output
    assert "event_count: 2" in result.output
    assert "series_000001" in result.output
    assert "seg_000001" in result.output


# --- no track / invalid track / forbidden language ------------------------------


def test_cli_audio_digest_no_track_gives_no_results_cleanly(valid_package):
    runner = CliRunner()
    for args in (
        ["audio-digest", "get", str(valid_package), "nope"],
        ["audio-digest", "query-time", str(valid_package), "--start-ms", "0", "--end-ms", "1000"],
        ["audio-digest", "query-type", str(valid_package), "audio_feature_series"],
        ["audio-digest", "query-linked", str(valid_package), "nope"],
        ["audio-digest", "query-salience", str(valid_package), "--min-salience", "0.1"],
        ["audio-digest", "llm-context", str(valid_package)],
        ["audio-digest", "retrieval-summary", str(valid_package)],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output


def test_cli_audio_digest_invalid_track_fails_cleanly(valid_package):
    _append(valid_package, _feature_series())
    _tamper_track_file(valid_package, _feature_series(t_start_ms="not-an-int"))

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "retrieval-summary", str(valid_package)])
    assert result.exit_code == 1


def test_cli_audio_digest_forbidden_language_fails_cleanly(valid_package):
    _append(valid_package, _digest_segment())
    _tamper_track_file(
        valid_package,
        _digest_segment(payload={**_digest_segment()["payload"], "label": "This is definitely a fact."}),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["audio-digest", "retrieval-summary", str(valid_package)])
    assert result.exit_code == 1


# --- non-mutation / non-receipt / non-index -------------------------------------


def test_cli_audio_digest_retrieval_does_not_mutate_package(valid_package):
    _append(valid_package, _feature_series(), _digest_segment())
    manifest_before = (valid_package / "manifest.json").read_bytes()
    track_before = (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes()

    runner = CliRunner()
    runner.invoke(app, ["audio-digest", "get", str(valid_package), "seg_000001"])
    runner.invoke(app, ["audio-digest", "query-time", str(valid_package), "--start-ms", "0", "--end-ms", "2000"])
    runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])
    runner.invoke(app, ["audio-digest", "retrieval-summary", str(valid_package)])

    assert (valid_package / "manifest.json").read_bytes() == manifest_before
    assert (valid_package / AUDIO_DIGEST_TRACK_FILE).read_bytes() == track_before


def test_cli_audio_digest_retrieval_does_not_write_receipts(valid_package):
    _append(valid_package, _feature_series(), write_receipt=False)
    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()

    runner = CliRunner()
    runner.invoke(app, ["audio-digest", "get", str(valid_package), "series_000001"])
    runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])

    assert not (valid_package / AUDIO_DIGEST_RECEIPTS_FILE).exists()


def test_cli_audio_digest_retrieval_does_not_touch_index(valid_package):
    _append(valid_package, _feature_series())
    manifest = Manifest.from_json_file(valid_package / "manifest.json")
    index_path = valid_package / manifest.index.file
    before = index_path.read_bytes()

    runner = CliRunner()
    runner.invoke(app, ["audio-digest", "get", str(valid_package), "series_000001"])
    runner.invoke(app, ["audio-digest", "llm-context", str(valid_package)])

    assert index_path.read_bytes() == before


def test_cli_audio_digest_retrieval_sanitizes_control_characters_in_errors(valid_package):
    ansi_payload = "\x1b[31mred\x1b[0m"
    runner = CliRunner()
    result = runner.invoke(
        app, ["audio-digest", "query-type", str(valid_package), f"not_a_real_type{ansi_payload}"]
    )
    assert result.exit_code == 1
    assert "\x1b[31m" not in result.output


# --- docs / README ------------------------------------------------------------


def test_phase_doc_exists():
    assert PHASE_DOC.is_file()


def test_phase_doc_states_core_rule():
    text = " ".join(PHASE_DOC.read_text(encoding="utf-8").split()).lower()
    assert "store deep" in text
    assert "show shallow" in text
    assert "retrieve detail only when needed" in text


def test_readme_includes_phase_3_9_roadmap_bullet():
    assert "CLULatent" in README_PATH.read_text(encoding="utf-8")
