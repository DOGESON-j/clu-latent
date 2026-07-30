"""Phase 2.6: analysis lane schema primitives.

Schema-only. This module implements no video/audio analysis, no
FFmpeg-based tracker, no OCR runtime, no object detector, and no ML
model dependency. It gives a **future** adapter (Phase 2.5's optional,
never-core FFmpeg/PySceneDetect/OpenCV/Tesseract/WhisperX/... bridges)
the smallest safe shape to check a candidate analysis-lane event
against, plus a documented receipt contract, before that adapter (or a
later phase's writer) ever touches a real package.

Core principle (Phase 2.5, restated here because it governs every
check below): adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable (see `docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`).

Nothing here is wired into `validate.py`, `lock.py`, `tracks.py`, or
any CLI command. No new track file is written by this phase. A future
lane track, once it exists as a real `tracks/<lane>.jsonl` declared in
`manifest.tracks`, is just another canonical track using the existing
shared `EventEnvelope` (`event.py`) -- this module adds no new envelope
container schema, only additional shape/bound checks a raw candidate
record must pass before an adapter proposes writing it.

Why raw dicts, not `EventEnvelope` instances: `EventEnvelope` (a
Pydantic model with lenient, non-strict coercion) would silently accept
a numeric string like `"1000"` for `t_start_ms`. This module is meant
to be the first, strictest gate a future adapter's raw JSON-shaped
output passes through -- before anything is handed to
`EventEnvelope.model_validate()` -- so it checks types explicitly
rather than relying on coercion.

Path fields inside a payload (`path`, or any key ending in `_path`) get
a two-stage check mirroring `security/paths.py`: lexical
(`validate_relative_posix`) always runs; if a `package_root` is
supplied (i.e. a real package exists on disk to check against), the
full filesystem containment + symlink-refusal check
(`resolve_in_package`) also runs. Without a `package_root` (the common
case for an adapter checking output before any package write), only
the lexical stage is possible -- full symlink-escape safety is a
filesystem-time property, not a pure-schema one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import TRACKS_DIR
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix

# --- Lane catalog (Phase 2.5) --------------------------------------------


class AnalysisEventError(ValueError):
    """Raised by `normalize_analysis_lane_name` for an unrecognized lane name.

    `validate_analysis_event`/`validate_analysis_track` never raise this
    (or anything else) -- they return `(errors, warnings)` lists, the
    same non-raising convention `review.validate_review_track` already
    uses, so a caller can collect every problem in one pass instead of
    stopping at the first one.
    """


ANALYSIS_LANE_NAMES: tuple[str, ...] = (
    "scene_events",
    "visual_change_events",
    "motion_events",
    "object_proposal_events",
    "object_tracking_events",
    "ocr_events",
    "audio_energy_events",
    "audio_transient_events",
    "audio_texture_events",
    "audio_signature_events",
    "rhythm_events",
    "music_events",
    "stereo_events",
    "cross_lane_link_events",
)
"""The full Phase 2.5 lane catalog, in the same order named there."""

SUPPORTED_ANALYSIS_LANES = frozenset(ANALYSIS_LANE_NAMES)

CROSS_LANE_LINK_LANE_NAME = "cross_lane_link_events"

# Lanes whose payload conventions center on a proposed or tracked
# object/region (Phase 2.5 lane catalog #4, #5) plus the cross-lane
# link lane (#14), which can associate object-ish events with anything
# else. The Phase 2.6 "no identity claims" rules below are actually
# applied lane-agnostically (see `_check_identity_claims`) -- this set
# exists only for callers/tests that want to name "the lanes this rule
# was written for" explicitly.
OBJECT_OR_LINK_LANES = frozenset(
    {"object_proposal_events", "object_tracking_events", CROSS_LANE_LINK_LANE_NAME}
)

# Payload fields that would assert real-world person identity. Presence
# of any of these anywhere in an analysis-lane payload is always a hard
# error: an automatic adapter must never claim to know who someone is.
# Applied to every lane, not just the object/tracking ones -- "no real-
# person identification" is a whole-schema principle (Phase 2.5).
FORBIDDEN_IDENTITY_FIELDS = frozenset(
    {"person_name", "identity", "face_identity", "biometric_identity"}
)

# Boolean "is this an identity claim" flags Phase 2.5's payload sketches
# already use (object_proposal/object_tracking's `is_identity`,
# cross_lane_link's `is_identity_claim`). When present, an automatic
# adapter must always set these to False -- true identity assertion is
# out of scope for every lane defined here.
IDENTITY_FLAG_FIELDS: tuple[str, ...] = ("is_identity", "is_identity_claim")

# cross_lane_link_events.relation_type must describe an association,
# never a causal claim -- "no claim that correlation proves cause"
# (Phase 2.5 rule 5 / cross_lane_link_events rules).
FORBIDDEN_CAUSAL_RELATION_TYPES = frozenset({"causes", "caused_by", "proves", "confirms"})

_PATH_LIKE_PAYLOAD_KEYS = frozenset({"path"})
_PATH_LIKE_PAYLOAD_SUFFIX = "_path"


def is_supported_analysis_lane(name: Any) -> bool:
    """True if `name` is exactly one of `ANALYSIS_LANE_NAMES`.

    Never raises, never normalizes -- a non-string or unrecognized
    value simply returns False.
    """
    return isinstance(name, str) and name in SUPPORTED_ANALYSIS_LANES


def normalize_analysis_lane_name(name: Any) -> str:
    """Validate `name` is a supported analysis lane, stripped of surrounding whitespace.

    Only strips leading/trailing whitespace -- never rewrites case or
    punctuation, so what gets checked (and returned) is otherwise
    exactly the caller's string, matching `security.paths`'s "never
    silently rewrite" convention. Raises `AnalysisEventError` if the
    result is not one of `ANALYSIS_LANE_NAMES`.
    """
    if not isinstance(name, str):
        raise AnalysisEventError(f"lane name must be a string, got {type(name).__name__}")
    normalized = name.strip()
    if normalized not in SUPPORTED_ANALYSIS_LANES:
        raise AnalysisEventError(
            f"{name!r} is not a supported analysis lane "
            f"(must be one of {sorted(SUPPORTED_ANALYSIS_LANES)})"
        )
    return normalized


# --- Field-level checks (append to an errors list; never raise) ----------


def _check_bounded_string(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool,
    errors: list[str],
) -> None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if required and value == "":
        errors.append(f"{field_name} must not be empty")
        return
    if "\x00" in value:
        errors.append(f"{field_name} must not contain NUL bytes")
        return
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound (got {encoded_len} bytes)")


def _check_bounded_string_list(
    value: Any,
    field_name: str,
    *,
    max_item_bytes: int,
    required: bool,
    errors: list[str],
) -> list[str] | None:
    """Validate `value` is a list of short, non-empty, bounded strings.

    Returns the list if shape-valid (so callers can do further checks,
    e.g. self-reference), otherwise None. Mirrors
    `review._check_id_list`'s reasoning: a bare string/number/dict
    would satisfy a naive truthiness check, so shape is validated
    up front instead of assumed.
    """
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return None
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array of strings, got {type(value).__name__}")
        return None
    if required and not value:
        errors.append(f"{field_name} must not be empty")
        return None
    ok = True
    for item in value:
        before = len(errors)
        _check_bounded_string(
            item, f"{field_name}[]", max_bytes=max_item_bytes, required=True, errors=errors
        )
        if len(errors) != before:
            ok = False
    return value if ok else None


def _check_confidence(value: Any, field_name: str, errors: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field_name} must be a number, got {type(value).__name__}")
        return
    if not (0.0 <= float(value) <= 1.0):
        errors.append(f"{field_name} must be within [0.0, 1.0], got {value!r}")


def _check_identity_claims(payload: dict[str, Any], errors: list[str]) -> None:
    for forbidden_field in FORBIDDEN_IDENTITY_FIELDS:
        if forbidden_field in payload:
            errors.append(
                f"payload.{forbidden_field} is a real-person identity claim, which is never "
                "permitted in an automatic analysis-lane event"
            )
    for flag_field in IDENTITY_FLAG_FIELDS:
        if flag_field not in payload:
            continue
        flag_value = payload[flag_field]
        if flag_value is not False:
            errors.append(
                f"payload.{flag_field} must be false for an automatic adapter "
                f"(got {flag_value!r}); real-person identity claims are out of scope"
            )


def _check_path_like_fields(
    payload: dict[str, Any],
    *,
    package_root: Path | None,
    errors: list[str],
) -> None:
    for key, value in payload.items():
        if not isinstance(value, str):
            continue
        if key not in _PATH_LIKE_PAYLOAD_KEYS and not key.endswith(_PATH_LIKE_PAYLOAD_SUFFIX):
            continue
        field_name = f"payload.{key}"
        try:
            if package_root is not None:
                resolve_in_package(package_root, value, field_name=field_name)
            else:
                validate_relative_posix(value, field_name=field_name)
        except PathSecurityError as exc:
            errors.append(str(exc))


def _check_cross_lane_link_payload(
    payload: dict[str, Any],
    *,
    event_id: Any,
    limits: Limits,
    errors: list[str],
) -> None:
    source_ids = _check_bounded_string_list(
        payload.get("source_event_ids"),
        "payload.source_event_ids",
        max_item_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
    )
    target_ids = _check_bounded_string_list(
        payload.get("target_event_ids"),
        "payload.target_event_ids",
        max_item_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
    )
    if isinstance(event_id, str):
        if source_ids and event_id in source_ids:
            errors.append("payload.source_event_ids must not reference its own event id")
        if target_ids and event_id in target_ids:
            errors.append("payload.target_event_ids must not reference its own event id")

    relation_type = payload.get("relation_type")
    _check_bounded_string(
        relation_type,
        "payload.relation_type",
        max_bytes=limits.max_analysis_label_bytes,
        required=True,
        errors=errors,
    )
    if isinstance(relation_type, str) and relation_type in FORBIDDEN_CAUSAL_RELATION_TYPES:
        errors.append(
            f"payload.relation_type {relation_type!r} asserts causation; cross_lane_link_events "
            "may only claim association (e.g. 'supports', 'co_occurs', 'derived_from', "
            "'conflicts', 'unknown'), never proof of cause"
        )

    if "confidence" in payload:
        _check_confidence(payload.get("confidence"), "payload.confidence", errors)


# --- Public validation API -------------------------------------------------


def validate_analysis_event(
    record: Any,
    *,
    lane: str | None = None,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted analysis-lane event record.

    `record` is a plain JSON-shaped object (e.g. `json.loads()` of one
    JSONL line, or a dict a future adapter is about to propose writing)
    -- not a constructed `EventEnvelope`. Returns `(errors, warnings)`;
    an empty `errors` list means the record is shape-valid. Never
    raises and never mutates `record`.

    Every analysis event must have `id`, `type`, `t_start_ms`,
    `t_end_ms`, `producer`, and `payload` (Phase 2.6 rule). `lane`, if
    given, must be one of `ANALYSIS_LANE_NAMES` and enables lane-
    specific payload rules (currently: `cross_lane_link_events`).
    `package_root`, if given, additionally resolves any path-like
    payload field through `security.paths.resolve_in_package` for full
    filesystem containment + symlink-escape safety; without it, path-
    like fields still get the lexical `validate_relative_posix` check.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if lane is not None:
        try:
            lane = normalize_analysis_lane_name(lane)
        except AnalysisEventError as exc:
            errors.append(str(exc))
            lane = None

    if not isinstance(record, dict):
        errors.append(f"analysis event record must be a JSON object, got {type(record).__name__}")
        return errors, warnings

    event_id = record.get("id")
    _check_bounded_string(
        event_id, "id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )

    event_type = record.get("type")
    _check_bounded_string(
        event_type, "type", max_bytes=limits.max_analysis_type_bytes, required=True, errors=errors
    )

    t_start_ms = record.get("t_start_ms")
    t_start_valid = (
        isinstance(t_start_ms, int) and not isinstance(t_start_ms, bool) and t_start_ms >= 0
    )
    if "t_start_ms" not in record:
        errors.append("t_start_ms is required")
    elif not isinstance(t_start_ms, int) or isinstance(t_start_ms, bool):
        errors.append(f"t_start_ms must be an integer, got {type(t_start_ms).__name__}")
    elif t_start_ms < 0:
        errors.append(f"t_start_ms must be >= 0, got {t_start_ms}")

    t_end_ms = record.get("t_end_ms")
    t_end_valid = isinstance(t_end_ms, int) and not isinstance(t_end_ms, bool) and t_end_ms >= 0
    if "t_end_ms" not in record:
        errors.append("t_end_ms is required")
    elif not isinstance(t_end_ms, int) or isinstance(t_end_ms, bool):
        errors.append(f"t_end_ms must be an integer, got {type(t_end_ms).__name__}")
    elif t_end_ms < 0:
        errors.append(f"t_end_ms must be >= 0, got {t_end_ms}")

    if t_start_valid and t_end_valid and t_end_ms < t_start_ms:
        errors.append(f"t_end_ms ({t_end_ms}) must be >= t_start_ms ({t_start_ms})")

    # Optional duration_ms bound check: if a payload/top-level
    # duration_ms is supplied, it must be consistent with the
    # start/end span it describes (Phase 2.6 rule: "optional
    # duration_ms bound check if duration is supplied").
    duration_ms = record.get("duration_ms")
    if duration_ms is not None:
        if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
            errors.append(f"duration_ms must be a non-negative integer, got {duration_ms!r}")
        elif t_start_valid and t_end_valid and duration_ms != (t_end_ms - t_start_ms):
            errors.append(
                f"duration_ms ({duration_ms}) does not match t_end_ms - t_start_ms "
                f"({t_end_ms - t_start_ms})"
            )

    producer = record.get("producer")
    if "producer" not in record:
        errors.append("producer is required")
    elif not isinstance(producer, dict):
        errors.append(f"producer must be an object, got {type(producer).__name__}")
    else:
        _check_bounded_string(
            producer.get("name"),
            "producer.name",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )
        _check_bounded_string(
            producer.get("version"),
            "producer.version",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )

    _check_confidence(record.get("confidence"), "confidence", errors)

    payload = record.get("payload")
    if "payload" not in record:
        errors.append("payload is required")
        payload = None
    elif not isinstance(payload, dict):
        errors.append(f"payload must be a JSON object, got {type(payload).__name__}")
        payload = None
    else:
        try:
            encoded_len = len(json.dumps(payload).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            errors.append(f"payload is not JSON-serializable: {exc}")
        else:
            if encoded_len > limits.max_analysis_payload_bytes:
                errors.append(
                    f"payload exceeds the {limits.max_analysis_payload_bytes}-byte bound "
                    f"(got {encoded_len} bytes)"
                )

    if isinstance(payload, dict):
        _check_identity_claims(payload, errors)
        _check_path_like_fields(payload, package_root=package_root, errors=errors)

        if lane == CROSS_LANE_LINK_LANE_NAME:
            _check_cross_lane_link_payload(
                payload, event_id=event_id, limits=limits, errors=errors
            )

    return errors, warnings


def validate_analysis_track(
    events: list[Any],
    *,
    lane: str,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a full lane track: every event plus track-wide rules.

    `events` is a list of raw record dicts (see `validate_analysis_event`
    for the shape each one is checked against), all belonging to the
    same declared `lane`. Returns `(errors, warnings)` aggregated across
    every record, each error prefixed with `lane[index]` (and the
    record's `id`, if it has one) so a caller can locate the offending
    line. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    try:
        lane = normalize_analysis_lane_name(lane)
    except AnalysisEventError as exc:
        return [str(exc)], warnings

    seen_ids: set[str] = set()
    for index, record in enumerate(events):
        record_errors, record_warnings = validate_analysis_event(
            record, lane=lane, package_root=package_root, limits=limits
        )
        record_id = record.get("id") if isinstance(record, dict) else None
        prefix = f"{lane}[{index}]" + (f" ({record_id})" if isinstance(record_id, str) else "")
        errors.extend(f"{prefix}: {message}" for message in record_errors)
        warnings.extend(f"{prefix}: {message}" for message in record_warnings)

        if isinstance(record_id, str) and record_id != "":
            if record_id in seen_ids:
                errors.append(f"{prefix}: duplicate analysis event id within {lane}")
            else:
                seen_ids.add(record_id)

    return errors, warnings


# --- Adapter receipt contract (Phase 2.6, documented shape only) ---------


@dataclass
class AnalysisAdapterReceipt:
    """Documented field set for a future adapter run's receipt entry.

    Phase 2.6 adds no adapter runtime and this dataclass writes nothing
    to disk on its own -- it exists so a future adapter (whenever one
    is implemented) has one already-agreed receipt shape to construct
    and hand to a receipts writer, mirroring `receipts.ReceiptLog.add`'s
    existing dict-based, append-only, one-record-per-operation
    convention, instead of inventing an ad hoc shape per adapter. A
    future writer would most likely serialize `to_dict()` as one line
    of a `receipts/analyze.jsonl` (name TBD; see
    `docs/PHASE_2_5_ANALYSIS_LANES_TRACKING_ADAPTER_DESIGN.md`'s
    "Future export receipts" precedent) -- `lock.py` already globs
    `receipts/*.jsonl` generically, so no lock.py change would be
    needed once that file exists.
    """

    adapter_name: str
    tool_name: str
    tool_version: str
    status: str  # "success" | "partial" | "failure"
    output_tracks: list[str] = field(default_factory=list)
    event_counts: dict[str, int] = field(default_factory=dict)
    input_sources: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    model_name: str | None = None
    model_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    skipped_count: int = 0
    clamped_count: int = 0
    bounded_count: int = 0
    failure_details: str | None = None
    environment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "status": self.status,
            "output_tracks": list(self.output_tracks),
            "event_counts": dict(self.event_counts),
            "input_sources": list(self.input_sources),
            "parameters": dict(self.parameters),
            "model_name": self.model_name,
            "model_version": self.model_version,
            "warnings": list(self.warnings),
            "skipped_count": self.skipped_count,
            "clamped_count": self.clamped_count,
            "bounded_count": self.bounded_count,
            "failure_details": self.failure_details,
            "environment": dict(self.environment),
        }


# --- Receipt shape validation (Phase 2.9, package-validation use) --------

_ANALYSIS_RECEIPT_STATUSES = frozenset({"success", "partial", "failure"})

_ANALYSIS_RECEIPT_OUTPUT_TRACK_PATHS = frozenset(
    f"{TRACKS_DIR}/{lane}.jsonl" for lane in ANALYSIS_LANE_NAMES
)


def _check_output_track_reference(value: Any, field_name: str, errors: list[str]) -> None:
    """`value` must be a bare supported lane name or its canonical
    `tracks/<lane>.jsonl` path.

    Both accepted forms are drawn from the same small, fixed whitelist
    (`ANALYSIS_LANE_NAMES` / `_ANALYSIS_RECEIPT_OUTPUT_TRACK_PATHS`), so
    this single membership check also rules out an absolute path, a
    parent-traversal segment, or a symlink-escape attempt on its own:
    none of those strings can ever equal one of the whitelisted forms.
    """
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string, got {type(value).__name__}")
        return
    if value in SUPPORTED_ANALYSIS_LANES or value in _ANALYSIS_RECEIPT_OUTPUT_TRACK_PATHS:
        return
    errors.append(
        f"{field_name} must be a supported analysis lane name or its "
        f"'{TRACKS_DIR}/<lane>.jsonl' path, got {value!r}"
    )


def _check_input_sources(
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    limits: Limits,
    errors: list[str],
) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array of strings, got {type(value).__name__}")
        return
    for index, item in enumerate(value):
        item_field = f"{field_name}[{index}]"
        _check_bounded_string(
            item, item_field, max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )
        if not isinstance(item, str):
            continue
        try:
            if package_root is not None:
                resolve_in_package(package_root, item, field_name=item_field)
            else:
                validate_relative_posix(item, field_name=item_field)
        except PathSecurityError as exc:
            errors.append(str(exc))


def validate_analysis_receipt(
    record: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `receipts/analyze.jsonl` entry.

    Conservative: catches obviously-bad data (wrong types, missing
    required fields, unbounded strings, path-unsafe
    `output_tracks`/`input_sources`) without asserting anything about
    whether the receipt's *claims* are true -- a receipt records what
    an adapter says it did; this only checks the record is shape-valid
    enough to trust as a receipt at all. Bound choices mirror
    `analysis_writer._check_receipt_bounds`, but this function operates
    on an untrusted raw dict as read from disk (not an
    already-constructed `AnalysisAdapterReceipt`), and additionally
    checks `output_tracks`/`input_sources` for path safety -- a check
    the writer itself does not need, since it always constructs those
    fields from paths it already validated internally. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(record, dict):
        errors.append(f"analysis receipt record must be a JSON object, got {type(record).__name__}")
        return errors, warnings

    for field_name in ("adapter_name", "tool_name", "tool_version"):
        _check_bounded_string(
            record.get(field_name),
            field_name,
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )

    if "status" not in record:
        errors.append("status is required")
    elif record.get("status") not in _ANALYSIS_RECEIPT_STATUSES:
        errors.append(
            f"status must be one of {sorted(_ANALYSIS_RECEIPT_STATUSES)}, got {record.get('status')!r}"
        )

    for field_name in ("model_name", "model_version"):
        if record.get(field_name) is not None:
            _check_bounded_string(
                record.get(field_name),
                field_name,
                max_bytes=limits.max_analysis_label_bytes,
                required=False,
                errors=errors,
            )

    if record.get("failure_details") is not None:
        _check_bounded_string(
            record.get("failure_details"),
            "failure_details",
            max_bytes=limits.max_analysis_text_bytes,
            required=False,
            errors=errors,
        )

    output_tracks = record.get("output_tracks")
    if output_tracks is not None:
        if not isinstance(output_tracks, list):
            errors.append(f"output_tracks must be an array, got {type(output_tracks).__name__}")
        else:
            for index, item in enumerate(output_tracks):
                _check_output_track_reference(item, f"output_tracks[{index}]", errors)

    _check_input_sources(
        record.get("input_sources"),
        "input_sources",
        package_root=package_root,
        limits=limits,
        errors=errors,
    )

    warnings_value = record.get("warnings")
    if warnings_value is not None:
        if not isinstance(warnings_value, list):
            errors.append(f"warnings must be an array, got {type(warnings_value).__name__}")
        else:
            for index, item in enumerate(warnings_value):
                _check_bounded_string(
                    item,
                    f"warnings[{index}]",
                    max_bytes=limits.max_analysis_text_bytes,
                    required=True,
                    errors=errors,
                )

    for field_name in ("event_counts", "parameters", "environment"):
        value = record.get(field_name)
        if value is None:
            continue
        if not isinstance(value, dict):
            errors.append(f"{field_name} must be a JSON object, got {type(value).__name__}")
            continue
        try:
            encoded_len = len(json.dumps(value).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            errors.append(f"{field_name} is not JSON-serializable: {exc}")
            continue
        if encoded_len > limits.max_analysis_payload_bytes:
            errors.append(
                f"{field_name} exceeds the {limits.max_analysis_payload_bytes}-byte bound "
                f"(got {encoded_len} bytes)"
            )

    for field_name in ("skipped_count", "clamped_count", "bounded_count"):
        if record.get(field_name) is None:
            continue
        value = record.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{field_name} must be a non-negative integer, got {value!r}")

    return errors, warnings


def validate_analysis_receipts(
    records: list[Any],
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate every entry of a `receipts/analyze.jsonl` batch.

    Returns `(errors, warnings)` aggregated across every record, each
    prefixed with its index so a caller can locate the offending
    entry. Never raises. An empty `records` list is valid -- a package
    with no analysis-lane receipts yet (or one written before Phase
    2.7 existed) is not an error on its own; see
    `docs/PHASE_2_9_ANALYSIS_LANE_VALIDATION_INTEGRATION.md`.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_analysis_receipt(
            record, package_root=package_root, limits=limits
        )
        prefix = f"receipts/analyze.jsonl[{index}]"
        errors.extend(f"{prefix}: {message}" for message in record_errors)
        warnings.extend(f"{prefix}: {message}" for message in record_warnings)
    return errors, warnings
