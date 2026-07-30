import pytest
from pydantic import ValidationError

from clu_latent.manifest import (
    IndexInfo,
    Manifest,
    MediaInfo,
    MediaKeyframesInfo,
    ReceiptsInfo,
    SourceInfo,
    TrackDescriptor,
)


def _minimal_manifest(**overrides) -> Manifest:
    kwargs = dict(
        package_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-07-07T00:00:00+00:00",
        status="complete",
        source=SourceInfo(
            filename="sample.mp4",
            stored_path="sources/source.mp4",
            sha256="a" * 64,
            duration_ms=5000,
            container_format="mp4",
            width=1920,
            height=1080,
            fps=30.0,
            video_codec="h264",
            audio_codec="aac",
            bitrate=1_000_000,
            has_audio=True,
        ),
        tracks=[
            TrackDescriptor(
                name="keyframes",
                file="tracks/keyframes.jsonl",
                schema_id="clulatent.track.event_envelope",
                schema_version="0.1.0",
                record_count=5,
                sorted_by="t_start_ms",
            )
        ],
        media=MediaInfo(
            keyframes=MediaKeyframesInfo(
                dir="media/keyframes", method="ffmpeg_interval", interval_ms=1000, count=5
            )
        ),
        index=IndexInfo(file="index/search.sqlite"),
        receipts=ReceiptsInfo(file="receipts/ingest.jsonl"),
    )
    kwargs.update(overrides)
    return Manifest(**kwargs)


def test_minimal_manifest_is_valid():
    manifest = _minimal_manifest()
    assert manifest.status == "complete"
    assert manifest.index.canonical is False
    assert manifest.source.phase_1_single_source is True
    assert manifest.source.storage_mode == "embedded"
    assert manifest.timebase.unit == "ms"


def test_manifest_json_roundtrip(tmp_path):
    manifest = _minimal_manifest()
    path = tmp_path / "manifest.json"
    manifest.to_json_file(path)

    loaded = Manifest.from_json_file(path)
    assert loaded == manifest


def test_manifest_rejects_invalid_status():
    with pytest.raises(ValidationError):
        _minimal_manifest(status="not_a_real_status")


def test_manifest_index_canonical_is_always_false():
    manifest = _minimal_manifest()
    assert manifest.index.canonical is False
    with pytest.raises(ValidationError):
        IndexInfo(file="index/search.sqlite", canonical=True)


def test_manifest_rejects_extra_top_level_fields():
    with pytest.raises(ValidationError):
        _minimal_manifest(unexpected_field="nope")


def test_manifest_requires_source():
    kwargs = dict(
        package_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-07-07T00:00:00+00:00",
        status="complete",
        tracks=[],
        media=MediaInfo(
            keyframes=MediaKeyframesInfo(
                dir="media/keyframes", method="ffmpeg_interval", interval_ms=1000, count=0
            )
        ),
        index=IndexInfo(file="index/search.sqlite"),
        receipts=ReceiptsInfo(file="receipts/ingest.jsonl"),
    )
    with pytest.raises(ValidationError):
        Manifest(**kwargs)
