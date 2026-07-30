"""Phase 3.4: audio evidence digest schema primitives.

Validated, code-level shape/bound checks for the three record types
Phase 3.3 (`docs/AUDIO_EVIDENCE_DIGEST_CONTRACT.md`) defines for the
audio evidence digest layer:

  - `audio_feature_series` (Level 1: dense metrics, referenced by
    package-relative path, never inlined)
  - `audio_digest_segment` (Level 3: merged, evidence-linked spans)
  - `audio_llm_context_packet` (Level 5: bounded, ranked, caveat-
    bearing packets safe to hand to an LLM by default)

Core principle (Phase 3.3, restated here because it governs every
check below): **store deep, show shallow, retrieve detail only when
needed.**

What this module is NOT:

  - No audio adapter. No FFmpeg/librosa/Essentia/aubio/Basic
    Pitch/Demucs/YAMNet/PANNs/OpenL3 invocation of any kind.
  - No dense audio extraction, no stem separation, no ML dependency.
  - No semantic truth generation. These primitives validate that a
    *record* is shaped safely -- they never generate audio evidence
    and never claim CLULatent understands audio.
  - Not wired into `validate.py`, `manifest.py`, `tracks.py`, or any
    CLI command. No new track file is written by this phase. A future
    `tracks/audio_digest_events.jsonl` (name TBD), once it exists,
    would just be another canonical track using the existing shared
    `EventEnvelope`, validated with the functions below before write.

Validation here follows the same non-raising `(errors, warnings)`
convention `analysis_lanes.py`/`analysis_adapters.py`/`review.py`
already use -- nothing in this module raises for a malformed *record*;
only `AudioDigestEventError` exists, and only for the narrow
`is_supported_audio_digest_type`-adjacent normalization helper, never
from the validators themselves.

Records are checked as raw, untrusted dicts (the same convention
`analysis_lanes.validate_analysis_event` uses for lane events, and for
the same reason: a lenient, coercing model would silently accept a
numeric string for `t_start_ms`; this module is the strictest gate a
raw JSON-shaped record passes through before anything downstream ever
trusts it).

Every payload field set below is a **closed whitelist**, not an
open/free-form dict like most Phase 2.5/2.6 analysis-lane payloads.
This is intentional: the whole point of the digest contract is to
keep dense, unbounded material *out* of these records (referenced by
`data_path` instead), so an unrecognized payload key -- which could be
a smuggled raw array, an identity field, or anything else out of
scope -- is always rejected rather than silently passed through.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .constants import AUDIO_DIGEST_TRACK_FILE
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix

# --- Record type catalog ----------------------------------------------------


class AudioDigestEventError(ValueError):
    """Raised by `normalize_audio_digest_type` for an unrecognized digest type.

    Never raised by any `validate_audio_digest_*` function -- those
    follow the non-raising `(errors, warnings)` convention, so a caller
    can collect every problem in one pass.
    """


AUDIO_DIGEST_RECORD_TYPES: tuple[str, ...] = (
    "audio_feature_series",
    "audio_digest_segment",
    "audio_llm_context_packet",
)
"""The three Phase 3.3 digest record types, in pyramid order (Level 1, 3, 5)."""

SUPPORTED_AUDIO_DIGEST_TYPES = frozenset(AUDIO_DIGEST_RECORD_TYPES)


def is_supported_audio_digest_type(record_type: Any) -> bool:
    """True if `record_type` is exactly one of `AUDIO_DIGEST_RECORD_TYPES`.

    Never raises, never normalizes -- a non-string or unrecognized
    value simply returns False.
    """
    return isinstance(record_type, str) and record_type in SUPPORTED_AUDIO_DIGEST_TYPES


def normalize_audio_digest_type(record_type: Any) -> str:
    """Validate `record_type` is a supported digest type, stripped of surrounding whitespace.

    Raises `AudioDigestEventError` if the result is not one of
    `AUDIO_DIGEST_RECORD_TYPES`.
    """
    if not isinstance(record_type, str):
        raise AudioDigestEventError(f"type must be a string, got {type(record_type).__name__}")
    normalized = record_type.strip()
    if normalized not in SUPPORTED_AUDIO_DIGEST_TYPES:
        raise AudioDigestEventError(
            f"{record_type!r} is not a supported audio digest record type "
            f"(must be one of {sorted(SUPPORTED_AUDIO_DIGEST_TYPES)})"
        )
    return normalized


# --- Conservative-language enforcement --------------------------------------

# Phase 3.3/3.4 forbidden-claims list. Checked (case-insensitively) as a
# substring against every free-text field a human or LLM would actually
# read: label, summary, caveats, top_evidence, warnings,
# omitted_detail_reason. Deliberately a fixed, documented list rather
# than a heuristic classifier -- conservative and predictable, matching
# `analysis_lanes.FORBIDDEN_CAUSAL_RELATION_TYPES`'s "small, fixed,
# whitelist/blacklist" style.
FORBIDDEN_AUDIO_DIGEST_PHRASES: frozenset[str] = frozenset(
    {
        "proves intent",
        "proof of intent",
        "manipulation",
        "manipulates you",
        "makes viewer afraid",
        "makes the viewer afraid",
        "clulatent understands audio",
        "semantic audio truth",
        "definitely exact instrument",
        "definitely exact sound source",
    }
)

# Additional bare certainty markers that make a label/summary read as
# truthy/asserted rather than a hedged candidate, even outside the
# exact forbidden phrases above (Phase 3.3 rule: "label must be
# candidate/qualified, not truthy").
_CERTAINTY_MARKERS: frozenset[str] = frozenset(
    {"definitely", "proven", "confirmed fact", "is a fact that", "the truth is"}
)

# Payload keys that would smuggle a raw dense array into a record this
# contract requires to stay small and bounded (Phase 3.3 rule: dense
# arrays are referenced by `data_path`, never inlined).
FORBIDDEN_RAW_ARRAY_KEYS: frozenset[str] = frozenset(
    {
        "values",
        "samples",
        "frames",
        "raw",
        "dense_values",
        "series_values",
        "embedding",
        "embeddings",
    }
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _check_conservative_language(value: str, field_name: str, errors: list[str]) -> None:
    lowered = value.lower()
    for phrase in FORBIDDEN_AUDIO_DIGEST_PHRASES:
        if phrase in lowered:
            errors.append(f"{field_name} contains forbidden language: {phrase!r}")
    for marker in _CERTAINTY_MARKERS:
        if marker in lowered:
            errors.append(
                f"{field_name} contains a truthy certainty marker {marker!r}; "
                "labels and summaries must stay hedged/qualified, not asserted as fact"
            )


# --- Field-level checks (append to an errors list; never raise) ------------


def _check_bounded_string(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool,
    errors: list[str],
    check_language: bool = False,
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
    if _CONTROL_CHAR_RE.search(value) or "\x00" in value:
        errors.append(f"{field_name} must not contain control characters")
        return
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > max_bytes:
        errors.append(f"{field_name} exceeds the {max_bytes}-byte bound (got {encoded_len} bytes)")
        return
    if check_language:
        _check_conservative_language(value, field_name, errors)


def _check_bounded_string_list(
    value: Any,
    field_name: str,
    *,
    max_item_bytes: int,
    max_items: int,
    required: bool,
    errors: list[str],
    check_language: bool = False,
) -> list[str] | None:
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
    if len(value) > max_items:
        errors.append(f"{field_name} exceeds the {max_items}-item bound (got {len(value)} items)")
        return None
    ok = True
    for item in value:
        before = len(errors)
        _check_bounded_string(
            item,
            f"{field_name}[]",
            max_bytes=max_item_bytes,
            required=True,
            errors=errors,
            check_language=check_language,
        )
        if len(errors) != before:
            ok = False
    return value if ok else None


def _check_confidence(value: Any, field_name: str, *, required: bool, errors: list[str]) -> None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field_name} must be a number, got {type(value).__name__}")
        return
    if not (0.0 <= float(value) <= 1.0):
        errors.append(f"{field_name} must be within [0.0, 1.0], got {value!r}")


def _check_positive_int(value: Any, field_name: str, *, max_value: int, errors: list[str]) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        errors.append(f"{field_name} must be an integer, got {type(value).__name__}")
        return
    if value <= 0:
        errors.append(f"{field_name} must be a positive integer, got {value}")
        return
    if value > max_value:
        errors.append(f"{field_name} exceeds the {max_value} bound (got {value})")


def _check_bool(value: Any, field_name: str, *, required: bool, errors: list[str]) -> None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, bool):
        errors.append(f"{field_name} must be a boolean, got {type(value).__name__}")


def _check_path_field(
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    errors: list[str],
) -> None:
    if not isinstance(value, str) or value == "":
        errors.append(f"{field_name} must be a non-empty string, got {value!r}")
        return
    try:
        if package_root is not None:
            resolve_in_package(package_root, value, field_name=field_name)
        else:
            validate_relative_posix(value, field_name=field_name)
    except PathSecurityError as exc:
        errors.append(str(exc))


def _check_no_unrecognized_fields(
    payload: dict[str, Any], allowed: frozenset[str], *, record_type: str, errors: list[str]
) -> None:
    for key in payload:
        if key in allowed:
            continue
        if key in FORBIDDEN_RAW_ARRAY_KEYS:
            errors.append(
                f"payload.{key} is a raw dense-array field, which {record_type} must never "
                "inline -- reference dense data by 'data_path' instead"
            )
        else:
            errors.append(f"payload.{key} is not a recognized field for {record_type}")


# --- Bound constants (local to this module; see security/limits.py for the ---
# --- shared, cross-module bounds this module reuses where practical) --------

MAX_ID_BYTES = 256
MAX_LABEL_BYTES = 256
MAX_TEXT_BYTES = 4 * 1024  # summary / caveat / warning / top_evidence entries
MAX_UNITS_BYTES = 64
MAX_FEATURE_NAME_BYTES = 128
MAX_TREND_BYTES = 64
MAX_TIME_RANGE_BYTES = 512
MAX_OMITTED_REASON_BYTES = 1024
MAX_RETRIEVAL_HINT_BYTES = 512

MAX_LINKED_ID_COUNT = 200
MAX_CAVEATS_COUNT = 20
MAX_WARNINGS_COUNT = 20
MAX_TOP_EVIDENCE_COUNT = 50
MAX_QUALITY_WARNINGS_COUNT = 20
MAX_DOMINANT_FEATURES_COUNT = 50

MAX_WINDOW_HOP_MS = 10 * 60 * 1000  # 10 minutes; a single window/hop this long is already absurd
MAX_BUDGET_TOKENS_ESTIMATE = 20_000  # generous but bounded LLM-context-packet budget


# --- audio_feature_series (Level 1) -----------------------------------------

_FEATURE_SERIES_PAYLOAD_KEYS = frozenset(
    {"feature", "window_ms", "hop_ms", "units", "data_path", "summary"}
)
_FEATURE_SERIES_SUMMARY_KEYS = frozenset(
    {"min", "max", "mean", "median", "trend", "count", "quality_warnings"}
)
_FEATURE_SERIES_SUMMARY_NUMERIC_KEYS = frozenset({"min", "max", "mean", "median"})


def _validate_feature_series_summary(summary: Any, errors: list[str]) -> None:
    if not isinstance(summary, dict):
        errors.append(f"payload.summary must be a JSON object, got {type(summary).__name__}")
        return
    if not summary:
        errors.append("payload.summary must not be empty; at least one stat field is required")
        return
    for key in summary:
        if key not in _FEATURE_SERIES_SUMMARY_KEYS:
            errors.append(f"payload.summary.{key} is not a recognized summary stat field")

    for key in _FEATURE_SERIES_SUMMARY_NUMERIC_KEYS:
        if key not in summary:
            continue
        value = summary[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"payload.summary.{key} must be a number, got {type(value).__name__}")

    if "count" in summary:
        value = summary["count"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"payload.summary.count must be a non-negative integer, got {value!r}")

    if "trend" in summary:
        _check_bounded_string(
            summary["trend"], "payload.summary.trend", max_bytes=MAX_TREND_BYTES, required=False, errors=errors
        )

    if "quality_warnings" in summary:
        _check_bounded_string_list(
            summary["quality_warnings"],
            "payload.summary.quality_warnings",
            max_item_bytes=MAX_TEXT_BYTES,
            max_items=MAX_QUALITY_WARNINGS_COUNT,
            required=False,
            errors=errors,
        )


def validate_audio_feature_series(
    event: dict[str, Any],
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw `audio_feature_series` record (envelope + payload).

    `event` is a plain JSON-shaped dict (see module docstring). Returns
    `(errors, warnings)`; an empty `errors` list means the record is
    shape-valid. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []
    _check_envelope_fields(event, expected_type="audio_feature_series", limits=limits, errors=errors)

    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return errors, warnings

    _check_no_unrecognized_fields(
        payload, _FEATURE_SERIES_PAYLOAD_KEYS, record_type="audio_feature_series", errors=errors
    )

    _check_bounded_string(
        payload.get("feature"), "payload.feature", max_bytes=MAX_FEATURE_NAME_BYTES, required=True, errors=errors
    )
    _check_bounded_string(
        payload.get("units"), "payload.units", max_bytes=MAX_UNITS_BYTES, required=True, errors=errors
    )

    if "window_ms" not in payload:
        errors.append("payload.window_ms is required")
    else:
        _check_positive_int(payload.get("window_ms"), "payload.window_ms", max_value=MAX_WINDOW_HOP_MS, errors=errors)

    if "hop_ms" not in payload:
        errors.append("payload.hop_ms is required")
    else:
        _check_positive_int(payload.get("hop_ms"), "payload.hop_ms", max_value=MAX_WINDOW_HOP_MS, errors=errors)

    if "data_path" not in payload:
        errors.append("payload.data_path is required")
    else:
        _check_path_field(payload.get("data_path"), "payload.data_path", package_root=package_root, errors=errors)

    if "summary" not in payload:
        errors.append("payload.summary is required")
    else:
        _validate_feature_series_summary(payload.get("summary"), errors)

    return errors, warnings


# --- audio_digest_segment (Level 3) -----------------------------------------

_DIGEST_SEGMENT_PAYLOAD_KEYS = frozenset(
    {
        "label",
        "summary",
        "linked_event_ids",
        "linked_feature_series_ids",
        "salience",
        "recommended_for_llm_context",
        "caveats",
        "dominant_features",
    }
)


def validate_audio_digest_segment(
    event: dict[str, Any],
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw `audio_digest_segment` record (envelope + payload).

    Never raises. See module docstring for the non-raising convention.
    """
    errors: list[str] = []
    warnings: list[str] = []
    _check_envelope_fields(event, expected_type="audio_digest_segment", limits=limits, errors=errors)

    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return errors, warnings

    _check_no_unrecognized_fields(
        payload, _DIGEST_SEGMENT_PAYLOAD_KEYS, record_type="audio_digest_segment", errors=errors
    )

    _check_bounded_string(
        payload.get("label"),
        "payload.label",
        max_bytes=MAX_LABEL_BYTES,
        required=True,
        errors=errors,
        check_language=True,
    )
    _check_bounded_string(
        payload.get("summary"),
        "payload.summary",
        max_bytes=MAX_TEXT_BYTES,
        required=True,
        errors=errors,
        check_language=True,
    )

    _check_bounded_string_list(
        payload.get("linked_event_ids"),
        "payload.linked_event_ids",
        max_item_bytes=MAX_ID_BYTES,
        max_items=MAX_LINKED_ID_COUNT,
        required=True,
        errors=errors,
    )
    _check_bounded_string_list(
        payload.get("linked_feature_series_ids"),
        "payload.linked_feature_series_ids",
        max_item_bytes=MAX_ID_BYTES,
        max_items=MAX_LINKED_ID_COUNT,
        required=True,
        errors=errors,
    )

    if "salience" not in payload:
        errors.append("payload.salience is required")
    else:
        _check_confidence(payload.get("salience"), "payload.salience", required=True, errors=errors)

    _check_bool(
        payload.get("recommended_for_llm_context"),
        "payload.recommended_for_llm_context",
        required=True,
        errors=errors,
    )

    _check_bounded_string_list(
        payload.get("caveats"),
        "payload.caveats",
        max_item_bytes=MAX_TEXT_BYTES,
        max_items=MAX_CAVEATS_COUNT,
        required=True,
        errors=errors,
        check_language=True,
    )

    if "dominant_features" in payload:
        _check_bounded_string_list(
            payload.get("dominant_features"),
            "payload.dominant_features",
            max_item_bytes=MAX_FEATURE_NAME_BYTES,
            max_items=MAX_DOMINANT_FEATURES_COUNT,
            required=False,
            errors=errors,
        )

    return errors, warnings


# --- audio_llm_context_packet (Level 5) -------------------------------------

_CONTEXT_PACKET_PAYLOAD_KEYS = frozenset(
    {
        "time_range",
        "budget_tokens_estimate",
        "summary",
        "top_evidence",
        "warnings",
        "caveats",
        "linked_event_ids",
        "linked_digest_segment_ids",
        "linked_feature_series_ids",
        "omitted_detail_reason",
        "retrieval_hints",
    }
)

_RETRIEVAL_HINT_REQUIRED_KEYS = ("by_time_range", "by_evidence_id")


def _check_time_range(value: Any, errors: list[str]) -> None:
    if isinstance(value, str):
        _check_bounded_string(
            value, "payload.time_range", max_bytes=MAX_TIME_RANGE_BYTES, required=True, errors=errors
        )
        return
    if isinstance(value, dict):
        for key in ("t_start_ms", "t_end_ms"):
            if key not in value:
                errors.append(f"payload.time_range.{key} is required when time_range is an object")
                continue
            item = value[key]
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                errors.append(f"payload.time_range.{key} must be a non-negative integer, got {item!r}")
        if (
            isinstance(value.get("t_start_ms"), int)
            and not isinstance(value.get("t_start_ms"), bool)
            and isinstance(value.get("t_end_ms"), int)
            and not isinstance(value.get("t_end_ms"), bool)
            and value["t_end_ms"] < value["t_start_ms"]
        ):
            errors.append("payload.time_range.t_end_ms must be >= payload.time_range.t_start_ms")
        extra_keys = set(value) - {"t_start_ms", "t_end_ms"}
        for key in extra_keys:
            errors.append(f"payload.time_range.{key} is not a recognized field")
        return
    errors.append(f"payload.time_range must be an object or string, got {type(value).__name__}")


def _check_retrieval_hints(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"payload.retrieval_hints must be a JSON object, got {type(value).__name__}")
        return
    for key in _RETRIEVAL_HINT_REQUIRED_KEYS:
        if key not in value:
            errors.append(f"payload.retrieval_hints.{key} is required")
            continue
        _check_bounded_string(
            value[key],
            f"payload.retrieval_hints.{key}",
            max_bytes=MAX_RETRIEVAL_HINT_BYTES,
            required=True,
            errors=errors,
        )
    extra_keys = set(value) - set(_RETRIEVAL_HINT_REQUIRED_KEYS)
    for key in extra_keys:
        errors.append(f"payload.retrieval_hints.{key} is not a recognized field")


def _check_evidence_not_truth_caveat(caveats: Any, errors: list[str]) -> None:
    if not isinstance(caveats, list):
        return
    for item in caveats:
        if not isinstance(item, str):
            continue
        lowered = item.lower()
        if "evidence" in lowered and "not truth" in lowered:
            return
    errors.append(
        "payload.caveats must include at least one evidence-not-truth style caveat "
        "(mentioning both 'evidence' and 'not truth')"
    )


def validate_audio_llm_context_packet(
    event: dict[str, Any],
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw `audio_llm_context_packet` record (envelope + payload).

    Never raises. See module docstring for the non-raising convention.
    """
    errors: list[str] = []
    warnings: list[str] = []
    _check_envelope_fields(event, expected_type="audio_llm_context_packet", limits=limits, errors=errors)

    payload = event.get("payload") if isinstance(event, dict) else None
    if not isinstance(payload, dict):
        return errors, warnings

    _check_no_unrecognized_fields(
        payload, _CONTEXT_PACKET_PAYLOAD_KEYS, record_type="audio_llm_context_packet", errors=errors
    )

    if "time_range" not in payload:
        errors.append("payload.time_range is required")
    else:
        _check_time_range(payload.get("time_range"), errors)

    if "budget_tokens_estimate" not in payload:
        errors.append("payload.budget_tokens_estimate is required")
    else:
        _check_positive_int(
            payload.get("budget_tokens_estimate"),
            "payload.budget_tokens_estimate",
            max_value=MAX_BUDGET_TOKENS_ESTIMATE,
            errors=errors,
        )

    _check_bounded_string(
        payload.get("summary"),
        "payload.summary",
        max_bytes=MAX_TEXT_BYTES,
        required=True,
        errors=errors,
        check_language=True,
    )

    _check_bounded_string_list(
        payload.get("top_evidence"),
        "payload.top_evidence",
        max_item_bytes=MAX_TEXT_BYTES,
        max_items=MAX_TOP_EVIDENCE_COUNT,
        required=True,
        errors=errors,
        check_language=True,
    )

    _check_bounded_string_list(
        payload.get("warnings"),
        "payload.warnings",
        max_item_bytes=MAX_TEXT_BYTES,
        max_items=MAX_WARNINGS_COUNT,
        required=True,
        errors=errors,
        check_language=True,
    )

    caveats = _check_bounded_string_list(
        payload.get("caveats"),
        "payload.caveats",
        max_item_bytes=MAX_TEXT_BYTES,
        max_items=MAX_CAVEATS_COUNT,
        required=True,
        errors=errors,
        check_language=True,
    )
    if caveats is not None:
        _check_evidence_not_truth_caveat(caveats, errors)

    _check_bounded_string_list(
        payload.get("linked_event_ids"),
        "payload.linked_event_ids",
        max_item_bytes=MAX_ID_BYTES,
        max_items=MAX_LINKED_ID_COUNT,
        required=True,
        errors=errors,
    )
    _check_bounded_string_list(
        payload.get("linked_digest_segment_ids"),
        "payload.linked_digest_segment_ids",
        max_item_bytes=MAX_ID_BYTES,
        max_items=MAX_LINKED_ID_COUNT,
        required=True,
        errors=errors,
    )
    _check_bounded_string_list(
        payload.get("linked_feature_series_ids"),
        "payload.linked_feature_series_ids",
        max_item_bytes=MAX_ID_BYTES,
        max_items=MAX_LINKED_ID_COUNT,
        required=True,
        errors=errors,
    )

    _check_bounded_string(
        payload.get("omitted_detail_reason"),
        "payload.omitted_detail_reason",
        max_bytes=MAX_OMITTED_REASON_BYTES,
        required=True,
        errors=errors,
        check_language=True,
    )

    if "retrieval_hints" not in payload:
        errors.append("payload.retrieval_hints is required")
    else:
        _check_retrieval_hints(payload.get("retrieval_hints"), errors)

    return errors, warnings


# --- Shared envelope check ---------------------------------------------------

_ENVELOPE_TOP_LEVEL_KEYS = frozenset(
    {"id", "type", "t_start_ms", "t_end_ms", "producer", "confidence", "payload"}
)


def _check_envelope_fields(
    event: Any,
    *,
    expected_type: str | None,
    limits: Limits,
    errors: list[str],
) -> None:
    """Check the shared `EventEnvelope` shape every digest record must satisfy.

    `expected_type`, if given, additionally requires `event["type"] ==
    expected_type` (used by the per-type validators, which are always
    called knowing which type they check). `validate_audio_digest_event`
    passes `expected_type=None` and instead checks `type` is *any*
    supported digest type, since it does not yet know which one.
    """
    if not isinstance(event, dict):
        errors.append(f"audio digest record must be a JSON object, got {type(event).__name__}")
        return

    for key in event:
        if key not in _ENVELOPE_TOP_LEVEL_KEYS:
            errors.append(f"{key} is not a recognized top-level field")

    _check_bounded_string(event.get("id"), "id", max_bytes=MAX_ID_BYTES, required=True, errors=errors)

    record_type = event.get("type")
    if "type" not in event:
        errors.append("type is required")
    elif expected_type is not None:
        if record_type != expected_type:
            errors.append(f"type must be {expected_type!r}, got {record_type!r}")
    elif not is_supported_audio_digest_type(record_type):
        errors.append(
            f"type {record_type!r} is not a supported audio digest record type "
            f"(must be one of {sorted(SUPPORTED_AUDIO_DIGEST_TYPES)})"
        )

    t_start_ms = event.get("t_start_ms")
    t_start_valid = isinstance(t_start_ms, int) and not isinstance(t_start_ms, bool) and t_start_ms >= 0
    if "t_start_ms" not in event:
        errors.append("t_start_ms is required")
    elif not isinstance(t_start_ms, int) or isinstance(t_start_ms, bool):
        errors.append(f"t_start_ms must be an integer, got {type(t_start_ms).__name__}")
    elif t_start_ms < 0:
        errors.append(f"t_start_ms must be >= 0, got {t_start_ms}")

    t_end_ms = event.get("t_end_ms")
    t_end_valid = isinstance(t_end_ms, int) and not isinstance(t_end_ms, bool) and t_end_ms >= 0
    if "t_end_ms" not in event:
        errors.append("t_end_ms is required")
    elif not isinstance(t_end_ms, int) or isinstance(t_end_ms, bool):
        errors.append(f"t_end_ms must be an integer, got {type(t_end_ms).__name__}")
    elif t_end_ms < 0:
        errors.append(f"t_end_ms must be >= 0, got {t_end_ms}")

    if t_start_valid and t_end_valid and t_end_ms < t_start_ms:
        errors.append(f"t_end_ms ({t_end_ms}) must be >= t_start_ms ({t_start_ms})")

    producer = event.get("producer")
    if "producer" not in event:
        errors.append("producer is required")
    elif not isinstance(producer, dict):
        errors.append(f"producer must be an object, got {type(producer).__name__}")
    else:
        _check_bounded_string(
            producer.get("name"),
            "producer.name",
            max_bytes=MAX_LABEL_BYTES,
            required=True,
            errors=errors,
        )
        _check_bounded_string(
            producer.get("version"),
            "producer.version",
            max_bytes=MAX_LABEL_BYTES,
            required=True,
            errors=errors,
        )
        extra_keys = set(producer) - {"name", "version"}
        for key in extra_keys:
            errors.append(f"producer.{key} is not a recognized field")

    if "confidence" not in event:
        errors.append("confidence is required")
    else:
        _check_confidence(event.get("confidence"), "confidence", required=True, errors=errors)

    if "payload" not in event:
        errors.append("payload is required")
    elif not isinstance(event.get("payload"), dict):
        errors.append(f"payload must be a JSON object, got {type(event.get('payload')).__name__}")


# --- Dispatch ----------------------------------------------------------------

_VALIDATORS = {
    "audio_feature_series": validate_audio_feature_series,
    "audio_digest_segment": validate_audio_digest_segment,
    "audio_llm_context_packet": validate_audio_llm_context_packet,
}


def validate_audio_digest_event(
    event: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted audio digest record of any supported type.

    Dispatches on `event["type"]` to `validate_audio_feature_series`,
    `validate_audio_digest_segment`, or `validate_audio_llm_context_packet`.
    If `type` is missing or not a supported digest type, returns a
    single shared-envelope error set (still shape-checking everything
    else about the record) rather than raising. Never raises.
    """
    if not isinstance(event, dict):
        return [f"audio digest record must be a JSON object, got {type(event).__name__}"], []

    record_type = event.get("type")
    validator = _VALIDATORS.get(record_type) if isinstance(record_type, str) else None
    if validator is None:
        errors: list[str] = []
        warnings: list[str] = []
        _check_envelope_fields(event, expected_type=None, limits=limits, errors=errors)
        return errors, warnings

    if validator is validate_audio_feature_series:
        return validator(event, package_root=package_root, limits=limits)
    return validator(event, limits=limits)


def validate_audio_digest_track(
    events: Iterable[Any],
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a full mixed-type digest track: every event plus track-wide rules.

    `events` is an iterable of raw record dicts, of any mix of the
    three supported digest types (a digest track is expected to
    interleave Level 1/3/5 records, unlike a single-lane Phase 2.6
    analysis track). Returns `(errors, warnings)` aggregated across
    every record, each error prefixed with `[index]` (and the record's
    `id`, if it has one). Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()

    for index, record in enumerate(events):
        record_errors, record_warnings = validate_audio_digest_event(
            record, package_root=package_root, limits=limits
        )
        record_id = record.get("id") if isinstance(record, dict) else None
        prefix = f"[{index}]" + (f" ({record_id})" if isinstance(record_id, str) else "")
        errors.extend(f"{prefix}: {message}" for message in record_errors)
        warnings.extend(f"{prefix}: {message}" for message in record_warnings)

        if isinstance(record_id, str) and record_id != "":
            if record_id in seen_ids:
                errors.append(f"{prefix}: duplicate audio digest record id")
            else:
                seen_ids.add(record_id)

    return errors, warnings


# --- Receipt shape validation (Phase 3.7, package-validation use) -----------

_AUDIO_DIGEST_RECEIPT_STATUSES = frozenset({"success", "failure"})

_MAX_SANE_EVENT_COUNT = 1_000_000


def validate_audio_digest_receipt(
    record: Any,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `receipts/audio_digest.jsonl` entry.

    Mirrors `analysis_lanes.validate_analysis_receipt`'s conservative,
    shape-only checking, adapted to `AudioDigestReceipt.to_dict()`'s
    actual field set (`output_track` is a single string, not a list;
    there is no `adapter_name`). Catches obviously-bad data (wrong
    types, missing required fields, unbounded strings, an
    `output_track` that does not point at the one canonical audio
    digest track path) without asserting anything about whether the
    receipt's claims are true. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(record, dict):
        errors.append(f"audio digest receipt record must be a JSON object, got {type(record).__name__}")
        return errors, warnings

    for field_name in ("operation", "tool_name", "tool_version"):
        _check_bounded_string(
            record.get(field_name),
            field_name,
            max_bytes=MAX_LABEL_BYTES,
            required=True,
            errors=errors,
        )

    operation = record.get("operation")
    if isinstance(operation, str) and "audio_digest" not in operation:
        errors.append(f"operation must be audio-digest related, got {operation!r}")

    if "status" not in record:
        errors.append("status is required")
    elif record.get("status") not in _AUDIO_DIGEST_RECEIPT_STATUSES:
        errors.append(
            f"status must be one of {sorted(_AUDIO_DIGEST_RECEIPT_STATUSES)}, got {record.get('status')!r}"
        )

    output_track = record.get("output_track")
    if not isinstance(output_track, str):
        errors.append(f"output_track must be a string, got {type(output_track).__name__}")
    elif output_track != AUDIO_DIGEST_TRACK_FILE:
        errors.append(f"output_track must be {AUDIO_DIGEST_TRACK_FILE!r}, got {output_track!r}")

    event_count = record.get("event_count")
    if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count < 0:
        errors.append(f"event_count must be a non-negative integer, got {event_count!r}")
    elif event_count > _MAX_SANE_EVENT_COUNT:
        errors.append(
            f"event_count exceeds the {_MAX_SANE_EVENT_COUNT} sanity bound (got {event_count})"
        )

    if record.get("failure_details") is not None:
        _check_bounded_string(
            record.get("failure_details"),
            "failure_details",
            max_bytes=MAX_TEXT_BYTES,
            required=False,
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
                    max_bytes=MAX_TEXT_BYTES,
                    required=True,
                    errors=errors,
                )

    record_type_counts = record.get("record_type_counts")
    if record_type_counts is not None and not isinstance(record_type_counts, dict):
        errors.append(f"record_type_counts must be a JSON object, got {type(record_type_counts).__name__}")

    return errors, warnings


def validate_audio_digest_receipts(
    records: list[Any],
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate every entry of a `receipts/audio_digest.jsonl` batch.

    Returns `(errors, warnings)` aggregated across every record, each
    prefixed with its index so a caller can locate the offending
    entry. Never raises. An empty `records` list is valid -- a package
    with no audio digest writes yet has no reason to carry this file,
    and that alone must not make the package invalid.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_audio_digest_receipt(record, limits=limits)
        prefix = f"receipts/audio_digest.jsonl[{index}]"
        errors.extend(f"{prefix}: {message}" for message in record_errors)
        warnings.extend(f"{prefix}: {message}" for message in record_warnings)
    return errors, warnings
