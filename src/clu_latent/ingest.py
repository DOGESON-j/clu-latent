"""Ingest orchestration: video file -> .clulatent package.

Ingest is atomic: everything is built inside a temporary directory next
to the requested output path (via `security.temp.staged_package_dir`),
and only moved into place with `security.temp.commit_package` after
every step succeeds. If anything fails, the temp directory is removed
and a clear IngestError is raised — the final output path is never left
in a partial or fake-complete state. A failure receipt is still written
next to the requested output path so a failed run leaves an audit
trail even though its temp package is discarded.

The whole run is additionally guarded end-to-end by
`security.operation_lock.ingest_target_lock` (Phase 1.7.5), so two
concurrent `clulatent ingest` invocations targeting the same
`output_path` can never race at the final commit step — the second one
fails cleanly instead of silently overwriting the first.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import ffmpeg_tools, tracks as tracks_mod
from .constants import (
    AUDIO_EVENTS_TRACK_FILE,
    INDEX_DIR,
    INGEST_RECEIPTS_FILE,
    KEYFRAME_INTERVAL_MS,
    KEYFRAME_METHOD,
    KEYFRAMES_DIR,
    KEYFRAMES_TRACK_FILE,
    RECEIPTS_DIR,
    SEARCH_INDEX_FILE,
    SEMANTIC_EVENTS_TRACK_FILE,
    SOURCE_SHA256_FILENAME,
    SOURCES_DIR,
    SPEECH_EVENTS_TRACK_FILE,
    TOOL_NAME,
    TOOL_VERSION,
    TRACKS_DIR,
    WHISPER_DEFAULT_MODEL,
)
from .event import EventEnvelope
from .hash import sha256_file, write_sha256_sidecar
from .index import build_search_index
from .manifest import (
    IndexInfo,
    Manifest,
    MediaInfo,
    MediaKeyframesInfo,
    ReceiptsInfo,
    SourceInfo,
    TrackDescriptor,
)
from .paths import to_posix_relative
from .receipts import ReceiptLog
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, ingest_target_lock
from .security.subprocess import resolve_tool
from .security.temp import PackageFinalizeError, commit_package, staged_package_dir


class IngestError(RuntimeError):
    """Raised for any ingest failure that should be shown to the user."""


@dataclass
class IngestResult:
    manifest: Manifest
    package_path: Path


def ingest_video(
    video_path: Path,
    output_path: Path,
    *,
    force: bool = False,
    force_stale_lock: bool = False,
    enable_vad: bool = False,
    enable_transcription: bool = False,
    whisper_model: str = WHISPER_DEFAULT_MODEL,
    whisper_model_path: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> IngestResult:
    """Ingest `video_path` into a `.clulatent` package at `output_path`.

    `enable_vad`/`enable_transcription` are opt-in only — both default
    to False, so a plain ingest never imports torch/silero-vad/
    faster-whisper, never loads a model, and never attempts a network
    download. Enabling them is the only way those optional ML
    dependencies are ever touched.

    Guarded end-to-end by `security.operation_lock.ingest_target_lock`
    so two concurrent ingests can never race to build/commit the same
    `output_path`; `force_stale_lock` clears a lock left behind by a
    crashed/killed prior ingest (only if confirmed stale — see that
    module).
    """
    video_path = Path(video_path)
    output_path = Path(output_path)

    if not video_path.exists() or not video_path.is_file():
        raise IngestError(f"Input video does not exist or is not a file: {video_path}")

    source_size = video_path.stat().st_size
    if source_size > limits.max_source_file_bytes:
        raise IngestError(
            f"Input video exceeds max_source_file_bytes "
            f"({source_size} > {limits.max_source_file_bytes}): {video_path}"
        )

    if output_path.exists() and not force:
        raise IngestError(
            f"Output path already exists: {output_path}. Use --force to overwrite it."
        )

    try:
        ffmpeg_tools.check_tools_available()
    except ffmpeg_tools.FfmpegNotFoundError as exc:
        raise IngestError(str(exc)) from exc

    receipts = ReceiptLog()
    try:
        with ingest_target_lock(
            output_path, operation="ingest", force_stale=force_stale_lock, limits=limits
        ):
            with staged_package_dir(output_path) as temp_dir:
                manifest = _build_package(
                    video_path,
                    temp_dir,
                    receipts,
                    enable_vad=enable_vad,
                    enable_transcription=enable_transcription,
                    whisper_model=whisper_model,
                    whisper_model_path=whisper_model_path,
                    limits=limits,
                )
                try:
                    commit_package(temp_dir, output_path, force=force)
                except PackageFinalizeError as exc:
                    raise IngestError(str(exc)) from exc
    except OperationLockError as exc:
        # Lock contention/staleness is a pre-flight refusal, same as the
        # "output path already exists" check above — no work was
        # attempted, so no failure receipt is written for it either.
        raise IngestError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - we deliberately convert everything to IngestError
        receipts.add(
            operation="ingest",
            status="failure",
            source_path=str(video_path),
            output_path=str(output_path),
            errors=[str(exc)],
            limits_snapshot=asdict(limits),
        )
        _write_failure_receipt(output_path, receipts)
        if isinstance(exc, IngestError):
            raise
        raise IngestError(f"Ingest failed: {exc}") from exc

    return IngestResult(manifest=manifest, package_path=output_path)


def _write_failure_receipt(output_path: Path, receipts: ReceiptLog) -> None:
    """Persist receipts for a failed ingest next to the discarded temp package.

    The temp package itself is always removed on failure, so this is the
    only durable record of what a failed ingest attempted and why —
    written as a sibling of `output_path` rather than inside it, since
    `output_path` was never (or no longer) written to.
    """
    failure_path = output_path.parent / f"{output_path.name}.failure-receipt.jsonl"
    try:
        receipts.write(failure_path)
    except OSError:
        pass


def _build_package(
    video_path: Path,
    pkg: Path,
    receipts: ReceiptLog,
    *,
    enable_vad: bool = False,
    enable_transcription: bool = False,
    whisper_model: str = WHISPER_DEFAULT_MODEL,
    whisper_model_path: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> Manifest:
    sources_dir = pkg / SOURCES_DIR
    keyframes_dir = pkg / KEYFRAMES_DIR
    tracks_dir = pkg / TRACKS_DIR
    index_dir = pkg / INDEX_DIR
    receipts_dir = pkg / RECEIPTS_DIR
    for d in (sources_dir, keyframes_dir, tracks_dir, index_dir, receipts_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 1. Copy source media into sources/source.<ext>
    extension = video_path.suffix.lower() or ""
    stored_filename = f"source{extension}"
    stored_path = sources_dir / stored_filename
    shutil.copy2(video_path, stored_path)
    receipts.add(
        operation="copy_source",
        status="success",
        source_path=str(video_path),
        output_path=to_posix_relative(stored_path, pkg),
        files_created=[to_posix_relative(stored_path, pkg)],
    )

    # 2. Hash the copied source and write the sidecar
    digest = sha256_file(stored_path)
    sha256_path = sources_dir / SOURCE_SHA256_FILENAME
    write_sha256_sidecar(sha256_path, digest, stored_filename)
    receipts.add(
        operation="hash_source",
        status="success",
        source_path=to_posix_relative(stored_path, pkg),
        output_path=to_posix_relative(sha256_path, pkg),
        source_hash=digest,
        files_created=[to_posix_relative(sha256_path, pkg)],
    )

    # 3. Probe metadata via ffprobe
    ffprobe_path = resolve_tool("ffprobe")
    try:
        metadata, probe_result = ffmpeg_tools.probe_video(stored_path, limits=limits)
    except ffmpeg_tools.FfmpegExecutionError as exc:
        receipts.add(
            operation="probe_source",
            status="failure",
            source_path=to_posix_relative(stored_path, pkg),
            ffprobe_command_success=False,
            errors=[str(exc)],
            tool_path=ffprobe_path,
            timed_out=exc.result.timed_out,
            stdout_truncated=exc.result.stdout_truncated,
            stderr_truncated=exc.result.stderr_truncated,
            stderr_tail=exc.result.stderr_tail,
        )
        raise
    receipts.add(
        operation="probe_source",
        status="success",
        source_path=to_posix_relative(stored_path, pkg),
        ffprobe_command_success=True,
        tool_path=ffprobe_path,
        timed_out=probe_result.timed_out,
        stdout_truncated=probe_result.stdout_truncated,
        stderr_truncated=probe_result.stderr_truncated,
    )

    if metadata.duration_ms > limits.max_media_duration_ms:
        receipts.add(
            operation="check_duration",
            status="failure",
            source_path=to_posix_relative(stored_path, pkg),
            errors=[
                f"source duration exceeds max_media_duration_ms "
                f"({metadata.duration_ms} > {limits.max_media_duration_ms})"
            ],
        )
        raise IngestError(
            f"Source duration exceeds max_media_duration_ms "
            f"({metadata.duration_ms} > {limits.max_media_duration_ms})"
        )

    container_format = extension.lstrip(".") or "unknown"
    ffmpeg_version = ffmpeg_tools.get_tool_version("ffmpeg")
    ffprobe_version = ffmpeg_tools.get_tool_version("ffprobe")

    # 4. Extract one keyframe per second
    ffmpeg_path = resolve_tool("ffmpeg")
    try:
        frame_paths, extract_result = ffmpeg_tools.extract_keyframes(
            stored_path, keyframes_dir, KEYFRAME_INTERVAL_MS, limits=limits
        )
    except ffmpeg_tools.FfmpegExecutionError as exc:
        receipts.add(
            operation="extract_keyframes",
            status="failure",
            source_path=to_posix_relative(stored_path, pkg),
            ffmpeg_command_success=False,
            errors=[str(exc)],
            tool_path=ffmpeg_path,
            timed_out=exc.result.timed_out,
            stdout_truncated=exc.result.stdout_truncated,
            stderr_truncated=exc.result.stderr_truncated,
            stderr_tail=exc.result.stderr_tail,
        )
        raise
    receipts.add(
        operation="extract_keyframes",
        status="success",
        source_path=to_posix_relative(stored_path, pkg),
        output_path=to_posix_relative(keyframes_dir, pkg),
        ffmpeg_command_success=True,
        files_created=[to_posix_relative(p, pkg) for p in frame_paths],
        tool_path=ffmpeg_path,
        timed_out=extract_result.timed_out,
        stdout_truncated=extract_result.stdout_truncated,
        stderr_truncated=extract_result.stderr_truncated,
    )

    # 5. Build the keyframes track
    keyframe_events: list[EventEnvelope] = []
    for index, frame_path in enumerate(frame_paths):
        t_start_ms = index * KEYFRAME_INTERVAL_MS
        if metadata.duration_ms > t_start_ms:
            t_end_ms = min(t_start_ms + KEYFRAME_INTERVAL_MS, metadata.duration_ms)
        else:
            t_end_ms = t_start_ms + KEYFRAME_INTERVAL_MS
        keyframe_events.append(
            tracks_mod.make_keyframe_event(
                frame_index=index,
                t_start_ms=t_start_ms,
                t_end_ms=t_end_ms,
                image_relpath=to_posix_relative(frame_path, pkg),
                width=metadata.width,
                height=metadata.height,
                producer_version=ffmpeg_version,
            )
        )
    keyframes_track_path = pkg / "tracks" / "keyframes.jsonl"
    keyframe_count = tracks_mod.write_track_file(keyframes_track_path, keyframe_events)

    # 6. Build the audio_events track: audio-present marker (existing) plus
    # ffmpeg silencedetect silence/non_silent_audio spans (Phase 1.7A).
    # Non-ML — always run for any source with an audio stream, no opt-in
    # flag needed.
    audio_events: list[EventEnvelope] = []
    if metadata.has_audio:
        audio_events.append(
            tracks_mod.make_audio_present_event(
                t_end_ms=metadata.duration_ms, producer_version=ffprobe_version
            )
        )
        try:
            silence_intervals, silence_result = ffmpeg_tools.detect_silence(
                stored_path, metadata.duration_ms, limits=limits
            )
        except ffmpeg_tools.FfmpegExecutionError as exc:
            receipts.add(
                operation="detect_silence",
                status="failure",
                source_path=to_posix_relative(stored_path, pkg),
                ffmpeg_command_success=False,
                errors=[str(exc)],
                tool_path=ffmpeg_path,
                timed_out=exc.result.timed_out,
                stdout_truncated=exc.result.stdout_truncated,
                stderr_truncated=exc.result.stderr_truncated,
                stderr_tail=exc.result.stderr_tail,
            )
            raise
        receipts.add(
            operation="detect_silence",
            status="success",
            source_path=to_posix_relative(stored_path, pkg),
            ffmpeg_command_success=True,
            tool_path=ffmpeg_path,
            timed_out=silence_result.timed_out,
            stdout_truncated=silence_result.stdout_truncated,
            stderr_truncated=silence_result.stderr_truncated,
        )
        non_silent_intervals = ffmpeg_tools.complement_intervals(
            silence_intervals, metadata.duration_ms
        )
        for index, (start_ms, end_ms) in enumerate(silence_intervals):
            audio_events.append(
                tracks_mod.make_silence_event(
                    index=index,
                    t_start_ms=start_ms,
                    t_end_ms=end_ms,
                    producer_version=ffmpeg_version,
                )
            )
        for index, (start_ms, end_ms) in enumerate(non_silent_intervals):
            audio_events.append(
                tracks_mod.make_non_silent_audio_event(
                    index=index,
                    t_start_ms=start_ms,
                    t_end_ms=end_ms,
                    producer_version=ffmpeg_version,
                )
            )
    audio_track_path = pkg / "tracks" / "audio_events.jsonl"
    audio_count = tracks_mod.write_track_file(audio_track_path, audio_events)

    # 7. Build the speech_events track: Silero VAD speech_activity (Phase
    # 1.7B, --vad) and/or faster-whisper speech_segment (Phase 1.7C,
    # --transcribe). Both are ML and strictly opt-in — nothing here runs,
    # and no ML dependency is even imported for use, unless the caller
    # explicitly requested it. semantic_events remains empty (out of scope).
    speech_events: list[EventEnvelope] = []
    if metadata.has_audio and (enable_vad or enable_transcription):
        with tempfile.TemporaryDirectory(prefix="clulatent-audio-") as scratch_dir:
            # Extracted outside `pkg` deliberately: commit_package() does a
            # blanket rename of the whole staged directory, so scratch
            # audio must never live under `pkg` or it would ship inside
            # the final .clulatent package.
            wav_path = Path(scratch_dir) / "audio.wav"
            try:
                wav_result = ffmpeg_tools.extract_audio_wav(stored_path, wav_path, limits=limits)
            except ffmpeg_tools.FfmpegExecutionError as exc:
                receipts.add(
                    operation="extract_audio_wav",
                    status="failure",
                    source_path=to_posix_relative(stored_path, pkg),
                    ffmpeg_command_success=False,
                    errors=[str(exc)],
                    tool_path=ffmpeg_path,
                    timed_out=exc.result.timed_out,
                    stdout_truncated=exc.result.stdout_truncated,
                    stderr_truncated=exc.result.stderr_truncated,
                    stderr_tail=exc.result.stderr_tail,
                )
                raise
            receipts.add(
                operation="extract_audio_wav",
                status="success",
                source_path=to_posix_relative(stored_path, pkg),
                ffmpeg_command_success=True,
                tool_path=ffmpeg_path,
                timed_out=wav_result.timed_out,
                stdout_truncated=wav_result.stdout_truncated,
                stderr_truncated=wav_result.stderr_truncated,
            )

            if enable_vad:
                # Imported here, not at module top, so a plain ingest never
                # imports silero-vad/torch (the import happens inside
                # detect_speech_activity only when --vad is passed).
                from . import vad as vad_mod

                try:
                    activity_segments = vad_mod.detect_speech_activity(wav_path)
                except vad_mod.VadUnavailableError as exc:
                    receipts.add(operation="vad_detect", status="failure", errors=[str(exc)])
                    raise IngestError(str(exc)) from exc
                vad_version = vad_mod.get_engine_version()
                for index, seg in enumerate(activity_segments):
                    speech_events.append(
                        tracks_mod.make_speech_activity_event(
                            index=index,
                            t_start_ms=seg.t_start_ms,
                            t_end_ms=seg.t_end_ms,
                            producer_version=vad_version,
                        )
                    )
                receipts.add(
                    operation="vad_detect",
                    status="success",
                    source_path=to_posix_relative(stored_path, pkg),
                )

            if enable_transcription:
                # Imported here, not at module top, so a plain ingest never
                # imports faster-whisper (the import happens inside
                # transcribe_audio only when --transcribe is passed).
                from . import transcribe as transcribe_mod

                try:
                    transcript_segments = transcribe_mod.transcribe_audio(
                        wav_path, model_name=whisper_model, model_path=whisper_model_path
                    )
                except transcribe_mod.WhisperUnavailableError as exc:
                    receipts.add(operation="transcribe_audio", status="failure", errors=[str(exc)])
                    raise IngestError(str(exc)) from exc
                whisper_version = transcribe_mod.get_engine_version()

                # Phase 1.7.1: faster-whisper's own timestamps are
                # untrusted model output — normalize against the
                # ffprobe-known source duration before any segment
                # becomes a canonical speech_segment event.
                accepted_segments, duration_warnings = transcribe_mod.clamp_segments_to_duration(
                    transcript_segments, metadata.duration_ms
                )
                for index, seg in enumerate(accepted_segments):
                    speech_events.append(
                        tracks_mod.make_speech_segment_event(
                            index=index,
                            t_start_ms=seg.t_start_ms,
                            t_end_ms=seg.t_end_ms,
                            text=seg.text,
                            language=seg.language,
                            producer_version=whisper_version,
                            confidence=seg.confidence,
                        )
                    )
                receipts.add(
                    operation="transcribe_audio",
                    status="success",
                    source_path=to_posix_relative(stored_path, pkg),
                    warnings=duration_warnings or None,
                )

    speech_track_path = pkg / "tracks" / "speech_events.jsonl"
    semantic_track_path = pkg / "tracks" / "semantic_events.jsonl"
    speech_count = tracks_mod.write_track_file(speech_track_path, speech_events)
    semantic_count = tracks_mod.write_track_file(semantic_track_path, [])

    receipts.add(
        operation="write_tracks",
        status="success",
        files_created=[
            to_posix_relative(keyframes_track_path, pkg),
            to_posix_relative(audio_track_path, pkg),
            to_posix_relative(speech_track_path, pkg),
            to_posix_relative(semantic_track_path, pkg),
        ],
    )

    # 8. Build the derived search index
    search_index_path = pkg / "index" / "search.sqlite"
    build_search_index(
        search_index_path,
        {
            "keyframes": keyframe_events,
            "audio_events": audio_events,
            "speech_events": speech_events,
            "semantic_events": [],
        },
    )
    receipts.add(
        operation="build_index",
        status="success",
        output_path=to_posix_relative(search_index_path, pkg),
        files_created=[to_posix_relative(search_index_path, pkg)],
    )

    # 9. Assemble the manifest
    package_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()

    manifest = Manifest(
        package_id=package_id,
        created_at=created_at,
        status="complete",
        tool={"name": TOOL_NAME, "version": TOOL_VERSION},
        source=SourceInfo(
            filename=video_path.name,
            stored_path=to_posix_relative(stored_path, pkg),
            sha256=digest,
            duration_ms=metadata.duration_ms,
            container_format=container_format,
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            video_codec=metadata.video_codec,
            audio_codec=metadata.audio_codec,
            bitrate=metadata.bitrate,
            has_audio=metadata.has_audio,
        ),
        tracks=[
            TrackDescriptor(
                name="keyframes",
                file=KEYFRAMES_TRACK_FILE,
                schema_id="clulatent.track.event_envelope",
                schema_version="0.1.0",
                record_count=keyframe_count,
                sorted_by="t_start_ms",
            ),
            TrackDescriptor(
                name="audio_events",
                file=AUDIO_EVENTS_TRACK_FILE,
                schema_id="clulatent.track.event_envelope",
                schema_version="0.1.0",
                record_count=audio_count,
                sorted_by="t_start_ms",
            ),
            TrackDescriptor(
                name="speech_events",
                file=SPEECH_EVENTS_TRACK_FILE,
                schema_id="clulatent.track.event_envelope",
                schema_version="0.1.0",
                record_count=speech_count,
                sorted_by="t_start_ms",
            ),
            TrackDescriptor(
                name="semantic_events",
                file=SEMANTIC_EVENTS_TRACK_FILE,
                schema_id="clulatent.track.event_envelope",
                schema_version="0.1.0",
                record_count=semantic_count,
                sorted_by="t_start_ms",
            ),
        ],
        media=MediaInfo(
            keyframes=MediaKeyframesInfo(
                dir=KEYFRAMES_DIR,
                method=KEYFRAME_METHOD,
                interval_ms=KEYFRAME_INTERVAL_MS,
                count=len(frame_paths),
            )
        ),
        index=IndexInfo(file=SEARCH_INDEX_FILE, status="derived", canonical=False),
        receipts=ReceiptsInfo(file=INGEST_RECEIPTS_FILE),
    )

    receipts.add(
        operation="write_manifest",
        status="success",
        output_path="manifest.json",
        files_created=["manifest.json"],
    )
    receipts.add(
        operation="ingest_summary",
        status="success",
        source_path=str(video_path),
        output_path=str(pkg),
        source_hash=digest,
        limits_snapshot=asdict(limits),
    )

    receipts_path = pkg / "receipts" / "ingest.jsonl"
    receipts.write(receipts_path)

    manifest_path = pkg / "manifest.json"
    manifest.to_json_file(manifest_path)

    return manifest
