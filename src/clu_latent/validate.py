"""Package validation: verify a .clulatent package is internally consistent.

This module is read-only — it never modifies manifest.json, track files,
source media, or index/search.sqlite. It only reports problems.

manifest.json and tracks/*.jsonl are canonical; index/search.sqlite is
derived. Validation cross-checks the canonical data against itself and
against the derived index/receipts, but never "fixes" anything — that is
`reindex`'s job (and only for the derived index).

Every manifest-declared, package-relative path (source.stored_path,
tracks[].file, index.file, receipts.file, and per-record payload.path
values) is untrusted and is resolved via
`security.paths.resolve_in_package` before being opened — this is the
strict validation path, so any containment or symlink violation is a
hard failure, not a warning. JSONL tracks are read via
`security.jsonl.iter_jsonl_bounded`, so oversized lines/tracks and
malformed/non-object records are always caught here (validate is the
strict reader; reindex is the tolerant one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .agent_review import AGENT_REVIEW_TRACK_NAME, validate_agent_review_receipts, validate_agent_review_track
from .analysis_lanes import SUPPORTED_ANALYSIS_LANES, validate_analysis_receipts, validate_analysis_track
from .audio_digest import validate_audio_digest_receipts, validate_audio_digest_track
from .audio_digest_writer import AUDIO_DIGEST_TRACK_NAME
from .changed_region import validate_changed_region_receipts, validate_changed_region_track
from .changed_region_writer import CHANGED_REGION_TRACK_NAME
from .constants import (
    AGENT_REVIEW_RECEIPTS_FILE,
    ANALYSIS_RECEIPTS_FILE,
    AUDIO_DIGEST_RECEIPTS_FILE,
    CHANGED_REGION_RECEIPTS_FILE,
    EVENT_DURATION_TOLERANCE_MS,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EVIDENCE_BUNDLE_RECEIPTS_FILE,
    REVIEW_EVENTS_TRACK_NAME,
    VISUAL_CHANGE_RECEIPTS_FILE,
)
from .event import EventEnvelope
from .evidence_bundle import (
    EVIDENCE_BUNDLE_TRACK_NAME,
    KNOWN_RECEIPT_FILES,
    validate_evidence_bundle_receipts,
    validate_evidence_bundle_track,
)
from .hash import read_sha256_sidecar, sha256_file
from .keyframe_retrieval import KEYFRAMES_TRACK_NAME
from .manifest import Manifest
from .paths import normalize_posix
from .review import validate_review_track
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package
from .security.sqlite import SqliteOpenError, open_readonly
from .visual_change import validate_visual_change_receipts, validate_visual_change_track
from .visual_change_writer import VISUAL_CHANGE_TRACK_NAME


@dataclass
class ValidationReport:
    package_path: Path
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: Manifest | None = None


def _check_relative_posix(value: str, field_name: str, errors: list[str]) -> None:
    try:
        normalize_posix(value)
    except ValueError as exc:
        errors.append(f"{field_name} is not a relative POSIX-style path ({value!r}): {exc}")


def _safe_resolve(
    package_path: Path,
    value: str,
    field_name: str,
    errors: list[str],
    *,
    for_write: bool = False,
) -> Path | None:
    """Resolve a manifest-declared relative path, appending an error and
    returning None on any containment/symlink violation instead of
    raising — callers can then `continue`/skip further checks for that
    one path without aborting the whole validation run.
    """
    try:
        return resolve_in_package(package_path, value, field_name=field_name, for_write=for_write)
    except PathSecurityError as exc:
        errors.append(str(exc))
        return None


def validate_package(package_path: Path, *, limits: Limits = DEFAULT_LIMITS) -> ValidationReport:
    """Run every Phase 1 validation check against a .clulatent package."""
    package_path = Path(package_path)
    errors: list[str] = []
    warnings: list[str] = []

    # 1. package path exists
    if not package_path.exists() or not package_path.is_dir():
        errors.append(f"Package path does not exist or is not a directory: {package_path}")
        return ValidationReport(package_path=package_path, valid=False, errors=errors)

    # 2 & 3. manifest.json exists and validates against the Pydantic schema
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        errors.append("manifest.json not found")
        return ValidationReport(package_path=package_path, valid=False, errors=errors)

    try:
        manifest = Manifest.from_json_file(manifest_path, limits=limits)
    except JsonlLimitError as exc:
        errors.append(f"manifest.json exceeds size limits: {exc}")
        return ValidationReport(package_path=package_path, valid=False, errors=errors)
    except json.JSONDecodeError as exc:
        errors.append(f"manifest.json is not valid JSON: {exc}")
        return ValidationReport(package_path=package_path, valid=False, errors=errors)
    except ValidationError as exc:
        errors.append(f"manifest.json failed schema validation: {exc}")
        return ValidationReport(package_path=package_path, valid=False, errors=errors)

    # 4. status is "complete"
    if manifest.status != "complete":
        errors.append(f"manifest status is {manifest.status!r}, expected 'complete'")

    # 14. all internal paths are relative POSIX-style paths (lexical check)
    _check_relative_posix(manifest.source.stored_path, "source.stored_path", errors)
    for track in manifest.tracks:
        _check_relative_posix(track.file, f"tracks[{track.name}].file", errors)
    _check_relative_posix(manifest.media.keyframes.dir, "media.keyframes.dir", errors)
    _check_relative_posix(manifest.index.file, "index.file", errors)
    _check_relative_posix(manifest.receipts.file, "receipts.file", errors)

    # media.keyframes.dir: containment check. Not currently dereferenced
    # by any reader (nothing in this codebase opens the keyframes
    # directory itself), but every manifest-declared path must still be
    # resolved through the same containment/symlink resolver as every
    # other path for consistency — a lexical-only check cannot catch a
    # symlink-based escape. Read-only: existence is not required and
    # nothing is created.
    _safe_resolve(package_path, manifest.media.keyframes.dir, "media.keyframes.dir", errors)

    # 5. source file exists at manifest source.stored_path (path-contained, symlink-safe)
    stored_source_path = _safe_resolve(
        package_path, manifest.source.stored_path, "source.stored_path", errors
    )
    if stored_source_path is not None and not stored_source_path.exists():
        errors.append(f"source file not found: {manifest.source.stored_path}")

    # 6. sources/source.sha256 exists
    sha_sidecar_path = _safe_resolve(
        package_path, "sources/source.sha256", "sources/source.sha256", errors
    )
    if sha_sidecar_path is not None and not sha_sidecar_path.exists():
        errors.append("sources/source.sha256 not found")

    # 7 & 8. source file hash matches manifest and matches the sidecar
    if stored_source_path is not None and stored_source_path.exists():
        actual_digest = sha256_file(stored_source_path)
        if actual_digest != manifest.source.sha256:
            errors.append(
                "source file hash does not match manifest.source.sha256: "
                f"actual={actual_digest} manifest={manifest.source.sha256}"
            )
        if sha_sidecar_path is not None and sha_sidecar_path.exists():
            try:
                sidecar_digest = read_sha256_sidecar(sha_sidecar_path, limits=limits)
            except (OSError, IndexError, JsonlLimitError) as exc:
                errors.append(f"sources/source.sha256 could not be read: {exc}")
            else:
                if actual_digest != sidecar_digest:
                    errors.append(
                        "source file hash does not match sources/source.sha256: "
                        f"actual={actual_digest} sidecar={sidecar_digest}"
                    )

    # 9-13, keyframe path existence: per-track checks
    #
    # Phase 3.16: each track's raw records are also stashed here (by
    # track name) so the changed-region cross-check block below the
    # loop can look up known keyframe/visual-change ids without
    # re-reading either track file a second time, and regardless of
    # which order the tracks happen to appear in manifest.tracks.
    track_raw_records: dict[str, list[dict[str, Any]]] = {}
    for track in manifest.tracks:
        # Schema identity: a track's declared schema_id/schema_version must
        # match the shared event envelope this code actually implements.
        # This catches packages produced by a different schema generation
        # before their records are trusted (important once speech_events /
        # semantic_events start shipping real, evolving schemas).
        if track.schema_id != EVENT_ENVELOPE_SCHEMA_ID:
            errors.append(
                f"{track.name} ({track.file}): schema_id mismatch — "
                f"declared {track.schema_id!r}, expected {EVENT_ENVELOPE_SCHEMA_ID!r}"
            )
        if track.schema_version != EVENT_ENVELOPE_SCHEMA_VERSION:
            errors.append(
                f"{track.name} ({track.file}): schema_version mismatch — "
                f"declared {track.schema_version!r}, expected {EVENT_ENVELOPE_SCHEMA_VERSION!r}"
            )

        track_path = _safe_resolve(package_path, track.file, f"tracks[{track.name}].file", errors)
        if track_path is None:
            continue
        if not track_path.exists():
            errors.append(f"track file listed in manifest is missing: {track.file}")
            continue

        valid_events: list[EventEnvelope] = []
        raw_records: list[dict[str, Any]] = []
        if track_path.stat().st_size > 0:
            try:
                for record in iter_jsonl_bounded(track_path, limits=limits):
                    if record.error is not None:
                        errors.append(f"{track.file}:{record.lineno}: {record.error}")
                        continue
                    raw_records.append(record.data)
                    try:
                        event = EventEnvelope.model_validate(record.data)
                    except ValidationError as exc:
                        errors.append(
                            f"{track.file}:{record.lineno}: does not match the shared "
                            f"event envelope ({exc})"
                        )
                        continue
                    valid_events.append(event)
            except JsonlLimitError as exc:
                errors.append(f"{track.file}: exceeds size limits: {exc}")
                continue

        if len(valid_events) != track.record_count:
            errors.append(
                f"{track.name}: record_count mismatch — manifest says {track.record_count}, "
                f"found {len(valid_events)} valid record(s) in {track.file}"
            )

        if track.sorted_by == "t_start_ms":
            timestamps = [event.t_start_ms for event in valid_events]
            if timestamps != sorted(timestamps):
                errors.append(f"{track.name}: records in {track.file} are not sorted by t_start_ms")

        # Phase 1.7.1: every event's timestamps are bounded by the source
        # media's known duration (plus a small tolerance for codec/stream
        # metadata rounding — see EVENT_DURATION_TOLERANCE_MS). This
        # catches producer bugs like a faster-whisper segment ending well
        # past the ffprobe-reported duration (validate is the strict
        # reader; ingest's transcribe.clamp_segments_to_duration already
        # normalizes this for freshly-produced speech_segment events, but
        # validate must still catch it in any package, including
        # hand-edited or pre-1.7.1 ones). Negative timestamps and
        # t_end_ms < t_start_ms are NOT re-checked here — they are already
        # hard failures above, at `EventEnvelope.model_validate()`, via
        # that model's own `Field(ge=0)` and `_check_time_range` validators.
        duration_limit_ms = manifest.source.duration_ms + EVENT_DURATION_TOLERANCE_MS
        for event in valid_events:
            if event.t_start_ms > duration_limit_ms:
                errors.append(
                    f"{track.name}: record {event.id} ({event.type}) t_start_ms "
                    f"({event.t_start_ms}) exceeds source duration_ms "
                    f"({manifest.source.duration_ms}) + tolerance_ms "
                    f"({EVENT_DURATION_TOLERANCE_MS})"
                )
            if event.t_end_ms > duration_limit_ms:
                errors.append(
                    f"{track.name}: record {event.id} ({event.type}) t_end_ms "
                    f"({event.t_end_ms}) exceeds source duration_ms "
                    f"({manifest.source.duration_ms}) + tolerance_ms "
                    f"({EVENT_DURATION_TOLERANCE_MS})"
                )

            path_value = event.payload.get("path")
            if not isinstance(path_value, str):
                continue
            resolved_payload_path = _safe_resolve(
                package_path, path_value, f"{track.name}[{event.id}].payload.path", errors
            )
            if resolved_payload_path is None:
                continue
            if not resolved_payload_path.exists():
                errors.append(
                    f"{track.name}: record {event.id} payload.path does not exist on disk: "
                    f"{path_value}"
                )

        # Phase 2.0: review_events.jsonl carries additional, type-specific
        # rules (review_state validity, source_event_ids/supersedes_event_ids
        # shape, correction/override payload-patch consistency, session
        # summary count consistency, ...) beyond the generic EventEnvelope
        # shape already checked above — see review.validate_review_track's
        # own docstring for exactly what is (and, deliberately, is not)
        # checked here.
        if track.name == REVIEW_EVENTS_TRACK_NAME:
            review_errors, review_warnings = validate_review_track(valid_events, limits=limits)
            errors.extend(review_errors)
            warnings.extend(review_warnings)

        # Phase 2.9: a supported analysis lane track (Phase 2.5 catalog)
        # carries additional lane-specific rules (Phase 2.6: bounds,
        # confidence range, payload shape/path-safety, no-identity-claim,
        # cross-lane-link payload shape) beyond the generic EventEnvelope
        # shape already checked above. Run against the same raw records
        # already parsed from this track, independent of whether each one
        # also passed the generic envelope check, so a lane-specific
        # violation (e.g. an identity claim or an unsafe payload path) is
        # always reported even for a record that separately fails/passes
        # EventEnvelope validation. Not every supported lane needs to
        # exist in a given package -- this block only runs for tracks
        # actually present in the manifest.
        if track.name in SUPPORTED_ANALYSIS_LANES:
            lane_errors, lane_warnings = validate_analysis_track(
                raw_records, lane=track.name, package_root=package_path, limits=limits
            )
            errors.extend(lane_errors)
            warnings.extend(lane_warnings)

        # Phase 3.7: the audio digest track (Phase 3.4 schema, Phase 3.5
        # writer) carries additional type-dispatch and duplicate-id rules
        # beyond the generic EventEnvelope shape already checked above.
        # Run against the same raw records already parsed from this
        # track, for the same reason the analysis-lane block above does.
        # This block only runs for packages that actually have an audio
        # digest track declared in the manifest -- a package without one
        # is unaffected, and a track file present on disk but not
        # declared in the manifest is not inspected here, following the
        # same convention already used for analysis-lane tracks.
        if track.name == AUDIO_DIGEST_TRACK_NAME:
            digest_errors, digest_warnings = validate_audio_digest_track(
                raw_records, package_root=package_path, limits=limits
            )
            errors.extend(digest_errors)
            warnings.extend(digest_warnings)

        # Phase 3.15: the visual change candidates track (non-semantic
        # pixel-difference evidence between adjacent stored keyframes)
        # carries additional payload-shape, strength, and fixed-caveat
        # rules beyond the generic EventEnvelope shape already checked
        # above. Named `visual_change_candidates` -- deliberately not
        # `visual_change_events`, which is the unrelated Phase 2.15
        # ffmpeg-`scdet` analysis lane already handled by the
        # `SUPPORTED_ANALYSIS_LANES` block above. This block only runs
        # for packages that actually have a visual change track
        # declared in the manifest.
        if track.name == VISUAL_CHANGE_TRACK_NAME:
            visual_change_errors, visual_change_warnings = validate_visual_change_track(
                raw_records, package_root=package_path, limits=limits
            )
            errors.extend(visual_change_errors)
            warnings.extend(visual_change_warnings)

        # Phase 3.17: the evidence bundle track (bounded, package-local
        # collections of already-existing evidence for a time range)
        # carries additional payload-shape, evidence_refs/evidence_counts/
        # coverage/missing_evidence cross-check, and fixed-caveat rules
        # beyond the generic EventEnvelope shape already checked above.
        # Self-contained: an evidence bundle's own payload is checked
        # against itself (not against other tracks' raw records), so this
        # runs inside the per-track loop rather than after it. This block
        # only runs for packages that actually have an evidence bundle
        # track declared in the manifest.
        if track.name == EVIDENCE_BUNDLE_TRACK_NAME:
            evidence_bundle_errors, evidence_bundle_warnings = validate_evidence_bundle_track(
                raw_records,
                package_root=package_path,
                known_receipt_paths=set(KNOWN_RECEIPT_FILES),
                limits=limits,
            )
            errors.extend(evidence_bundle_errors)
            warnings.extend(evidence_bundle_warnings)

        track_raw_records[track.name] = raw_records

    # Phase 3.16: the changed-region candidates track (non-semantic
    # grid-cell localization of the strongest visual difference between
    # each visual-change candidate's linked source/target keyframe
    # images) carries additional payload-shape, region/grid/metrics,
    # strength, change_scope, and fixed-caveat rules beyond the generic
    # EventEnvelope shape already checked in the loop above -- plus two
    # cross-track link checks (`payload.visual_change_id` must exist in
    # the visual_change track, `payload.source_keyframe_id`/
    # `payload.target_keyframe_id` must exist in the keyframes track)
    # that need every track's raw records already collected above, so
    # this runs after the per-track loop rather than inside it. This
    # block only runs for packages that actually have a changed-region
    # track declared in the manifest.
    if CHANGED_REGION_TRACK_NAME in track_raw_records:
        known_visual_change_ids = {
            record.get("id")
            for record in track_raw_records.get(VISUAL_CHANGE_TRACK_NAME, [])
            if isinstance(record, dict)
        }
        known_keyframe_ids = {
            record.get("id")
            for record in track_raw_records.get(KEYFRAMES_TRACK_NAME, [])
            if isinstance(record, dict)
        }
        changed_region_errors, changed_region_warnings = validate_changed_region_track(
            track_raw_records[CHANGED_REGION_TRACK_NAME],
            package_root=package_path,
            known_visual_change_ids=known_visual_change_ids,
            known_keyframe_ids=known_keyframe_ids,
            limits=limits,
        )
        errors.extend(changed_region_errors)
        warnings.extend(changed_region_warnings)

    # Phase 3.17: the agent review track (a bounded, rule-based review of
    # exactly one linked evidence bundle) carries additional payload-shape,
    # allowed-status/label/next-step/confidence, and fixed-caveat rules
    # beyond the generic EventEnvelope shape already checked in the loop
    # above -- plus a cross-track link check (`payload.evidence_bundle_id`
    # must exist in, and this review's time range must fit inside, the
    # linked evidence bundle) that needs every evidence bundle's raw
    # records already collected above, so this runs after the per-track
    # loop rather than inside it. This block only runs for packages that
    # actually have an agent review track declared in the manifest.
    if AGENT_REVIEW_TRACK_NAME in track_raw_records:
        bundles_by_id = {
            record.get("id"): record
            for record in track_raw_records.get(EVIDENCE_BUNDLE_TRACK_NAME, [])
            if isinstance(record, dict) and isinstance(record.get("id"), str)
        }
        agent_review_errors, agent_review_warnings = validate_agent_review_track(
            track_raw_records[AGENT_REVIEW_TRACK_NAME],
            bundles_by_id=bundles_by_id,
            limits=limits,
        )
        errors.extend(agent_review_errors)
        warnings.extend(agent_review_warnings)

    # 15 & 16. index/search.sqlite exists on disk and is a valid, openable SQLite file.
    #
    # manifest.index.status == "derived" and manifest.index.canonical is
    # False are NOT re-checked here: both fields are `Literal` types on
    # `IndexInfo` (see manifest.py), so a manifest.json with any other
    # value already fails Pydantic schema validation above, before this
    # function can even reach here. Re-checking them here would be dead,
    # unreachable code masquerading as coverage.
    index_path = _safe_resolve(package_path, manifest.index.file, "index.file", errors)
    if index_path is not None:
        if not index_path.exists():
            errors.append(f"index file not found: {manifest.index.file}")
        else:
            try:
                conn = open_readonly(index_path)
                conn.close()
            except SqliteOpenError as exc:
                errors.append(f"index file is not a valid, openable SQLite database: {exc}")

    # 17 & 18. receipts/ingest.jsonl exists and is valid JSONL
    receipts_path = _safe_resolve(package_path, manifest.receipts.file, "receipts.file", errors)
    if receipts_path is not None:
        if not receipts_path.exists():
            errors.append(f"receipts file not found: {manifest.receipts.file}")
        elif receipts_path.stat().st_size > 0:
            try:
                for record in iter_jsonl_bounded(receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(f"{manifest.receipts.file}:{record.lineno}: {record.error}")
            except JsonlLimitError as exc:
                errors.append(f"{manifest.receipts.file}: exceeds size limits: {exc}")

    # Phase 2.9: receipts/analyze.jsonl, if present, gets the same
    # bounded JSONL parse as receipts/ingest.jsonl above, plus Phase
    # 2.6-style shape checks per entry (Phase 2.7's receipt contract).
    # This file is optional and unlisted in manifest.json (it is not a
    # `manifest.tracks` entry) -- its absence is never an error: a
    # package with no analysis-lane writes yet, or one written before
    # Phase 2.7 added this file, has no reason to carry it, and that
    # alone must not make the package invalid.
    analyze_receipts_path = _safe_resolve(
        package_path, ANALYSIS_RECEIPTS_FILE, "receipts/analyze.jsonl", errors
    )
    if analyze_receipts_path is not None and analyze_receipts_path.exists():
        if analyze_receipts_path.stat().st_size > 0:
            analyze_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(analyze_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(f"{ANALYSIS_RECEIPTS_FILE}:{record.lineno}: {record.error}")
                        continue
                    analyze_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{ANALYSIS_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                receipt_errors, receipt_warnings = validate_analysis_receipts(
                    analyze_receipt_records, package_root=package_path, limits=limits
                )
                errors.extend(receipt_errors)
                warnings.extend(receipt_warnings)

    # Phase 3.7: receipts/audio_digest.jsonl, if present, gets the same
    # bounded JSONL parse as receipts/analyze.jsonl above, plus a
    # receipt-shape check (Phase 3.5's receipt contract). This file is
    # optional and unlisted in manifest.json -- its absence is never an
    # error: a package with no audio digest writes yet has no reason to
    # carry it, and that alone must not make the package invalid.
    audio_digest_receipts_path = _safe_resolve(
        package_path, AUDIO_DIGEST_RECEIPTS_FILE, "receipts/audio_digest.jsonl", errors
    )
    if audio_digest_receipts_path is not None and audio_digest_receipts_path.exists():
        if audio_digest_receipts_path.stat().st_size > 0:
            audio_digest_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(audio_digest_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(
                            f"{AUDIO_DIGEST_RECEIPTS_FILE}:{record.lineno}: {record.error}"
                        )
                        continue
                    audio_digest_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{AUDIO_DIGEST_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                digest_receipt_errors, digest_receipt_warnings = validate_audio_digest_receipts(
                    audio_digest_receipt_records, limits=limits
                )
                errors.extend(digest_receipt_errors)
                warnings.extend(digest_receipt_warnings)

    # Phase 3.15: receipts/visual_change.jsonl, if present, gets the
    # same bounded JSONL parse as receipts/audio_digest.jsonl above,
    # plus a receipt-shape check. This file is optional and unlisted in
    # manifest.json -- its absence is never an error: a package with no
    # visual-change analyze writes yet has no reason to carry it, and
    # that alone must not make the package invalid.
    visual_change_receipts_path = _safe_resolve(
        package_path, VISUAL_CHANGE_RECEIPTS_FILE, "receipts/visual_change.jsonl", errors
    )
    if visual_change_receipts_path is not None and visual_change_receipts_path.exists():
        if visual_change_receipts_path.stat().st_size > 0:
            visual_change_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(visual_change_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(
                            f"{VISUAL_CHANGE_RECEIPTS_FILE}:{record.lineno}: {record.error}"
                        )
                        continue
                    visual_change_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{VISUAL_CHANGE_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                vc_receipt_errors, vc_receipt_warnings = validate_visual_change_receipts(
                    visual_change_receipt_records, limits=limits
                )
                errors.extend(vc_receipt_errors)
                warnings.extend(vc_receipt_warnings)

    # Phase 3.16: receipts/changed_region.jsonl, if present, gets the
    # same bounded JSONL parse as receipts/visual_change.jsonl above,
    # plus a receipt-shape check. This file is optional and unlisted in
    # manifest.json -- its absence is never an error: a package with no
    # changed-region analyze writes yet has no reason to carry it, and
    # that alone must not make the package invalid.
    changed_region_receipts_path = _safe_resolve(
        package_path, CHANGED_REGION_RECEIPTS_FILE, "receipts/changed_region.jsonl", errors
    )
    if changed_region_receipts_path is not None and changed_region_receipts_path.exists():
        if changed_region_receipts_path.stat().st_size > 0:
            changed_region_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(changed_region_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(
                            f"{CHANGED_REGION_RECEIPTS_FILE}:{record.lineno}: {record.error}"
                        )
                        continue
                    changed_region_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{CHANGED_REGION_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                cr_receipt_errors, cr_receipt_warnings = validate_changed_region_receipts(
                    changed_region_receipt_records, limits=limits
                )
                errors.extend(cr_receipt_errors)
                warnings.extend(cr_receipt_warnings)

    # Phase 3.17: receipts/evidence_bundle.jsonl, if present, gets the
    # same bounded JSONL parse as receipts/changed_region.jsonl above,
    # plus a receipt-shape check. This file is optional and unlisted in
    # manifest.json -- its absence is never an error: a package with no
    # evidence bundle build writes yet has no reason to carry it, and
    # that alone must not make the package invalid.
    evidence_bundle_receipts_path = _safe_resolve(
        package_path, EVIDENCE_BUNDLE_RECEIPTS_FILE, "receipts/evidence_bundle.jsonl", errors
    )
    if evidence_bundle_receipts_path is not None and evidence_bundle_receipts_path.exists():
        if evidence_bundle_receipts_path.stat().st_size > 0:
            evidence_bundle_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(evidence_bundle_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(
                            f"{EVIDENCE_BUNDLE_RECEIPTS_FILE}:{record.lineno}: {record.error}"
                        )
                        continue
                    evidence_bundle_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{EVIDENCE_BUNDLE_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                eb_receipt_errors, eb_receipt_warnings = validate_evidence_bundle_receipts(
                    evidence_bundle_receipt_records, limits=limits
                )
                errors.extend(eb_receipt_errors)
                warnings.extend(eb_receipt_warnings)

    # Phase 3.17: receipts/agent_review.jsonl, if present, gets the same
    # bounded JSONL parse as receipts/evidence_bundle.jsonl above, plus a
    # receipt-shape check. This file is optional and unlisted in
    # manifest.json -- its absence is never an error: a package with no
    # agent review run writes yet has no reason to carry it, and that
    # alone must not make the package invalid.
    agent_review_receipts_path = _safe_resolve(
        package_path, AGENT_REVIEW_RECEIPTS_FILE, "receipts/agent_review.jsonl", errors
    )
    if agent_review_receipts_path is not None and agent_review_receipts_path.exists():
        if agent_review_receipts_path.stat().st_size > 0:
            agent_review_receipt_records: list[Any] = []
            try:
                for record in iter_jsonl_bounded(agent_review_receipts_path, limits=limits):
                    if record.error is not None:
                        errors.append(
                            f"{AGENT_REVIEW_RECEIPTS_FILE}:{record.lineno}: {record.error}"
                        )
                        continue
                    agent_review_receipt_records.append(record.data)
            except JsonlLimitError as exc:
                errors.append(f"{AGENT_REVIEW_RECEIPTS_FILE}: exceeds size limits: {exc}")
            else:
                ar_receipt_errors, ar_receipt_warnings = validate_agent_review_receipts(
                    agent_review_receipt_records, limits=limits
                )
                errors.extend(ar_receipt_errors)
                warnings.extend(ar_receipt_warnings)

    return ValidationReport(
        package_path=package_path,
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        manifest=manifest,
    )
