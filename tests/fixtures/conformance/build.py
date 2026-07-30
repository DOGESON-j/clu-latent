"""Deterministic builder for the Phase 3.19 conformance fixtures.

This module materializes every conformance fixture used by
`tests/test_format_conformance.py` into a target directory. It is the
single source of truth for how each fixture is produced, and it is run
once to materialize the committed fixture directories under
`tests/fixtures/conformance/`.

Each fixture is built from scratch using the real library models
(`Manifest`, `EventEnvelope`) so a "valid" fixture is genuinely valid
(the validator accepts it) and each "invalid" fixture fails for exactly
one, documented reason. No FFmpeg/Pillow/ML/network is used: the
"source" media is a tiny deterministic byte blob (the validator only
checks its existence and SHA-256, never decodes it), and the index is a
real, empty SQLite database.

Nothing here interprets content or asserts meaning; it only lays bytes on
disk in the shapes the format spec (`docs/CLULATENT_FORMAT_SPEC_V0.md`)
describes as accepted or rejected.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable

from clu_latent.constants import (
    AGENT_REVIEW_TRACK_FILE,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
)
from clu_latent.agent_review import AGENT_REVIEW_CAVEATS, AGENT_REVIEW_METHOD

# The tiny, deterministic stand-in for the embedded source media. The
# validator only checks that this file exists and that its SHA-256
# matches the manifest + sidecar -- it never decodes it -- so a fixed
# byte blob is sufficient and keeps fixtures FFmpeg-free.
_SOURCE_BYTES = b"CLULATENT-CONFORMANCE-FIXTURE-SOURCE-v0\n"
_SOURCE_SHA256 = hashlib.sha256(_SOURCE_BYTES).hexdigest()
_DURATION_MS = 5000


def _base_manifest(*, tracks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A minimal, schema-valid manifest dict (status 'complete')."""
    return {
        "clulatent_version": "0.1.0",
        "package_id": "conformance-fixture",
        "created_at": "2026-01-01T00:00:00Z",
        "status": "complete",
        "tool": {"name": "clulatent", "version": "0.1.0"},
        "source": {
            "filename": "source.bin",
            "stored_path": "sources/source.bin",
            "sha256": _SOURCE_SHA256,
            "duration_ms": _DURATION_MS,
            "container_format": "bin",
            "width": 0,
            "height": 0,
            "has_audio": False,
            "storage_mode": "embedded",
            "phase_1_single_source": True,
        },
        "timebase": {"unit": "ms", "type": "integer"},
        "tracks": tracks if tracks is not None else [],
        "media": {
            "keyframes": {
                "dir": "media/keyframes",
                "method": "ffmpeg_interval",
                "interval_ms": 1000,
                "count": 0,
            }
        },
        "index": {"file": "index/search.sqlite", "status": "derived", "canonical": False},
        "receipts": {"file": "receipts/ingest.jsonl"},
    }


def _envelope(
    event_id: str,
    event_type: str,
    t_start: int,
    t_end: int,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "t_start_ms": t_start,
        "t_end_ms": t_end,
        "producer": {"name": "conformance", "version": "0.0.0"},
        "confidence": None,
        "payload": payload if payload is not None else {},
    }


def _track_descriptor(name: str, file: str, count: int) -> dict[str, Any]:
    return {
        "name": name,
        "file": file,
        "schema_id": EVENT_ENVELOPE_SCHEMA_ID,
        "schema_version": EVENT_ENVELOPE_SCHEMA_VERSION,
        "record_count": count,
        "sorted_by": "t_start_ms",
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _write_source(root: Path) -> None:
    src = root / "sources" / "source.bin"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(_SOURCE_BYTES)
    (root / "sources" / "source.sha256").write_text(
        f"{_SOURCE_SHA256}  source.bin\n", encoding="utf-8"
    )


def _write_index(root: Path) -> None:
    index_path = root / "index" / "search.sqlite"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(index_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT, v TEXT)")
        conn.commit()
    finally:
        conn.close()


def _write_receipts(root: Path, *, records: list[dict[str, Any]] | None = None) -> None:
    receipts = records if records is not None else [{"operation": "ingest", "status": "success"}]
    _write_jsonl(root / "receipts" / "ingest.jsonl", receipts)


def _scaffold(root: Path, manifest: dict[str, Any]) -> None:
    """Lay down the required non-manifest members shared by every package."""
    root.mkdir(parents=True, exist_ok=True)
    _write_source(root)
    _write_index(root)
    _write_receipts(root)
    (root / "media" / "keyframes").mkdir(parents=True, exist_ok=True)
    _write_json(root / "manifest.json", manifest)


# --- Valid fixtures ----------------------------------------------------------


def build_valid_minimal(root: Path) -> None:
    """Smallest valid package: no tracks at all."""
    _scaffold(root, _base_manifest(tracks=[]))


def build_valid_keyframes_audio(root: Path) -> None:
    """Valid package with a keyframes track and an audio_events track."""
    manifest = _base_manifest(
        tracks=[
            _track_descriptor("keyframes", "tracks/keyframes.jsonl", 2),
            _track_descriptor("audio_events", "tracks/audio_events.jsonl", 2),
        ]
    )
    _scaffold(root, manifest)
    # Keyframe events reference real (tiny) image files, so the validator's
    # payload.path existence check is satisfied.
    kf_dir = root / "media" / "keyframes"
    kf_dir.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (kf_dir / f"kf_{i:06d}.jpg").write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG SOI/EOI
    _write_jsonl(
        root / "tracks" / "keyframes.jsonl",
        [
            _envelope(
                f"kf_{i:06d}",
                "keyframe",
                i * 1000,
                i * 1000,
                payload={"path": f"media/keyframes/kf_{i:06d}.jpg", "index": i},
            )
            for i in range(2)
        ],
    )
    _write_jsonl(
        root / "tracks" / "audio_events.jsonl",
        [
            _envelope("ae_000000", "silence", 0, 500, payload={"kind": "silence"}),
            _envelope("ae_000001", "silence", 3000, 3500, payload={"kind": "silence"}),
        ],
    )
    # keep manifest keyframe count honest for the media block
    manifest["media"]["keyframes"]["count"] = 2
    _write_json(root / "manifest.json", manifest)


def build_valid_unknown_track(root: Path) -> None:
    """Valid package that also declares a track outside the known catalog.

    The reader must list it (flagged known=False); the validator applies
    no lane-specific trust to it, and — because its records are still
    valid envelopes — the package remains valid.
    """
    manifest = _base_manifest(
        tracks=[_track_descriptor("made_up_lane", "tracks/made_up_lane.jsonl", 1)]
    )
    _scaffold(root, manifest)
    _write_jsonl(
        root / "tracks" / "made_up_lane.jsonl",
        [_envelope("mu_000000", "made_up_event", 0, 100, payload={"note": "unknown lane"})],
    )


# --- Invalid fixtures --------------------------------------------------------


def build_invalid_missing_manifest(root: Path) -> None:
    """A package directory with everything but manifest.json."""
    manifest = _base_manifest(tracks=[])
    _scaffold(root, manifest)
    (root / "manifest.json").unlink()


def build_invalid_malformed_manifest_json(root: Path) -> None:
    """manifest.json that is not valid JSON."""
    _scaffold(root, _base_manifest(tracks=[]))
    (root / "manifest.json").write_text("{ this is not valid json ", encoding="utf-8")


def build_invalid_unsafe_track_path(root: Path) -> None:
    """A declared track whose file escapes the package via traversal."""
    manifest = _base_manifest(
        tracks=[_track_descriptor("keyframes", "../evil.jsonl", 0)]
    )
    _scaffold(root, manifest)


def build_invalid_missing_track_file(root: Path) -> None:
    """A declared track whose JSONL file does not exist on disk."""
    manifest = _base_manifest(
        tracks=[_track_descriptor("keyframes", "tracks/keyframes.jsonl", 1)]
    )
    _scaffold(root, manifest)
    # deliberately do NOT write tracks/keyframes.jsonl


def build_invalid_malformed_jsonl(root: Path) -> None:
    """A declared track file with a corrupt (non-JSON) line."""
    manifest = _base_manifest(
        tracks=[_track_descriptor("keyframes", "tracks/keyframes.jsonl", 1)]
    )
    _scaffold(root, manifest)
    path = root / "tracks" / "keyframes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_envelope("kf_000000", "keyframe", 0, 0)) + "\n")
        handle.write("{ this line is not valid json\n")


def build_invalid_duplicate_event_ids(root: Path) -> None:
    """An analysis lane (scene_events) with two records sharing an id.

    Duplicate-id rejection is lane-specific (not enforced by the generic
    envelope), so this fixture uses an analysis lane to exercise it; the
    two records are otherwise valid, so the *only* failure is the
    duplicate id.
    """
    manifest = _base_manifest(
        tracks=[_track_descriptor("scene_events", "tracks/scene_events.jsonl", 2)]
    )
    _scaffold(root, manifest)
    _write_jsonl(
        root / "tracks" / "scene_events.jsonl",
        [
            _envelope("sc_dup", "scene_event", 0, 100, payload={"note": "first"}),
            _envelope("sc_dup", "scene_event", 200, 300, payload={"note": "second"}),
        ],
    )


def build_invalid_dangling_evidence_reference(root: Path) -> None:
    """An agent_review_events record referencing a non-existent bundle.

    The record's payload is otherwise fully valid; the only failure is
    that `payload.evidence_bundle_id` references an evidence bundle that
    the package does not contain (no evidence_bundles track is declared).
    """
    manifest = _base_manifest(
        tracks=[_track_descriptor("agent_review_events", AGENT_REVIEW_TRACK_FILE, 1)]
    )
    _scaffold(root, manifest)
    payload = {
        "evidence_bundle_id": "eb_does_not_exist",
        "review_status": "insufficient_evidence",
        "findings": [
            {
                "label": "insufficient_evidence",
                "severity": "warning",
                "message": "No evidence of any category was found for this time range.",
                "evidence_ref_ids": [],
            }
        ],
        "evidence_present": [],
        "evidence_missing": [],
        "unsupported_claims": [],
        "recommended_next_step": "collect_more_evidence",
        "confidence": "low",
        "caveats": list(AGENT_REVIEW_CAVEATS),
        "method": AGENT_REVIEW_METHOD,
        "created_by": "conformance-fixture",
    }
    _write_jsonl(
        root / AGENT_REVIEW_TRACK_FILE,
        [_envelope("ar_000000", "agent_review_event", 0, _DURATION_MS, payload=payload)],
    )


def build_invalid_unsafe_receipt_path(root: Path) -> None:
    """A manifest whose primary receipt path escapes the package."""
    manifest = _base_manifest(tracks=[])
    manifest["receipts"]["file"] = "../evil-receipts.jsonl"
    _scaffold(root, manifest)


# --- Registry ----------------------------------------------------------------

VALID_FIXTURES: dict[str, Callable[[Path], None]] = {
    "valid_minimal": build_valid_minimal,
    "valid_keyframes_audio": build_valid_keyframes_audio,
    "valid_unknown_track": build_valid_unknown_track,
}

INVALID_FIXTURES: dict[str, Callable[[Path], None]] = {
    "invalid_missing_manifest": build_invalid_missing_manifest,
    "invalid_malformed_manifest_json": build_invalid_malformed_manifest_json,
    "invalid_unsafe_track_path": build_invalid_unsafe_track_path,
    "invalid_missing_track_file": build_invalid_missing_track_file,
    "invalid_malformed_jsonl": build_invalid_malformed_jsonl,
    "invalid_duplicate_event_ids": build_invalid_duplicate_event_ids,
    "invalid_dangling_evidence_reference": build_invalid_dangling_evidence_reference,
    "invalid_unsafe_receipt_path": build_invalid_unsafe_receipt_path,
}

ALL_FIXTURES: dict[str, Callable[[Path], None]] = {**VALID_FIXTURES, **INVALID_FIXTURES}


def build_fixture(name: str, root: Path) -> Path:
    """Build a single named fixture into `root` (created fresh)."""
    if name not in ALL_FIXTURES:
        raise KeyError(f"unknown conformance fixture: {name!r}")
    if root.exists():
        shutil.rmtree(root)
    ALL_FIXTURES[name](root)
    return root


def build_all(base_dir: Path) -> dict[str, Path]:
    """Build every fixture as `base_dir/<name>.clulatent`. Returns name->path."""
    out: dict[str, Path] = {}
    for name in ALL_FIXTURES:
        out[name] = build_fixture(name, base_dir / f"{name}.clulatent")
    return out


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    built = build_all(here / "packages")
    for fixture_name, fixture_path in built.items():
        print(f"built {fixture_name} -> {fixture_path}")
