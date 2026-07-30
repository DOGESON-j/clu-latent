"""Phase 3.17: agent review v0 schema + bounded, rule-based review logic.

Plain-English question this phase answers: **given one evidence
bundle, what evidence supports what, what is missing, and what needs a
human (or a later, more capable agent) to look at it?**

Agent review v0 is a bounded, deterministic, rule-based review layer
over exactly one evidence bundle (`evidence_bundle.py`). It never calls
an external model, never adds an external agent runtime, and never
adds model adapters -- it inspects a bundle's already-computed
`evidence_counts` / `coverage` / `missing_evidence` fields (and,
optionally, a small, bounded, untrusted claim file) and reports what it
finds using a small, closed vocabulary of labels.

Core rule (governs every check below):

    Agent review is review of evidence, not invention of truth.

This is a **review-of-evidence layer, not a scene understanding
system.** It never runs visual AI, never detects objects/faces/people/
actions/songs/emotion/intent, never captions or summarizes a scene, and
never asserts anything beyond what the linked evidence bundle already
counts and lists.

Allowed claims: "evidence present/missing/incomplete", "unsupported
claim", "needs human review", "ready for candidate review",
"insufficient evidence", "package validation failed", "bundle valid/
invalid", "no semantic claim made". Forbidden claims: anything naming
a detected object/person/face/action/song/weapon/emotion/intent, or a
generated scene summary (see `FORBIDDEN_AGENT_REVIEW_LABELS` /
`FORBIDDEN_AGENT_REVIEW_PHRASES` below).

This module implements no perception and no model call of any kind. It
only reads an already-gathered evidence bundle record (and, optionally,
a small untrusted claim file) and reports on evidence support and gaps.
"""

from __future__ import annotations

import re
from typing import Any

from .security.limits import DEFAULT_LIMITS, Limits

# --- Record type catalog ----------------------------------------------------

AGENT_REVIEW_RECORD_TYPE = "agent_review_event"
SUPPORTED_AGENT_REVIEW_TYPES = frozenset({AGENT_REVIEW_RECORD_TYPE})

# Track name, duplicated independently here (schema module) rather than
# only in `agent_review_writer.py`, mirroring
# `evidence_bundle.EVIDENCE_BUNDLE_TRACK_NAME` -- keeps any future
# consumer that only needs the track name from having to import the
# writer module.
AGENT_REVIEW_TRACK_NAME = "agent_review_events"

REVIEW_STATUSES: tuple[str, ...] = ("bundle_valid", "bundle_invalid", "needs_human_review", "insufficient_evidence")
SUPPORTED_REVIEW_STATUSES = frozenset(REVIEW_STATUSES)

RECOMMENDED_NEXT_STEPS: tuple[str, ...] = (
    "none",
    "human_review",
    "collect_more_evidence",
    "run_candidate_lane",
    "validate_package",
)
SUPPORTED_RECOMMENDED_NEXT_STEPS = frozenset(RECOMMENDED_NEXT_STEPS)

CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")
SUPPORTED_CONFIDENCE_LEVELS = frozenset(CONFIDENCE_LEVELS)

SEVERITIES: tuple[str, ...] = ("info", "warning", "error")
SUPPORTED_SEVERITIES = frozenset(SEVERITIES)

# The fixed, closed vocabulary of finding labels agent review v0 is
# permitted to write. Anything outside this set is rejected outright
# -- a hand-edited or (in a future phase) model-produced record can
# never smuggle a new, unreviewed label into this lane.
ALLOWED_AGENT_REVIEW_LABELS: frozenset[str] = frozenset(
    {
        "evidence_present",
        "evidence_missing",
        "evidence_incomplete",
        "unsupported_claim",
        "needs_human_review",
        "ready_for_candidate_review",
        "insufficient_evidence",
        "package_validation_failed",
        "bundle_valid",
        "bundle_invalid",
        "no_semantic_claim_made",
    }
)

# Explicitly named (not merely "not in ALLOWED_AGENT_REVIEW_LABELS") so
# a validation error is unambiguous about *why* a label was rejected --
# these are the exact labels the Phase 3.17 spec calls out as
# semantic-claim labels this lane must never produce.
FORBIDDEN_AGENT_REVIEW_LABELS: frozenset[str] = frozenset(
    {
        "person_detected",
        "face_detected",
        "object_detected",
        "action_detected",
        "song_identified",
        "weapon_detected",
        "emotion_detected",
        "intent_detected",
        "scene_understood",
        "summary_generated",
    }
)

AGENT_REVIEW_CAVEATS: tuple[str, ...] = (
    "Agent review of evidence, not ground truth.",
    "Does not identify objects, people, text, actions, intent, songs, or scene meaning.",
    "Does not use an external model in Phase 3.17.",
)

AGENT_REVIEW_METHOD = "rule_based_evidence_bundle_review_v0"

DEFAULT_MAX_FINDINGS = 50
DEFAULT_MAX_EVIDENCE_REF_IDS_PER_FINDING = 200
DEFAULT_MAX_EVIDENCE_LIST_ITEMS = 8  # bounded by len(evidence_bundle.EVIDENCE_CATEGORIES)
DEFAULT_MAX_UNSUPPORTED_CLAIMS = 100
DEFAULT_MAX_CLAIMS_PER_REQUEST = 100


class AgentReviewComputeError(ValueError):
    """Raised when an agent review cannot be safely produced (missing/invalid bundle, invalid claim file)."""


# --- Conservative-language enforcement --------------------------------------

# Checked (case-insensitively) as a substring against genuinely
# free-text fields only (`findings[].message`, `method`, `created_by`)
# -- never against `caveats`, which is checked by exact equality
# instead, since the fixed, approved caveat text itself uses these
# words in negation.
FORBIDDEN_AGENT_REVIEW_PHRASES: frozenset[str] = frozenset(
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
        "definitely happened",
        "who is in the video",
        "what object changed",
        "what action occurred",
    }
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _check_conservative_language(value: str, field_name: str, errors: list[str]) -> None:
    lowered = value.lower()
    for phrase in FORBIDDEN_AGENT_REVIEW_PHRASES:
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


def _check_non_negative_int(value: Any, field_name: str, *, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{field_name} must be an integer, got {type(value).__name__}")
        return
    if value < 0:
        errors.append(f"{field_name} must be >= 0, got {value!r}")


def _check_unit_interval(value: Any, field_name: str, *, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{field_name} must be a number, got {type(value).__name__}")
        return
    if not (0.0 <= float(value) <= 1.0):
        errors.append(f"{field_name} must be within [0.0, 1.0], got {value!r}")


# --- findings / unsupported_claims shape checks -----------------------------

_FINDING_KEYS = frozenset({"label", "severity", "message", "evidence_ref_ids"})
_UNSUPPORTED_CLAIM_KEYS = frozenset({"claim_id", "reason"})


def _check_finding(
    value: Any,
    field_name: str,
    *,
    known_evidence_ref_ids: set[str] | None,
    limits: Limits,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object, got {type(value).__name__}")
        return
    for key in value:
        if key not in _FINDING_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")

    label = value.get("label")
    if label in FORBIDDEN_AGENT_REVIEW_LABELS:
        errors.append(f"{field_name}.label is a forbidden semantic-claim label: {label!r}")
    elif label not in ALLOWED_AGENT_REVIEW_LABELS:
        errors.append(f"{field_name}.label must be one of {sorted(ALLOWED_AGENT_REVIEW_LABELS)}, got {label!r}")

    severity = value.get("severity")
    if severity not in SUPPORTED_SEVERITIES:
        errors.append(f"{field_name}.severity must be one of {sorted(SUPPORTED_SEVERITIES)}, got {severity!r}")

    _check_bounded_string(
        value.get("message"),
        f"{field_name}.message",
        max_bytes=limits.max_review_text_bytes,
        required=True,
        errors=errors,
        check_language=True,
    )

    ref_ids = _check_bounded_string_list(
        value.get("evidence_ref_ids"),
        f"{field_name}.evidence_ref_ids",
        max_item_bytes=limits.max_analysis_id_bytes,
        max_items=DEFAULT_MAX_EVIDENCE_REF_IDS_PER_FINDING,
        required=True,
        errors=errors,
    )
    if ref_ids is not None and known_evidence_ref_ids is not None:
        for ref_id in ref_ids:
            if ref_id not in known_evidence_ref_ids:
                errors.append(
                    f"{field_name}.evidence_ref_ids references {ref_id!r}, which does not exist in the "
                    "linked evidence bundle"
                )


def _check_findings(
    value: Any,
    field_name: str,
    *,
    known_evidence_ref_ids: set[str] | None,
    limits: Limits,
    errors: list[str],
) -> None:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array, got {type(value).__name__}")
        return
    if len(value) > DEFAULT_MAX_FINDINGS:
        errors.append(f"{field_name} exceeds the {DEFAULT_MAX_FINDINGS}-item bound (got {len(value)} items)")
        return
    for index, item in enumerate(value):
        _check_finding(
            item, f"{field_name}[{index}]", known_evidence_ref_ids=known_evidence_ref_ids, limits=limits, errors=errors
        )


def _check_unsupported_claim(value: Any, field_name: str, *, limits: Limits, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object, got {type(value).__name__}")
        return
    for key in value:
        if key not in _UNSUPPORTED_CLAIM_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")
    _check_bounded_string(
        value.get("claim_id"), f"{field_name}.claim_id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )
    _check_bounded_string(
        value.get("reason"),
        f"{field_name}.reason",
        max_bytes=limits.max_review_text_bytes,
        required=True,
        errors=errors,
        check_language=True,
    )


def _check_unsupported_claims(value: Any, field_name: str, *, limits: Limits, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{field_name} must be an array, got {type(value).__name__}")
        return
    if len(value) > DEFAULT_MAX_UNSUPPORTED_CLAIMS:
        errors.append(f"{field_name} exceeds the {DEFAULT_MAX_UNSUPPORTED_CLAIMS}-item bound (got {len(value)} items)")
        return
    for index, item in enumerate(value):
        _check_unsupported_claim(item, f"{field_name}[{index}]", limits=limits, errors=errors)


# --- Bundle cross-check helpers ----------------------------------------------

_EVIDENCE_CATEGORY_KEYS = frozenset(
    {
        "keyframes",
        "visual_change_candidates",
        "changed_region_candidates",
        "audio_events",
        "speech_events",
        "audio_digest_events",
        "review_events",
        "analysis_events",
    }
)


def evidence_ref_ids_in_bundle(bundle: dict[str, Any]) -> set[str]:
    """Collect every evidence ref id present anywhere in a bundle's `evidence_refs`.

    Defensive: a malformed bundle payload (missing/mistyped
    `evidence_refs`) yields an empty set rather than raising -- shape
    correctness of the bundle itself is `evidence_bundle.py`'s
    responsibility, not this helper's.
    """
    payload = bundle.get("payload") if isinstance(bundle.get("payload"), dict) else {}
    evidence_refs = payload.get("evidence_refs") if isinstance(payload.get("evidence_refs"), dict) else {}
    ids: set[str] = set()
    for category in _EVIDENCE_CATEGORY_KEYS:
        refs = evidence_refs.get(category)
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("id"), str):
                ids.add(ref["id"])
    return ids


# --- Envelope + payload validation ------------------------------------------

_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "evidence_bundle_id",
        "review_status",
        "findings",
        "evidence_present",
        "evidence_missing",
        "unsupported_claims",
        "recommended_next_step",
        "confidence",
        "caveats",
        "method",
        "created_by",
    }
)


def validate_agent_review_event(
    event: Any,
    *,
    bundle: dict[str, Any] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `agent_review_event` record dict.

    Returns `(errors, warnings)`; never raises. Checks the shared
    envelope fields, the closed payload whitelist, that `review_status`/
    `recommended_next_step`/`confidence` are one of their fixed
    vocabularies, that every finding uses an allowed (non-forbidden)
    label and a supported severity, that `evidence_present`/
    `evidence_missing` only name real evidence categories, and that the
    fixed `AGENT_REVIEW_CAVEATS` triple is present unmodified. When
    `bundle` (the linked evidence bundle record, as a plain dict) is
    given, additionally cross-checks that `evidence_bundle_id` matches
    the bundle's own id, that this review's time range fits inside the
    bundle's time range, and that every `evidence_ref_ids` entry in
    every finding actually exists in the bundle.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(event, dict):
        return [f"event must be an object, got {type(event).__name__}"], warnings

    _check_bounded_string(event.get("id"), "id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors)

    event_type = event.get("type")
    if event_type != AGENT_REVIEW_RECORD_TYPE:
        errors.append(f"type must be {AGENT_REVIEW_RECORD_TYPE!r}, got {event_type!r}")

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

    confidence_envelope = event.get("confidence")
    if confidence_envelope is not None:
        _check_unit_interval(confidence_envelope, "confidence", errors=errors)

    payload = event.get("payload")
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        return errors, warnings

    for key in payload:
        if key not in _ALLOWED_PAYLOAD_KEYS:
            errors.append(f"payload: unexpected field {key!r}")

    _check_bounded_string(
        payload.get("evidence_bundle_id"),
        "payload.evidence_bundle_id",
        max_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
    )

    known_evidence_ref_ids: set[str] | None = None
    if bundle is not None:
        if payload.get("evidence_bundle_id") != bundle.get("id"):
            errors.append(
                f"payload.evidence_bundle_id ({payload.get('evidence_bundle_id')!r}) does not match the "
                f"linked evidence bundle's id ({bundle.get('id')!r})"
            )
        bundle_start = bundle.get("t_start_ms")
        bundle_end = bundle.get("t_end_ms")
        if (
            isinstance(t_start, int)
            and isinstance(bundle_start, int)
            and isinstance(bundle_end, int)
            and not isinstance(t_start, bool)
            and isinstance(t_end, int)
            and not isinstance(t_end, bool)
            and (t_start < bundle_start or t_end > bundle_end)
        ):
            errors.append(
                f"t_start_ms/t_end_ms ([{t_start}, {t_end}]) must fit inside the linked evidence bundle's "
                f"time range ([{bundle_start}, {bundle_end}])"
            )
        known_evidence_ref_ids = evidence_ref_ids_in_bundle(bundle)

    review_status = payload.get("review_status")
    if review_status not in SUPPORTED_REVIEW_STATUSES:
        errors.append(f"payload.review_status must be one of {sorted(SUPPORTED_REVIEW_STATUSES)}, got {review_status!r}")

    _check_findings(
        payload.get("findings"), "payload.findings", known_evidence_ref_ids=known_evidence_ref_ids, limits=limits, errors=errors
    )

    _check_bounded_string_list(
        payload.get("evidence_present"),
        "payload.evidence_present",
        max_item_bytes=64,
        max_items=DEFAULT_MAX_EVIDENCE_LIST_ITEMS,
        required=True,
        errors=errors,
        allowed_values=_EVIDENCE_CATEGORY_KEYS,
    )
    _check_bounded_string_list(
        payload.get("evidence_missing"),
        "payload.evidence_missing",
        max_item_bytes=64,
        max_items=DEFAULT_MAX_EVIDENCE_LIST_ITEMS,
        required=True,
        errors=errors,
        allowed_values=_EVIDENCE_CATEGORY_KEYS,
    )

    _check_unsupported_claims(payload.get("unsupported_claims"), "payload.unsupported_claims", limits=limits, errors=errors)

    recommended_next_step = payload.get("recommended_next_step")
    if recommended_next_step not in SUPPORTED_RECOMMENDED_NEXT_STEPS:
        errors.append(
            f"payload.recommended_next_step must be one of {sorted(SUPPORTED_RECOMMENDED_NEXT_STEPS)}, "
            f"got {recommended_next_step!r}"
        )

    confidence = payload.get("confidence")
    if confidence not in SUPPORTED_CONFIDENCE_LEVELS:
        errors.append(f"payload.confidence must be one of {sorted(SUPPORTED_CONFIDENCE_LEVELS)}, got {confidence!r}")

    # Caveats are required to be exactly the fixed, pre-approved triple
    # -- not substring-scanned, since the approved text itself negates
    # the forbidden words. Exact-equality is the correct and sufficient
    # guard, mirroring `evidence_bundle.validate_evidence_bundle_event`.
    caveats = payload.get("caveats")
    if caveats != list(AGENT_REVIEW_CAVEATS):
        errors.append("payload.caveats must be exactly the fixed agent-review caveat triple")

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
        text_value = payload.get(field_name)
        if isinstance(text_value, str):
            _check_conservative_language(text_value, f"payload.{field_name}", errors)

    return errors, warnings


def validate_agent_review_track(
    events: Any,
    *,
    bundles_by_id: dict[str, dict[str, Any]] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a whole batch of `agent_review_event` records. Never raises.

    Runs `validate_agent_review_event` on every record (prefixing
    errors with `[index] (id)`), cross-checking each record against its
    linked bundle (looked up in `bundles_by_id` by `payload
    .evidence_bundle_id`, if given), and additionally rejects a
    duplicate id within the batch.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(events, list):
        return [f"events must be an array, got {type(events).__name__}"], warnings

    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("id") if isinstance(event, dict) else None
        prefix = f"[{index}] ({event_id!r})"

        bundle = None
        if bundles_by_id is not None and isinstance(event, dict) and isinstance(event.get("payload"), dict):
            bundle_id = event["payload"].get("evidence_bundle_id")
            if isinstance(bundle_id, str):
                bundle = bundles_by_id.get(bundle_id)
                if bundle is None:
                    errors.append(
                        f"{prefix}: payload.evidence_bundle_id ({bundle_id!r}) does not reference a known "
                        "evidence bundle"
                    )

        event_errors, event_warnings = validate_agent_review_event(event, bundle=bundle, limits=limits)
        errors.extend(f"{prefix}: {msg}" for msg in event_errors)
        warnings.extend(f"{prefix}: {msg}" for msg in event_warnings)

        if isinstance(event_id, str):
            if event_id in seen_ids:
                errors.append(f"{prefix}: duplicate id {event_id!r} within this batch")
            seen_ids.add(event_id)

    return errors, warnings


_RECEIPT_STATUSES = frozenset({"success", "failure"})


def validate_agent_review_receipt(record: Any, *, limits: Limits = DEFAULT_LIMITS) -> tuple[list[str], list[str]]:
    """Validate one raw `receipts/agent_review.jsonl` entry. Never raises."""
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


def validate_agent_review_receipts(records: Any, *, limits: Limits = DEFAULT_LIMITS) -> tuple[list[str], list[str]]:
    """Validate every entry of a raw `receipts/agent_review.jsonl` file. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(records, list):
        return [f"receipts must be an array, got {type(records).__name__}"], warnings
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_agent_review_receipt(record, limits=limits)
        errors.extend(f"[{index}]: {msg}" for msg in record_errors)
        warnings.extend(f"[{index}]: {msg}" for msg in record_warnings)
    return errors, warnings


# --- Rule-based review computation (Phase 3.17 v0; no external model) ------


def compute_agent_review_findings(
    bundle: dict[str, Any],
    *,
    claims: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministically derive review findings from one evidence bundle.

    Pure function of `bundle`'s already-computed `evidence_counts` /
    `coverage` / `missing_evidence` / `validation` fields (plus an
    optional, already-sanitized list of claim dicts -- see
    `agent_review_writer.py` for where a raw claim file gets validated
    and sanitized before ever reaching this function). Implements no
    perception and calls no model: every finding here is a direct,
    rule-based read of fields the evidence bundle already computed.

    Returns a dict with `review_status`, `findings`, `evidence_present`,
    `evidence_missing`, `unsupported_claims`, `recommended_next_step`,
    and `confidence` -- everything `validate_agent_review_event` checks
    on `payload` except the envelope/caveat/method/created_by fields,
    which the caller (the writer) attaches.
    """
    payload = bundle.get("payload") if isinstance(bundle.get("payload"), dict) else {}
    evidence_counts = payload.get("evidence_counts") if isinstance(payload.get("evidence_counts"), dict) else {}
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    missing_evidence = payload.get("missing_evidence") if isinstance(payload.get("missing_evidence"), list) else []
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}

    evidence_present = sorted(
        category for category in _EVIDENCE_CATEGORY_KEYS if evidence_counts.get(category, 0) > 0
    )
    evidence_missing = sorted(
        category for category in _EVIDENCE_CATEGORY_KEYS if category in missing_evidence
    )

    findings: list[dict[str, Any]] = []
    known_ref_ids = evidence_ref_ids_in_bundle(bundle)

    package_valid = validation.get("package_valid_at_build_time")
    if package_valid is False:
        findings.append(
            {
                "label": "package_validation_failed",
                "severity": "error",
                "message": "The package failed validation at the time this evidence bundle was built.",
                "evidence_ref_ids": [],
            }
        )

    for category in evidence_present:
        findings.append(
            {
                "label": "evidence_present",
                "severity": "info",
                "message": f"{category} evidence is present in this bundle ({evidence_counts.get(category, 0)} record(s)).",
                "evidence_ref_ids": [],
            }
        )
    for category in evidence_missing:
        findings.append(
            {
                "label": "evidence_missing",
                "severity": "warning",
                "message": f"{category} evidence is missing from this bundle.",
                "evidence_ref_ids": [],
            }
        )

    total_evidence = sum(evidence_counts.get(category, 0) for category in _EVIDENCE_CATEGORY_KEYS)
    present_category_count = len(evidence_present)

    unsupported_claims: list[dict[str, str]] = []
    for claim in claims or []:
        claim_id = claim.get("id") if isinstance(claim, dict) else None
        if not isinstance(claim_id, str):
            continue
        reason = _unsupported_claim_reason(claim, known_ref_ids=known_ref_ids)
        if reason is not None:
            unsupported_claims.append({"claim_id": claim_id, "reason": reason})
            findings.append(
                {
                    "label": "unsupported_claim",
                    "severity": "error",
                    "message": f"claim {claim_id!r} is not supported by this bundle's evidence: {reason}",
                    "evidence_ref_ids": [],
                }
            )

    if package_valid is False:
        review_status = "bundle_invalid"
    elif total_evidence == 0:
        review_status = "insufficient_evidence"
    elif unsupported_claims or present_category_count < 2:
        review_status = "needs_human_review"
    else:
        review_status = "bundle_valid"

    if review_status == "bundle_invalid":
        findings.append(
            {
                "label": "bundle_invalid",
                "severity": "error",
                "message": "This evidence bundle is linked to a package that failed validation.",
                "evidence_ref_ids": [],
            }
        )
    elif review_status == "insufficient_evidence":
        findings.append(
            {
                "label": "insufficient_evidence",
                "severity": "warning",
                "message": "No evidence of any category was found for this time range.",
                "evidence_ref_ids": [],
            }
        )
    elif review_status == "needs_human_review":
        findings.append(
            {
                "label": "needs_human_review",
                "severity": "warning",
                "message": "This bundle has thin or partially unsupported evidence and should be reviewed by a human.",
                "evidence_ref_ids": [],
            }
        )
    else:
        findings.append(
            {
                "label": "bundle_valid",
                "severity": "info",
                "message": "This evidence bundle is internally consistent and includes evidence from multiple categories.",
                "evidence_ref_ids": [],
            }
        )
        if present_category_count >= 3:
            findings.append(
                {
                    "label": "ready_for_candidate_review",
                    "severity": "info",
                    "message": "Enough non-semantic evidence exists across categories for a later candidate lane to review.",
                    "evidence_ref_ids": [],
                }
            )

    if not claims:
        findings.append(
            {
                "label": "no_semantic_claim_made",
                "severity": "info",
                "message": "This review makes no claim about what any collected evidence means.",
                "evidence_ref_ids": [],
            }
        )

    if review_status == "bundle_invalid":
        recommended_next_step = "validate_package"
    elif review_status == "insufficient_evidence":
        recommended_next_step = "collect_more_evidence"
    elif review_status == "needs_human_review":
        recommended_next_step = "human_review"
    elif present_category_count >= 3:
        recommended_next_step = "run_candidate_lane"
    else:
        recommended_next_step = "none"

    if review_status in ("bundle_invalid", "insufficient_evidence"):
        confidence = "low"
    elif review_status == "needs_human_review" or present_category_count < 3:
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "review_status": review_status,
        "findings": findings[:DEFAULT_MAX_FINDINGS],
        "evidence_present": evidence_present,
        "evidence_missing": evidence_missing,
        "unsupported_claims": unsupported_claims[:DEFAULT_MAX_UNSUPPORTED_CLAIMS],
        "recommended_next_step": recommended_next_step,
        "confidence": confidence,
    }


def _unsupported_claim_reason(claim: dict[str, Any], *, known_ref_ids: set[str]) -> str | None:
    """Return a bounded human-readable reason a claim is unsupported, or `None` if it is not flagged.

    Pure, deterministic rule set (Phase 3.17 v0, no model): a claim is
    unsupported if it references evidence ids outside the bundle, uses
    forbidden semantic language, or asserts a semantic category
    (object/person/action/song/intent/scene) this lane can never
    confirm from non-semantic evidence alone.
    """
    text = claim.get("text") if isinstance(claim.get("text"), str) else ""
    claim_type = claim.get("claim_type") if isinstance(claim.get("claim_type"), str) else ""
    evidence_ref_ids = claim.get("evidence_ref_ids") if isinstance(claim.get("evidence_ref_ids"), list) else []

    missing_refs = [
        ref_id for ref_id in evidence_ref_ids if isinstance(ref_id, str) and ref_id not in known_ref_ids
    ]
    if missing_refs:
        return f"references evidence ids not present in this bundle: {sorted(missing_refs)[:5]}"

    if not evidence_ref_ids:
        return "cites no evidence ids from this bundle"

    lowered = text.lower()
    for phrase in FORBIDDEN_AGENT_REVIEW_PHRASES:
        if phrase in lowered:
            # Deliberately does not quote the matched phrase back into the
            # reason text: this reason is itself checked for forbidden
            # language (it is a free-text field, same as any other), so
            # echoing the phrase would make the check fail against its own
            # output.
            return "uses language this lane treats as forbidden semantic language"

    semantic_claim_types = frozenset(
        {"object", "person", "face", "action", "song", "intent", "emotion", "scene", "identity"}
    )
    if claim_type in semantic_claim_types:
        return f"asserts a semantic claim type ({claim_type!r}) this lane cannot confirm from non-semantic evidence"

    certainty_markers = ("definitely", "certainly", "confirmed", "proves", "proven", "guaranteed")
    if any(marker in lowered for marker in certainty_markers):
        return "claims certainty beyond what bounded, non-semantic evidence can support"

    return None
