"""Central constants for the CLULatent Phase 1 package format.

Keeping these in one place avoids magic strings/numbers drifting between
manifest.py, ingest.py, tracks.py, etc.
"""

from __future__ import annotations

from .version import software_version

CLULATENT_VERSION = "0.1.0"
"""Schema/format version of the .clulatent package itself.

This is intentionally separate from TOOL_VERSION: a CLI patch release
does not necessarily change the package shape, and a package schema
change does not necessarily require a CLI version bump.
"""

TOOL_NAME = "clulatent"
TOOL_VERSION = software_version()

EVENT_ENVELOPE_SCHEMA_ID = "clulatent.track.event_envelope"
EVENT_ENVELOPE_SCHEMA_VERSION = "0.1.0"

KEYFRAME_INTERVAL_MS = 1000
KEYFRAME_METHOD = "ffmpeg_interval"

# FFmpeg `silencedetect` filter thresholds (Phase 1.7A). Non-ML: this is
# plain signal-level analysis, always run during ingest (no opt-in flag).
SILENCE_NOISE_DB = -30.0
SILENCE_MIN_DURATION_S = 0.5

# Silero VAD (Phase 1.7B) / faster-whisper (Phase 1.7C) defaults. Both
# features are opt-in only (see cli.py --vad/--transcribe flags) — these
# defaults only take effect once a user explicitly enables them.
WHISPER_DEFAULT_MODEL = "base"

# Phase 1.7.1: tolerance (in ms) used when comparing any track event's
# t_start_ms/t_end_ms against the source media's known duration_ms.
# Not zero, because ffprobe's reported container duration and a
# decoder/model's own internal notion of "duration" (frame boundaries,
# stream metadata, codec rounding) can legitimately differ by a small
# amount — without *some* tolerance, a technically-valid event ending a
# few milliseconds past the probed duration would be wrongly rejected.
# Not large, because the goal is to catch real integrity bugs (e.g. a
# faster-whisper segment ending tens of seconds past a 30-second
# source), not to silently accept them. Used identically by both
# ingest (clamping/skipping out-of-bounds transcription segments before
# they become canonical track data) and validate (failing a package
# that still contains a truly out-of-bounds event).
EVENT_DURATION_TOLERANCE_MS = 1000

# Relative, POSIX-style paths inside every package. These are written into
# manifest.json and must never be OS-specific or absolute.
SOURCES_DIR = "sources"
MEDIA_DIR = "media"
KEYFRAMES_DIR = f"{MEDIA_DIR}/keyframes"
TRACKS_DIR = "tracks"
INDEX_DIR = "index"
RECEIPTS_DIR = "receipts"

SOURCE_SHA256_FILENAME = "source.sha256"

KEYFRAMES_TRACK_FILE = f"{TRACKS_DIR}/keyframes.jsonl"
AUDIO_EVENTS_TRACK_FILE = f"{TRACKS_DIR}/audio_events.jsonl"
SPEECH_EVENTS_TRACK_FILE = f"{TRACKS_DIR}/speech_events.jsonl"
SEMANTIC_EVENTS_TRACK_FILE = f"{TRACKS_DIR}/semantic_events.jsonl"
# Phase 2.0: human review / correction track (Phase 1.9 design, implemented
# here). Canonical once present, using the same shared EventEnvelope as
# every other track — see review.py.
REVIEW_EVENTS_TRACK_NAME = "review_events"
REVIEW_EVENTS_TRACK_FILE = f"{TRACKS_DIR}/review_events.jsonl"

SEARCH_INDEX_FILE = f"{INDEX_DIR}/search.sqlite"
INGEST_RECEIPTS_FILE = f"{RECEIPTS_DIR}/ingest.jsonl"
# Phase 2.7: adapter run receipts for analysis-lane writes (see
# analysis_writer.py). Named "analyze.jsonl" per the Phase 2.5 design
# doc's "receipts/analyze.jsonl (name TBD)" placeholder. `lock.py`
# already globs `receipts/*.jsonl` generically, so this file is
# lock-covered without any lock.py change.
ANALYSIS_RECEIPTS_FILE = f"{RECEIPTS_DIR}/analyze.jsonl"

# Phase 3.5: audio digest record writes (see audio_digest_writer.py).
# One shared, mixed-type track holds all three Phase 3.4 digest record
# types (`audio_feature_series`, `audio_digest_segment`,
# `audio_llm_context_packet`), matching how
# `audio_digest.validate_audio_digest_track` already validates a
# mixed-type iterable in one pass. `lock.py` already globs
# `tracks/*.jsonl`/`receipts/*.jsonl` generically, so both files are
# lock-covered without any lock.py change.
AUDIO_DIGEST_TRACK_FILE = f"{TRACKS_DIR}/audio_digest_events.jsonl"
AUDIO_DIGEST_RECEIPTS_FILE = f"{RECEIPTS_DIR}/audio_digest.jsonl"

# Phase 3.15: non-semantic visual change evidence lane (see
# visual_change.py / visual_change_writer.py). Named
# "visual_change_candidates" -- deliberately NOT "visual_change_events"
# -- because that track name is already used by the unrelated Phase
# 2.15 ffmpeg-`scdet` analysis lane (`analysis_ffmpeg_visual_change_
# adapter.py`, lane `visual_change_events`, type `visual_change`).
# `lock.py` already globs `tracks/*.jsonl`/`receipts/*.jsonl`
# generically, so both files are lock-covered without any lock.py
# change.
VISUAL_CHANGE_TRACK_FILE = f"{TRACKS_DIR}/visual_change_candidates.jsonl"
VISUAL_CHANGE_RECEIPTS_FILE = f"{RECEIPTS_DIR}/visual_change.jsonl"

# Phase 3.16: non-semantic changed-region evidence lane (see
# changed_region.py / changed_region_writer.py). Builds on the Phase
# 3.15 `visual_change_candidates` track -- for each visual change
# candidate, localizes the strongest grid-cell region of difference
# between its linked source/target keyframe images. `lock.py` already
# globs `tracks/*.jsonl`/`receipts/*.jsonl` generically, so both files
# are lock-covered without any lock.py change.
CHANGED_REGION_TRACK_FILE = f"{TRACKS_DIR}/changed_region_candidates.jsonl"
CHANGED_REGION_RECEIPTS_FILE = f"{RECEIPTS_DIR}/changed_region.jsonl"

# Phase 3.17: evidence bundle + agent review v0 (see evidence_bundle.py /
# evidence_bundle_writer.py / agent_review.py / agent_review_writer.py).
# Evidence bundles collect already-existing package evidence (keyframes,
# visual change candidates, changed-region candidates, audio/speech
# events, audio digest events, review events, analysis lane events,
# receipts, validation status) for a bounded time range into one
# record; agent review v0 is a bounded, rule-based (no external model)
# review layer over one evidence bundle. `lock.py` already globs
# `tracks/*.jsonl`/`receipts/*.jsonl` generically, so all four files
# are lock-covered without any lock.py change.
EVIDENCE_BUNDLE_TRACK_FILE = f"{TRACKS_DIR}/evidence_bundles.jsonl"
EVIDENCE_BUNDLE_RECEIPTS_FILE = f"{RECEIPTS_DIR}/evidence_bundle.jsonl"
AGENT_REVIEW_TRACK_FILE = f"{TRACKS_DIR}/agent_review_events.jsonl"
AGENT_REVIEW_RECEIPTS_FILE = f"{RECEIPTS_DIR}/agent_review.jsonl"

PACKAGE_SUFFIX = ".clulatent"

# Package locking (Phase 1.7.5). Two independent lock kinds:
#   - the integrity lock (LOCK_JSON_FILENAME + LOCK_SHA256_FILENAME): a
#     durable hash manifest a user creates deliberately via `clulatent lock`.
#   - the operation lock (LOCK_OPERATION_FILENAME): a short-lived write
#     guard automatically created/removed around a single write operation.
LOCK_VERSION = "0.1.0"
LOCK_DIR = "lock"
LOCK_JSON_FILENAME = "package.lock.json"
LOCK_SHA256_FILENAME = "package.lock.sha256"
LOCK_OPERATION_FILENAME = "package.operation.lock.json"
# `clulatent ingest` writes into a package that does not exist yet for
# most of its run (see security.temp.staged_package_dir), so it cannot
# use LOCK_OPERATION_FILENAME (which lives inside the target's own
# lock/ dir). Instead its operation lock is a sibling file next to the
# requested output path, e.g. `pkg.clulatent.oplock.json` — the same
# "sibling of a not-yet-existing target" pattern already used by
# ingest's own failure-receipt file.
INGEST_TARGET_LOCK_SUFFIX = ".oplock.json"
