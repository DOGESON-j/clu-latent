"""Central, single-source-of-truth resource limits for CLULatent.

Every bound referenced by security/subprocess.py, security/jsonl.py,
ingest.py, validate.py, and reindex.py comes from here. Nothing should
hardcode a byte count, timeout, or record limit anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    # Source media
    max_source_file_bytes: int = 2 * 1024**3  # 2 GiB
    max_media_duration_ms: int = 2 * 60 * 60 * 1000  # 2 hours

    # Manifest / canonical JSONL tracks
    max_manifest_bytes: int = 1 * 1024**2  # 1 MiB
    max_jsonl_line_bytes: int = 1 * 1024**2  # 1 MiB
    max_records_per_track: int = 1_000_000

    # sources/source.sha256 sidecar (one hex digest + a short filename)
    max_sidecar_bytes: int = 4 * 1024  # 4 KiB

    # FFmpeg / ffprobe subprocess execution
    ffprobe_timeout_s: float = 30.0
    ffmpeg_timeout_s: float = 900.0
    stderr_tail_bytes: int = 64 * 1024  # 64 KiB kept in receipts/errors

    # Hard safety caps on live subprocess output capture. These are not
    # part of the task's suggested default table, but are required to
    # keep `run_tool` itself from buffering unbounded memory while a
    # process is still running (a malicious/broken ffmpeg could spam
    # gigabytes of stdout/stderr before exiting). Chosen generously above
    # stderr_tail_bytes/expected ffprobe JSON size so normal operation is
    # unaffected; documented as an implementation assumption.
    max_stdout_capture_bytes: int = 16 * 1024**2  # 16 MiB
    max_stderr_capture_bytes: int = 8 * 1024**2  # 8 MiB

    # Phase 2.0: review_events.jsonl free-text / label bounds (Phase 1.9
    # rule 8 — "a conservative fixed cap, consistent with the existing
    # Limits philosophy"). Applied by both review.py's factories (at
    # construction time) and validate.py (at validation time), so a
    # hand-edited package cannot bypass the bound just by skipping the
    # factory.
    max_review_label_bytes: int = 256  # reviewer_id, reviewer_label, session_id, override_kind
    max_review_text_bytes: int = 4 * 1024  # reason, note_text
    max_review_payload_bytes: int = 16 * 1024  # corrected_payload / original_payload (serialized)

    # Phase 2.6: analysis lane event bounds (schema primitives only --
    # no adapter runtime exists yet, see analysis_lanes.py). Applied by
    # validate_analysis_event/validate_analysis_track so a hand-edited
    # or future-adapter-written record cannot bypass these bounds just
    # by skipping whatever helper eventually constructs the record.
    max_analysis_id_bytes: int = 256  # event id
    max_analysis_type_bytes: int = 256  # event type
    max_analysis_label_bytes: int = 256  # producer.name/version, relation_type, lane-ish labels
    max_analysis_text_bytes: int = 4 * 1024  # notes, rationale, free text
    max_analysis_payload_bytes: int = 16 * 1024  # payload, serialized


DEFAULT_LIMITS = Limits()
