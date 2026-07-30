"""Phase 3.17: evidence bundle schema + bounded, non-generative gather logic.

Plain-English question this phase answers: **what evidence already
exists in this package for a given moment?**

Phase 3.15 (`visual_change.py`) taught CLULatent *when* visual change
happened. Phase 3.16 (`changed_region.py`) taught *where* inside the
frame. This module teaches CLULatent **how to gather evidence for a
moment** -- collecting already-computed, already-stored evidence
records (keyframes, visual change candidates, changed-region
candidates, audio/speech events, audio digest events, review events,
analysis lane events, receipts, and validation status) that overlap a
requested time range into one small, bounded, timestamped record. It
never runs visual AI, never decodes an image, never runs Pillow or
FFmpeg, never runs any ML model, never calls an LLM, and never infers
what any of the collected evidence *means*.

Core rule (governs every check below):

    Evidence bundle, not semantic interpretation.

An evidence bundle answers "what evidence exists for this time range?"
It does NOT answer "what definitely happened?", "who is in the
video?", "what object changed?", "what action occurred?", "what does
the scene mean?", "what song is this?", or "what is the intent or
emotion?".

Allowed claims: "evidence bundle", "evidence reference", "evidence
present/missing/incomplete", "coverage", "linked record". Forbidden
claims: anything naming an object/person/face/action/song, or
asserting what the collected evidence represents (see
`FORBIDDEN_EVIDENCE_BUNDLE_PHRASES` below).

Track naming: this lane's track is named `evidence_bundles`
(`tracks/evidence_bundles.jsonl`). Unlike Phase 3.15/3.16's
"recompute the whole track from scratch" `analyze` semantics, an
evidence bundle is built one time-range at a time and multiple bundles
can coexist in one package -- so the writer (`evidence_bundle_writer
.py`) appends one new bundle per `build` call and only refuses on a
duplicate bundle id (mirroring `audio_digest_writer.append_audio_
digest_events`'s append-with-duplicate-check pattern), not a
whole-track replace.

This module implements no perception of any kind. It only reads
already-computed records already sitting in the package's other
tracks and receipts, and packages small, bounded summaries of them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix

# --- Record type catalog ----------------------------------------------------

EVIDENCE_BUNDLE_RECORD_TYPE = "evidence_bundle"
SUPPORTED_EVIDENCE_BUNDLE_TYPES = frozenset({EVIDENCE_BUNDLE_RECORD_TYPE})

# Track name, duplicated independently here (schema module) rather than
# imported from `evidence_bundle_writer.py`, so that a module needing
# only the track name (e.g. `validate.py`) never has to import the
# writer module -- `evidence_bundle_writer.py` itself calls
# `validate.validate_package()`, so `validate.py -> evidence_bundle_writer.py
# -> validate.py` would otherwise be a circular import.
EVIDENCE_BUNDLE_TRACK_NAME = "evidence_bundles"

EVIDENCE_STRENGTHS: tuple[str, ...] = ("low", "medium", "high")
SUPPORTED_EVIDENCE_STRENGTHS = frozenset(EVIDENCE_STRENGTHS)

CHANGE_SCOPES: tuple[str, ...] = ("localized", "distributed", "global", "unknown")
SUPPORTED_CHANGE_SCOPES = frozenset(CHANGE_SCOPES)

# The fixed, closed catalog of evidence categories an evidence bundle
# can reference. Every one of `evidence_refs`, `evidence_counts`,
# `coverage`, and `missing_evidence` is keyed off this same list, so
# there is exactly one place that defines "what counts as a category
# of evidence" for this lane.
EVIDENCE_CATEGORIES: tuple[str, ...] = (
    "keyframes",
    "visual_change_candidates",
    "changed_region_candidates",
    "audio_events",
    "speech_events",
    "audio_digest_events",
    "review_events",
    "analysis_events",
)
SUPPORTED_EVIDENCE_CATEGORIES = frozenset(EVIDENCE_CATEGORIES)

# Maps each evidence category to its `coverage.*` boolean field name.
COVERAGE_KEY_FOR_CATEGORY: dict[str, str] = {
    "keyframes": "has_keyframes",
    "visual_change_candidates": "has_visual_change",
    "changed_region_candidates": "has_changed_regions",
    "audio_events": "has_audio",
    "speech_events": "has_speech",
    "audio_digest_events": "has_audio_digest",
    "review_events": "has_review",
    "analysis_events": "has_analysis",
}
COVERAGE_KEYS = frozenset(COVERAGE_KEY_FOR_CATEGORY.values())

# Fixed, documented caveats every evidence_bundle record carries. Not
# user-suppliable -- always exactly this triple, written by
# `evidence_bundle_writer.build_evidence_bundle` and re-checked as a
# required, unmodified field by the validator below.
EVIDENCE_BUNDLE_CAVEATS: tuple[str, ...] = (
    "Evidence bundle, not semantic interpretation.",
    "Collects existing package evidence only.",
    "Does not identify objects, people, text, actions, intent, songs, or scene meaning.",
)

EVIDENCE_BUNDLE_METHOD = "package_evidence_collection"

# Safety caps: how many refs a single category can carry inside one
# bundle, and how many entries `missing_evidence`/`receipts_summary
# .receipt_paths` can carry. Bundles are scoped to a bounded time
# range, so these should never be reached in practice -- they exist as
# a hard defensive ceiling, mirroring `changed_region.DEFAULT_MAX_
# CHANGED_REGION_PAIRS`.
DEFAULT_MAX_EVIDENCE_REFS_PER_CATEGORY = 1000
DEFAULT_MAX_MISSING_EVIDENCE_ITEMS = len(EVIDENCE_CATEGORIES)
DEFAULT_MAX_RECEIPT_PATHS = 20

# The fixed, closed set of receipt file paths this lane recognizes on
# `receipts_summary.receipt_paths`. Anything else is rejected outright
# rather than silently accepted, since receipt paths are otherwise
# arbitrary strings a hand-edited record could smuggle.
KNOWN_RECEIPT_FILES: frozenset[str] = frozenset(
    {
        "receipts/ingest.jsonl",
        "receipts/analyze.jsonl",
        "receipts/audio_digest.jsonl",
        "receipts/visual_change.jsonl",
        "receipts/changed_region.jsonl",
        "receipts/evidence_bundle.jsonl",
        "receipts/agent_review.jsonl",
    }
)


class EvidenceBundleComputeError(ValueError):
    """Raised when an evidence bundle cannot be safely built (unsafe path, invalid time range, unreadable source track)."""


# --- Conservative-language enforcement --------------------------------------

# Checked (case-insensitively) as a substring against genuinely
# free-text fields only (`method`, `created_by`,
# `validation.validation_summary`) -- never against `caveats`, which is
# checked by exact equality instead, since the fixed, approved caveat
# text itself uses these words in negation ("does not identify
# objects, people, text, actions, intent, songs, or scene meaning").
FORBIDDEN_EVIDENCE_BUNDLE_PHRASES: frozenset[str] = frozenset(
    {
        "person",
        "face",
        "object",
        "car",
        "weapon",
        "text",
        "logo",
        "action",
        "intent",
        "emotion",
        "scene meaning",
        "song",
        "the clip shows",
        "the model understands",
        "clulatent understands the scene",
        "clulatent understands visuals",
        "clulatent understands audio",
    }
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _check_conservative_language(value: str, field_name: str, errors: list[str]) -> None:
    lowered = value.lower()
    for phrase in FORBIDDEN_EVIDENCE_BUNDLE_PHRASES:
        if phrase in lowered:
            errors.append(f"{field_name} contains forbidden language: {phrase!r}")


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
    if _CONTROL_CHAR_RE.search(value):
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
    allowed_values: frozenset[str] | None = None,
) -> list[str] | None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return None
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array, got {type(value).__name__}")
        return None
    if len(value) > max_items:
        errors.append(f"{field_name} exceeds the {max_items}-item bound (got {len(value)} items)")
        return None
    ok = True
    for index, item in enumerate(value):
        before = len(errors)
        _check_bounded_string(
            item, f"{field_name}[{index}]", max_bytes=max_item_bytes, required=True, errors=errors
        )
        if allowed_values is not None and isinstance(item, str) and item not in allowed_values:
            errors.append(f"{field_name}[{index}] must be one of {sorted(allowed_values)}, got {item!r}")
        if len(errors) != before:
            ok = False
    return value if ok else None


def _check_path_field(
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    errors: list[str],
) -> None:
    if not isinstance(value, str) or value == "":
        errors.append(f"{field_name} must be a non-empty string")
        return
    try:
        if package_root is not None:
            resolve_in_package(package_root, value, field_name=field_name)
        else:
            validate_relative_posix(value, field_name=field_name)
    except PathSecurityError as exc:
        errors.append(str(exc))


def _check_unit_interval(value: Any, field_name: str, *, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field_name} must be a number, got {type(value).__name__}")
        return
    if not (0.0 <= float(value) <= 1.0):
        errors.append(f"{field_name} must be within [0.0, 1.0], got {value!r}")


def _check_non_negative_int(value: Any, field_name: str, *, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{field_name} must be an integer, got {type(value).__name__}")
        return
    if value < 0:
        errors.append(f"{field_name} must be >= 0, got {value!r}")


def _check_bool(value: Any, field_name: str, *, required: bool, errors: list[str]) -> None:
    if value is None:
        if required:
            errors.append(f"{field_name} is required")
        return
    if not isinstance(value, bool):
        errors.append(f"{field_name} must be a boolean, got {type(value).__name__}")


def _check_ref_time_range(value: dict[str, Any], field_name: str, *, errors: list[str]) -> None:
    t_start = value.get("t_start_ms")
    t_end = value.get("t_end_ms")
    _check_non_negative_int(t_start, f"{field_name}.t_start_ms", errors=errors)
    _check_non_negative_int(t_end, f"{field_name}.t_end_ms", errors=errors)
    if (
        isinstance(t_start, int)
        and isinstance(t_end, int)
        and not isinstance(t_start, bool)
        and not isinstance(t_end, bool)
        and t_end < t_start
    ):
        errors.append(f"{field_name}.t_end_ms ({t_end}) must be >= t_start_ms ({t_start})")


# --- Per-category evidence ref shape checks ---------------------------------

_KEYFRAME_REF_KEYS = frozenset({"id", "t_ms", "image_path"})
_VISUAL_CHANGE_REF_KEYS = frozenset({"id", "t_start_ms", "t_end_ms", "strength", "normalized_delta"})
_CHANGED_REGION_REF_KEYS = frozenset(
    {"id", "visual_change_id", "t_start_ms", "t_end_ms", "strength", "change_scope", "region", "normalized_region"}
)
_AUDIO_EVENT_REF_KEYS = frozenset({"id", "t_start_ms", "t_end_ms", "kind"})
_SPEECH_EVENT_REF_KEYS = frozenset({"id", "t_start_ms", "t_end_ms", "has_text"})
_AUDIO_DIGEST_REF_KEYS = frozenset({"id", "t_start_ms", "t_end_ms", "kind", "strength"})
_REVIEW_EVENT_REF_KEYS = frozenset({"id", "t_start_ms", "t_end_ms", "status"})
_ANALYSIS_EVENT_REF_KEYS = frozenset({"lane", "id", "t_start_ms", "t_end_ms", "status"})

_REF_KEYS_FOR_CATEGORY: dict[str, frozenset[str]] = {
    "keyframes": _KEYFRAME_REF_KEYS,
    "visual_change_candidates": _VISUAL_CHANGE_REF_KEYS,
    "changed_region_candidates": _CHANGED_REGION_REF_KEYS,
    "audio_events": _AUDIO_EVENT_REF_KEYS,
    "speech_events": _SPEECH_EVENT_REF_KEYS,
    "audio_digest_events": _AUDIO_DIGEST_REF_KEYS,
    "review_events": _REVIEW_EVENT_REF_KEYS,
    "analysis_events": _ANALYSIS_EVENT_REF_KEYS,
}


def _check_ref_common(value: dict[str, Any], field_name: str, *, limits: Limits, errors: list[str]) -> None:
    _check_bounded_string(
        value.get("id"), f"{field_name}.id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )


def _check_optional_strength(value: Any, field_name: str, *, errors: list[str]) -> None:
    if value is None:
        return
    if value not in SUPPORTED_EVIDENCE_STRENGTHS:
        errors.append(f"{field_name} must be null or one of {sorted(SUPPORTED_EVIDENCE_STRENGTHS)}, got {value!r}")


def _check_evidence_ref(
    category: str,
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    limits: Limits,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object, got {type(value).__name__}")
        return
    allowed_keys = _REF_KEYS_FOR_CATEGORY[category]
    for key in value:
        if key not in allowed_keys:
            errors.append(f"{field_name}: unexpected field {key!r}")

    if category == "keyframes":
        _check_ref_common(value, field_name, limits=limits, errors=errors)
        _check_non_negative_int(value.get("t_ms"), f"{field_name}.t_ms", errors=errors)
        _check_path_field(
            value.get("image_path"), f"{field_name}.image_path", package_root=package_root, errors=errors
        )
        return

    _check_ref_common(value, field_name, limits=limits, errors=errors)
    if category != "analysis_events":
        _check_ref_time_range(value, field_name, errors=errors)
    else:
        _check_ref_time_range(value, field_name, errors=errors)

    if category == "visual_change_candidates":
        strength = value.get("strength")
        if strength not in SUPPORTED_EVIDENCE_STRENGTHS:
            errors.append(
                f"{field_name}.strength must be one of {sorted(SUPPORTED_EVIDENCE_STRENGTHS)}, got {strength!r}"
            )
        _check_unit_interval(value.get("normalized_delta"), f"{field_name}.normalized_delta", errors=errors)

    elif category == "changed_region_candidates":
        _check_bounded_string(
            value.get("visual_change_id"),
            f"{field_name}.visual_change_id",
            max_bytes=limits.max_analysis_id_bytes,
            required=True,
            errors=errors,
        )
        strength = value.get("strength")
        if strength not in SUPPORTED_EVIDENCE_STRENGTHS:
            errors.append(
                f"{field_name}.strength must be one of {sorted(SUPPORTED_EVIDENCE_STRENGTHS)}, got {strength!r}"
            )
        change_scope = value.get("change_scope")
        if change_scope not in SUPPORTED_CHANGE_SCOPES:
            errors.append(
                f"{field_name}.change_scope must be one of {sorted(SUPPORTED_CHANGE_SCOPES)}, got {change_scope!r}"
            )
        # `region`/`normalized_region` are copied summaries of an
        # already-validated `changed_region_candidate` record (the
        # canonical geometry check lives in `changed_region.py`); here
        # they only need to be present, JSON objects.
        if not isinstance(value.get("region"), dict):
            errors.append(f"{field_name}.region must be an object")
        if not isinstance(value.get("normalized_region"), dict):
            errors.append(f"{field_name}.normalized_region must be an object")

    elif category == "audio_events":
        _check_bounded_string(
            value.get("kind"), f"{field_name}.kind", max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )

    elif category == "speech_events":
        _check_bool(value.get("has_text"), f"{field_name}.has_text", required=True, errors=errors)

    elif category == "audio_digest_events":
        _check_bounded_string(
            value.get("kind"), f"{field_name}.kind", max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )
        _check_optional_strength(value.get("strength"), f"{field_name}.strength", errors=errors)

    elif category == "review_events":
        _check_bounded_string(
            value.get("status"),
            f"{field_name}.status",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )

    elif category == "analysis_events":
        from .analysis_lanes import SUPPORTED_ANALYSIS_LANES

        lane = value.get("lane")
        if lane not in SUPPORTED_ANALYSIS_LANES:
            errors.append(f"{field_name}.lane must be one of {sorted(SUPPORTED_ANALYSIS_LANES)}, got {lane!r}")
        _check_bounded_string(
            value.get("status"),
            f"{field_name}.status",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )


def _check_evidence_refs(
    value: Any,
    field_name: str,
    *,
    package_root: Path | None,
    limits: Limits,
    errors: list[str],
) -> dict[str, list[Any]] | None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return None
    for key in value:
        if key not in SUPPORTED_EVIDENCE_CATEGORIES:
            errors.append(f"{field_name}: unexpected category {key!r}")

    parsed: dict[str, list[Any]] = {}
    for category in EVIDENCE_CATEGORIES:
        if category not in value:
            errors.append(f"{field_name}.{category} is required")
            continue
        refs = value[category]
        if not isinstance(refs, list):
            errors.append(f"{field_name}.{category} must be an array, got {type(refs).__name__}")
            continue
        if len(refs) > DEFAULT_MAX_EVIDENCE_REFS_PER_CATEGORY:
            errors.append(
                f"{field_name}.{category} exceeds the {DEFAULT_MAX_EVIDENCE_REFS_PER_CATEGORY}-item bound "
                f"(got {len(refs)} items)"
            )
            continue
        for index, ref in enumerate(refs):
            _check_evidence_ref(
                category,
                ref,
                f"{field_name}.{category}[{index}]",
                package_root=package_root,
                limits=limits,
                errors=errors,
            )
        parsed[category] = refs
    return parsed if len(parsed) == len(EVIDENCE_CATEGORIES) else None


def _check_evidence_counts(
    value: Any, field_name: str, *, evidence_refs: dict[str, list[Any]] | None, errors: list[str]
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return None
    for key in value:
        if key not in SUPPORTED_EVIDENCE_CATEGORIES:
            errors.append(f"{field_name}: unexpected category {key!r}")

    parsed: dict[str, int] = {}
    for category in EVIDENCE_CATEGORIES:
        if category not in value:
            errors.append(f"{field_name}.{category} is required")
            continue
        count = value[category]
        _check_non_negative_int(count, f"{field_name}.{category}", errors=errors)
        if isinstance(count, int) and not isinstance(count, bool):
            parsed[category] = count
            if evidence_refs is not None and category in evidence_refs and count != len(evidence_refs[category]):
                errors.append(
                    f"{field_name}.{category} ({count}) does not match the number of "
                    f"evidence_refs.{category} entries ({len(evidence_refs[category])})"
                )
    return parsed if len(parsed) == len(EVIDENCE_CATEGORIES) else None


def _check_coverage(value: Any, field_name: str, *, evidence_counts: dict[str, int] | None, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    for key in value:
        if key not in COVERAGE_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")

    for category, coverage_key in COVERAGE_KEY_FOR_CATEGORY.items():
        if coverage_key not in value:
            errors.append(f"{field_name}.{coverage_key} is required")
            continue
        _check_bool(value.get(coverage_key), f"{field_name}.{coverage_key}", required=True, errors=errors)
        if evidence_counts is not None and category in evidence_counts and isinstance(value.get(coverage_key), bool):
            expected = evidence_counts[category] > 0
            if value[coverage_key] != expected:
                errors.append(
                    f"{field_name}.{coverage_key} ({value[coverage_key]}) does not match evidence_counts."
                    f"{category} (expected {expected})"
                )


def _check_missing_evidence(
    value: Any, field_name: str, *, evidence_counts: dict[str, int] | None, errors: list[str]
) -> None:
    parsed = _check_bounded_string_list(
        value,
        field_name,
        max_item_bytes=64,
        max_items=DEFAULT_MAX_MISSING_EVIDENCE_ITEMS,
        required=True,
        errors=errors,
        allowed_values=SUPPORTED_EVIDENCE_CATEGORIES,
    )
    if parsed is None or evidence_counts is None:
        return
    expected = {category for category, count in evidence_counts.items() if count == 0}
    if set(parsed) != expected:
        errors.append(
            f"{field_name} must list exactly the zero-count evidence categories "
            f"(expected {sorted(expected)}, got {sorted(set(parsed))})"
        )


def _check_validation(value: Any, field_name: str, *, limits: Limits, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    allowed = frozenset({"package_valid_at_build_time", "validation_summary"})
    for key in value:
        if key not in allowed:
            errors.append(f"{field_name}: unexpected field {key!r}")
    _check_bool(
        value.get("package_valid_at_build_time"),
        f"{field_name}.package_valid_at_build_time",
        required=True,
        errors=errors,
    )
    _check_bounded_string(
        value.get("validation_summary"),
        f"{field_name}.validation_summary",
        max_bytes=limits.max_analysis_text_bytes,
        required=True,
        errors=errors,
        check_language=True,
    )


def _check_receipts_summary(
    value: Any, field_name: str, *, known_receipt_paths: set[str] | None, errors: list[str]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    allowed = frozenset({"receipt_paths", "receipt_count"})
    for key in value:
        if key not in allowed:
            errors.append(f"{field_name}: unexpected field {key!r}")

    receipt_paths = _check_bounded_string_list(
        value.get("receipt_paths"),
        f"{field_name}.receipt_paths",
        max_item_bytes=128,
        max_items=DEFAULT_MAX_RECEIPT_PATHS,
        required=True,
        errors=errors,
        allowed_values=KNOWN_RECEIPT_FILES,
    )
    if receipt_paths is not None and known_receipt_paths is not None:
        for path in receipt_paths:
            if path not in known_receipt_paths:
                errors.append(f"{field_name}.receipt_paths references {path!r}, which does not exist in the package")

    count = value.get("receipt_count")
    _check_non_negative_int(count, f"{field_name}.receipt_count", errors=errors)
    if receipt_paths is not None and isinstance(count, int) and not isinstance(count, bool) and count != len(receipt_paths):
        errors.append(
            f"{field_name}.receipt_count ({count}) does not match len(receipt_paths) ({len(receipt_paths)})"
        )


_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "timecode_start",
        "timecode_end",
        "evidence_refs",
        "evidence_counts",
        "coverage",
        "missing_evidence",
        "validation",
        "receipts_summary",
        "caveats",
        "method",
        "created_by",
    }
)


def validate_evidence_bundle_event(
    event: Any,
    *,
    package_root: Path | None = None,
    known_receipt_paths: set[str] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `evidence_bundle` record dict.

    Returns `(errors, warnings)`; never raises. Checks the shared
    envelope fields, the closed payload whitelist, the shape of every
    evidence category's refs, that `evidence_counts` matches the
    number of refs in each category, that `coverage` matches
    `evidence_counts`, that `missing_evidence` is exactly the
    zero-count categories, and that the fixed `EVIDENCE_BUNDLE_CAVEATS`
    triple is present unmodified. When `package_root` is given,
    referenced keyframe image paths are additionally checked for
    containment/symlink safety. When `known_receipt_paths` is given,
    `receipts_summary.receipt_paths` entries are checked for existence.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(event, dict):
        return [f"event must be an object, got {type(event).__name__}"], warnings

    _check_bounded_string(
        event.get("id"), "id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )

    event_type = event.get("type")
    if event_type != EVIDENCE_BUNDLE_RECORD_TYPE:
        errors.append(f"type must be {EVIDENCE_BUNDLE_RECORD_TYPE!r}, got {event_type!r}")

    t_start = event.get("t_start_ms")
    t_end = event.get("t_end_ms")
    if not isinstance(t_start, int) or isinstance(t_start, bool) or t_start < 0:
        errors.append("t_start_ms must be a non-negative integer")
    if not isinstance(t_end, int) or isinstance(t_end, bool) or t_end < 0:
        errors.append("t_end_ms must be a non-negative integer")
    if (
        isinstance(t_start, int)
        and isinstance(t_end, int)
        and not isinstance(t_start, bool)
        and not isinstance(t_end, bool)
        and t_end < t_start
    ):
        errors.append(f"t_end_ms ({t_end}) must be >= t_start_ms ({t_start})")

    producer = event.get("producer")
    if not isinstance(producer, dict):
        errors.append("producer must be an object with name/version")
    else:
        _check_bounded_string(
            producer.get("name"), "producer.name", max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
        )
        _check_bounded_string(
            producer.get("version"),
            "producer.version",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )

    confidence = event.get("confidence")
    if confidence is not None:
        _check_unit_interval(confidence, "confidence", errors=errors)

    payload = event.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        return errors, warnings

    for key in payload:
        if key not in _ALLOWED_PAYLOAD_KEYS:
            errors.append(f"payload: unexpected field {key!r}")

    _check_bounded_string(
        payload.get("timecode_start"),
        "payload.timecode_start",
        max_bytes=64,
        required=True,
        errors=errors,
    )
    _check_bounded_string(
        payload.get("timecode_end"),
        "payload.timecode_end",
        max_bytes=64,
        required=True,
        errors=errors,
    )

    evidence_refs = _check_evidence_refs(
        payload.get("evidence_refs"), "payload.evidence_refs", package_root=package_root, limits=limits, errors=errors
    )
    evidence_counts = _check_evidence_counts(
        payload.get("evidence_counts"), "payload.evidence_counts", evidence_refs=evidence_refs, errors=errors
    )
    _check_coverage(payload.get("coverage"), "payload.coverage", evidence_counts=evidence_counts, errors=errors)
    _check_missing_evidence(
        payload.get("missing_evidence"), "payload.missing_evidence", evidence_counts=evidence_counts, errors=errors
    )
    _check_validation(payload.get("validation"), "payload.validation", limits=limits, errors=errors)
    _check_receipts_summary(
        payload.get("receipts_summary"),
        "payload.receipts_summary",
        known_receipt_paths=known_receipt_paths,
        errors=errors,
    )

    # Caveats are required to be exactly the fixed, pre-approved triple
    # above -- not substring-scanned, since the approved text itself
    # negates the forbidden words ("does not identify objects,
    # people, ... or scene meaning"). Exact-equality is the correct
    # and sufficient guard, mirroring
    # `changed_region.validate_changed_region_event`'s identical
    # caveat check.
    caveats = payload.get("caveats")
    if caveats != list(EVIDENCE_BUNDLE_CAVEATS):
        errors.append("payload.caveats must be exactly the fixed evidence-bundle caveat triple")

    _check_bounded_string(
        payload.get("method"), "payload.method", max_bytes=limits.max_analysis_label_bytes, required=True, errors=errors
    )
    _check_bounded_string(
        payload.get("created_by"),
        "payload.created_by",
        max_bytes=limits.max_analysis_label_bytes,
        required=True,
        errors=errors,
    )
    for field_name in ("method", "created_by"):
        value = payload.get(field_name)
        if isinstance(value, str):
            _check_conservative_language(value, f"payload.{field_name}", errors)

    return errors, warnings


def validate_evidence_bundle_track(
    events: Any,
    *,
    package_root: Path | None = None,
    known_receipt_paths: set[str] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a whole batch of `evidence_bundle` records. Never raises.

    Runs `validate_evidence_bundle_event` on every record (prefixing
    errors with `[index] (id)`) and additionally rejects a duplicate id
    within the batch.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(events, list):
        return [f"events must be an array, got {type(events).__name__}"], warnings

    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("id") if isinstance(event, dict) else None
        prefix = f"[{index}] ({event_id!r})"
        event_errors, event_warnings = validate_evidence_bundle_event(
            event, package_root=package_root, known_receipt_paths=known_receipt_paths, limits=limits
        )
        errors.extend(f"{prefix}: {msg}" for msg in event_errors)
        warnings.extend(f"{prefix}: {msg}" for msg in event_warnings)

        if isinstance(event_id, str):
            if event_id in seen_ids:
                errors.append(f"{prefix}: duplicate id {event_id!r} within this batch")
            seen_ids.add(event_id)

    return errors, warnings


_RECEIPT_STATUSES = frozenset({"success", "failure"})


def validate_evidence_bundle_receipt(record: Any, *, limits: Limits = DEFAULT_LIMITS) -> tuple[list[str], list[str]]:
    """Validate one raw `receipts/evidence_bundle.jsonl` entry. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return [f"receipt must be an object, got {type(record).__name__}"], warnings

    if record.get("status") not in _RECEIPT_STATUSES:
        errors.append(f"receipt.status must be one of {sorted(_RECEIPT_STATUSES)}, got {record.get('status')!r}")
    for field_name in ("operation", "tool_name", "tool_version", "output_track"):
        _check_bounded_string(
            record.get(field_name), f"receipt.{field_name}", max_bytes=256, required=True, errors=errors
        )
    _check_bounded_string(
        record.get("failure_details"), "receipt.failure_details", max_bytes=4096, required=False, errors=errors
    )
    return errors, warnings


def validate_evidence_bundle_receipts(records: Any, *, limits: Limits = DEFAULT_LIMITS) -> tuple[list[str], list[str]]:
    """Validate every entry of a raw `receipts/evidence_bundle.jsonl` file. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(records, list):
        return [f"receipts must be an array, got {type(records).__name__}"], warnings
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_evidence_bundle_receipt(record, limits=limits)
        errors.extend(f"[{index}]: {msg}" for msg in record_errors)
        warnings.extend(f"[{index}]: {msg}" for msg in record_warnings)
    return errors, warnings
