"""Phase 2.0: human review / correction event factories and validation.

Implements the on-disk representation specified (design-only) by
`docs/design/PHASE_1_9_HUMAN_REVIEW_CORRECTION.md`. A review event is a
plain `EventEnvelope` — this module adds no new envelope schema — with a
`producer.name` of the form `human:<reviewer_id>` and one of seven
`type` values.

Core principle (unchanged from the Phase 1.9 design): human review is
**additive evidence**. Nothing here mutates, deletes, or overwrites a
model-generated event, another track's records, or the source media —
these factories only ever *construct* new `EventEnvelope` records for
`tracks/review_events.jsonl`, and `validate_review_track` only ever
*reads* them. Writing them to disk (an operation-locked `clulatent
review` command) and resolving "reviewed truth" at read time are later,
separate Phase 2.x work — see `docs/PHASE_2_REVIEW_EVENTS.md`.

Referential integrity here is **local to the review track only**:
`supersedes_event_ids` and `review_session_summary.review_event_ids` are
checked against the other ids present in the same `review_events.jsonl`
being validated (safe and self-contained, since those fields only ever
point at other review events). Resolving `source_event_ids` against
*other* canonical tracks (keyframes/audio_events/speech_events/...) is
deliberately **deferred** — see the module docstring note in
`validate_review_track` and `docs/PHASE_2_REVIEW_EVENTS.md`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .event import EventEnvelope, Producer
from .security.limits import DEFAULT_LIMITS, Limits

HUMAN_PRODUCER_PREFIX = "human:"

REVIEW_EVENT_TYPES = frozenset(
    {
        "review_status",
        "review_approval",
        "review_rejection",
        "review_correction",
        "review_override",
        "human_note",
        "review_session_summary",
    }
)

# Types that render a judgment about `source_event_ids` and therefore
# require it to be present and non-empty (Phase 1.9 rule 3).
JUDGMENT_EVENT_TYPES = frozenset(
    {
        "review_status",
        "review_approval",
        "review_rejection",
        "review_correction",
        "review_override",
    }
)

SOURCE_EVENT_IDS_REQUIRED_TYPES = JUDGMENT_EVENT_TYPES

REVIEW_STATES = frozenset(
    {
        "unreviewed",
        "needs_review",
        "uncertain",
        "reviewed",
        "approved",
        "corrected",
        "rejected",
        "superseded",
    }
)

# `superseded` is a derived, read-time state assigned by state resolution
# (Phase 1.9 "Review States") — never a state a human directly asserts
# when authoring a review event.
ASSERTABLE_REVIEW_STATES = REVIEW_STATES - {"superseded"}

_DOTTED_PATH_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# Same lexical class of check as security/paths.py's path validation and
# security/console.py's terminal-safety stripping: reject NUL and raw
# control characters (tab/newline are allowed in free text like `reason`
# or `note_text`).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ReviewEventError(ValueError):
    """Raised when a review event's fields fail a Phase 1.9 shape/bound rule."""


def _check_bounded_text(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool = False,
) -> None:
    if value is None:
        if required:
            raise ReviewEventError(f"{field_name} is required")
        return
    if not isinstance(value, str):
        raise ReviewEventError(f"{field_name} must be a string, got {type(value).__name__}")
    if required and value == "":
        raise ReviewEventError(f"{field_name} must not be empty")
    if "\x00" in value:
        raise ReviewEventError(f"{field_name} must not contain NUL bytes")
    if _CONTROL_CHAR_RE.search(value):
        raise ReviewEventError(f"{field_name} must not contain raw control characters")
    encoded_len = len(value.encode("utf-8"))
    if encoded_len > max_bytes:
        raise ReviewEventError(
            f"{field_name} exceeds the {max_bytes}-byte bound (got {encoded_len} bytes)"
        )


def _check_bounded_payload_patch(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool,
) -> None:
    """Validate a `corrected_payload`/`original_payload` partial patch.

    These are descriptive data (dotted field path -> new value), never an
    executable patch operation — see Phase 1.9 §"review_correction".
    """
    if value is None:
        if required:
            raise ReviewEventError(f"{field_name} is required")
        return
    if not isinstance(value, dict):
        raise ReviewEventError(f"{field_name} must be an object")
    if required and not value:
        raise ReviewEventError(f"{field_name} must not be empty")
    for key in value:
        if not isinstance(key, str) or not _DOTTED_PATH_RE.match(key):
            raise ReviewEventError(f"{field_name} key {key!r} is not a valid dotted field path")
    try:
        encoded = json.dumps(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReviewEventError(f"{field_name} is not JSON-serializable: {exc}") from exc
    if len(encoded) > max_bytes:
        raise ReviewEventError(
            f"{field_name} exceeds the {max_bytes}-byte bound (got {len(encoded)} bytes)"
        )


def _check_no_self_reference(event_id: str, ids: list[str], field_name: str) -> None:
    if event_id in ids:
        raise ReviewEventError(f"{field_name} must not reference its own event id ({event_id!r})")


def _check_id_list(
    value: Any,
    field_name: str,
    *,
    max_bytes: int,
    required: bool = False,
) -> None:
    """Validate a `*_event_ids`-style field: must be a JSON array of
    short, non-empty, NUL/control-character-free strings when present.

    Without this, a hand-edited package could put a bare string, a
    number, a dict, or a list containing non-string elements in a
    `source_event_ids`/`supersedes_event_ids`/`review_event_ids` field
    and have it silently pass every existing check: a truthy non-list
    value satisfies a bare `if not value` requiredness check, and a
    non-list value fails the `isinstance(value, list)` guard used by
    the self-reference/existence checks — meaning those checks simply
    never run instead of failing. This closes that gap by validating
    shape up front, before any of those checks run.
    """
    if value is None:
        if required:
            raise ReviewEventError(f"{field_name} is required")
        return
    if not isinstance(value, list):
        raise ReviewEventError(f"{field_name} must be an array of strings, got {type(value).__name__}")
    if required and not value:
        raise ReviewEventError(f"{field_name} must not be empty")
    for item in value:
        _check_bounded_text(item, f"{field_name}[]", max_bytes=max_bytes, required=True)


def _producer(reviewer_id: str, version: str) -> Producer:
    return Producer(name=f"{HUMAN_PRODUCER_PREFIX}{reviewer_id}", version=version)


# --- Factories ---------------------------------------------------------
#
# Each factory validates its own fields (bounds, requiredness,
# self-reference) and then relies on `EventEnvelope`'s own Pydantic
# validators (`extra="forbid"`, t_end_ms >= t_start_ms, confidence range)
# for envelope-level correctness. Id prefixes are per-type (rv_status_,
# rv_approval_, ...) rather than the single shared `rv_` namespace shown
# in the Phase 1.9 design doc's examples — a deliberate, documented
# Phase 2.0 implementation choice (see docs/PHASE_2_REVIEW_EVENTS.md) that
# makes a record's type identifiable from its id alone; it does not change
# the envelope shape or any validation rule.


def make_review_status_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    review_state: str,
    source_event_ids: list[str],
    reviewed_at: str,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_status` event: triage, flagging, or acknowledgment."""
    event_id = f"rv_status_{index:06d}"
    source_event_ids = list(source_event_ids)
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)

    if review_state not in ASSERTABLE_REVIEW_STATES:
        raise ReviewEventError(
            f"review_state {review_state!r} is not directly assertable "
            f"(must be one of {sorted(ASSERTABLE_REVIEW_STATES)})"
        )
    if not source_event_ids:
        raise ReviewEventError("review_status requires a non-empty source_event_ids")
    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    return EventEnvelope(
        id=event_id,
        type="review_status",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "review_state": review_state,
            "reason": reason,
            "reviewed_at": reviewed_at,
        },
    )


def make_review_approval_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    source_event_ids: list[str],
    reviewed_at: str,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_approval` event: confirms the target is correct as-is."""
    event_id = f"rv_approval_{index:06d}"
    source_event_ids = list(source_event_ids)
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)

    if not source_event_ids:
        raise ReviewEventError("review_approval requires a non-empty source_event_ids")
    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    return EventEnvelope(
        id=event_id,
        type="review_approval",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "review_state": "approved",
            "reason": reason,
            "reviewed_at": reviewed_at,
        },
    )


def make_review_rejection_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    source_event_ids: list[str],
    reviewed_at: str,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_rejection` event: asserts the target should not be trusted.

    Never deletes the target from its own track — see the module docstring.
    """
    event_id = f"rv_rejection_{index:06d}"
    source_event_ids = list(source_event_ids)
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)

    if not source_event_ids:
        raise ReviewEventError("review_rejection requires a non-empty source_event_ids")
    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    return EventEnvelope(
        id=event_id,
        type="review_rejection",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "review_state": "rejected",
            "reason": reason,
            "reviewed_at": reviewed_at,
        },
    )


def make_review_correction_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    source_event_ids: list[str],
    original_payload: dict[str, Any],
    corrected_payload: dict[str, Any],
    reviewed_at: str,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_correction` event: a field-level corrected value.

    `original_payload`/`corrected_payload` are partial patches (dotted
    field path -> value) that must share the exact same key set (Phase
    1.9 rule 6). The source event's own record is never modified — the
    correction lives only in this new record.
    """
    event_id = f"rv_correction_{index:06d}"
    source_event_ids = list(source_event_ids)
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)

    if not source_event_ids:
        raise ReviewEventError("review_correction requires a non-empty source_event_ids")
    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    _check_bounded_payload_patch(
        original_payload,
        "original_payload",
        max_bytes=limits.max_review_payload_bytes,
        required=True,
    )
    _check_bounded_payload_patch(
        corrected_payload,
        "corrected_payload",
        max_bytes=limits.max_review_payload_bytes,
        required=True,
    )
    if set(original_payload) != set(corrected_payload):
        raise ReviewEventError(
            "original_payload and corrected_payload must share the exact same set of keys"
        )

    return EventEnvelope(
        id=event_id,
        type="review_correction",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "review_state": "corrected",
            "reason": reason,
            "original_payload": original_payload,
            "corrected_payload": corrected_payload,
            "reviewed_at": reviewed_at,
        },
    )


def make_review_override_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    source_event_ids: list[str],
    override_kind: str,
    original_payload: dict[str, Any],
    corrected_payload: dict[str, Any],
    review_state: str,
    reviewed_at: str,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_override` event: a broader reinterpretation than a
    field-level correction (e.g. reclassify/respan/merge/split).

    `review_state` must be `"corrected"` or `"rejected"`; if
    `corrected_payload` reclassifies `type`, either is valid, otherwise
    `"corrected"` is required (Phase 1.9 rule 6a).
    """
    event_id = f"rv_override_{index:06d}"
    source_event_ids = list(source_event_ids)
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)
    _check_bounded_text(
        override_kind, "override_kind", max_bytes=limits.max_review_label_bytes, required=True
    )

    if not source_event_ids:
        raise ReviewEventError("review_override requires a non-empty source_event_ids")
    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    _check_bounded_payload_patch(
        original_payload,
        "original_payload",
        max_bytes=limits.max_review_payload_bytes,
        required=True,
    )
    _check_bounded_payload_patch(
        corrected_payload,
        "corrected_payload",
        max_bytes=limits.max_review_payload_bytes,
        required=True,
    )
    if set(original_payload) != set(corrected_payload):
        raise ReviewEventError(
            "original_payload and corrected_payload must share the exact same set of keys"
        )

    if review_state not in ("corrected", "rejected"):
        raise ReviewEventError(
            f"review_override review_state must be 'corrected' or 'rejected', got {review_state!r}"
        )
    is_reclassify = "type" in corrected_payload
    if not is_reclassify and review_state != "corrected":
        raise ReviewEventError(
            "review_override review_state must be 'corrected' unless corrected_payload "
            "reclassifies 'type'"
        )

    return EventEnvelope(
        id=event_id,
        type="review_override",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "review_state": review_state,
            "reason": reason,
            "override_kind": override_kind,
            "original_payload": original_payload,
            "corrected_payload": corrected_payload,
            "reviewed_at": reviewed_at,
        },
    )


def make_human_note_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    note_text: str,
    reviewed_at: str,
    source_event_ids: list[str] | None = None,
    supersedes_event_ids: list[str] | None = None,
    reviewer_label: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `human_note` event: freestanding commentary, not a judgment.

    Unlike every other review type, `source_event_ids` may be empty (a
    whole-package or whole-session note) and `review_state` is omitted
    entirely — a note does not transition any target's reviewed state.
    """
    event_id = f"rv_note_{index:06d}"
    source_event_ids = list(source_event_ids or [])
    supersedes_event_ids = list(supersedes_event_ids or [])

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(
        note_text, "note_text", max_bytes=limits.max_review_text_bytes, required=True
    )

    _check_no_self_reference(event_id, source_event_ids, "source_event_ids")
    _check_no_self_reference(event_id, supersedes_event_ids, "supersedes_event_ids")

    return EventEnvelope(
        id=event_id,
        type="human_note",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "source_event_ids": source_event_ids,
            "supersedes_event_ids": supersedes_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "note_text": note_text,
            "reviewed_at": reviewed_at,
        },
    )


def make_review_session_summary_event(
    *,
    index: int,
    t_start_ms: int,
    t_end_ms: int,
    reviewer_id: str,
    review_event_ids: list[str],
    session_id: str,
    started_at: str,
    ended_at: str,
    counts_by_review_state: dict[str, int],
    reviewer_label: str | None = None,
    reason: str | None = None,
    confidence: float | None = None,
    producer_version: str = "1.0",
    limits: Limits = DEFAULT_LIMITS,
) -> EventEnvelope:
    """Build a `review_session_summary` event: a session-level rollup.

    References *review* records via `review_event_ids`, not
    `source_event_ids` — it is a rollup of review work, not a judgment
    about model output. `counts_by_review_state` is supplied by the
    caller here; `validate_review_track` recomputes it from the
    referenced events and reports a mismatch as a hard error (Phase 1.9
    rule 9) — the factory itself does not have visibility into sibling
    events, so it only checks shape, not cross-record consistency.
    """
    event_id = f"rv_session_{index:06d}"
    review_event_ids = list(review_event_ids)

    _check_bounded_text(
        reviewer_id, "reviewer_id", max_bytes=limits.max_review_label_bytes, required=True
    )
    _check_bounded_text(reviewer_label, "reviewer_label", max_bytes=limits.max_review_label_bytes)
    _check_bounded_text(reason, "reason", max_bytes=limits.max_review_text_bytes)
    _check_bounded_text(
        session_id, "session_id", max_bytes=limits.max_review_label_bytes, required=True
    )

    if not review_event_ids:
        raise ReviewEventError("review_session_summary requires a non-empty review_event_ids")
    _check_no_self_reference(event_id, review_event_ids, "review_event_ids")

    if not isinstance(counts_by_review_state, dict):
        raise ReviewEventError("counts_by_review_state must be an object")
    for state, count in counts_by_review_state.items():
        if state not in REVIEW_STATES:
            raise ReviewEventError(
                f"counts_by_review_state key {state!r} is not a valid review_state"
            )
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ReviewEventError(
                f"counts_by_review_state[{state!r}] must be a non-negative integer"
            )

    return EventEnvelope(
        id=event_id,
        type="review_session_summary",
        t_start_ms=t_start_ms,
        t_end_ms=t_end_ms,
        producer=_producer(reviewer_id, producer_version),
        confidence=confidence,
        payload={
            "review_event_ids": review_event_ids,
            "reviewer_id": reviewer_id,
            "reviewer_label": reviewer_label,
            "session_id": session_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "counts_by_review_state": counts_by_review_state,
            "reason": reason,
        },
    )


# --- Validation ----------------------------------------------------------


def validate_review_track(
    events: list[EventEnvelope], *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], list[str]]:
    """Validate a `review_events.jsonl` track's records.

    Returns `(errors, warnings)`. This is a pure, read-only check over an
    already-parsed list of `EventEnvelope` records for a single track —
    the same shape `validate.py` uses for every other track — and is
    called from `validate_package` when a track named `"review_events"`
    is present in the manifest.

    Scope: every check here is **local to this one track**. Referential
    integrity of `source_event_ids` against *other* canonical tracks
    (resolving a `speech_segment` id, a `speaker_label` id, etc.) is
    deliberately **not** performed — that requires loading every other
    track's ids and is deferred to a future Tier-4 validation pass (see
    `docs/PHASE_2_REVIEW_EVENTS.md` and the schema freeze draft's
    Validation Tiers). `supersedes_event_ids` and
    `review_session_summary.review_event_ids` only ever point at other
    *review* events, so checking them against this track's own id set is
    both safe and complete.
    """
    errors: list[str] = []
    warnings: list[str] = []

    ids_in_track = {event.id for event in events}
    review_state_by_id: dict[str, str] = {}

    for event in events:
        prefix = f"review_events[{event.id}]"

        if event.type not in REVIEW_EVENT_TYPES:
            errors.append(f"{prefix}: type {event.type!r} is not a recognized review event type")
            continue

        if not event.producer.name.startswith(HUMAN_PRODUCER_PREFIX):
            errors.append(
                f"{prefix}: producer.name {event.producer.name!r} must start with "
                f"{HUMAN_PRODUCER_PREFIX!r} in review_events.jsonl"
            )
        else:
            # The part after "human:" is reviewer-controlled free-form
            # identity data (not re-parsed or trusted for anything), so it
            # must be bounded and control-character-free the same way
            # payload.reviewer_id is — otherwise an oversized or
            # control-character-laden producer.name would bypass every
            # bound check below simply by living in a field this function
            # never inspected.
            try:
                _check_bounded_text(
                    event.producer.name[len(HUMAN_PRODUCER_PREFIX) :],
                    f"{prefix}.producer.name",
                    max_bytes=limits.max_review_label_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))

        payload = event.payload

        try:
            _check_bounded_text(
                payload.get("reviewer_id"),
                f"{prefix}.reviewer_id",
                max_bytes=limits.max_review_label_bytes,
                required=True,
            )
            _check_bounded_text(
                payload.get("reviewer_label"),
                f"{prefix}.reviewer_label",
                max_bytes=limits.max_review_label_bytes,
            )
            _check_bounded_text(
                payload.get("reason"), f"{prefix}.reason", max_bytes=limits.max_review_text_bytes
            )
        except ReviewEventError as exc:
            errors.append(str(exc))

        # `source_event_ids`/`supersedes_event_ids` are validated for
        # *shape* (array of short, non-empty strings) before anything
        # below relies on `isinstance(..., list)` or plain truthiness —
        # otherwise a bare string, number, or dict in either field would
        # satisfy a truthiness check and skip every `isinstance` guard,
        # passing validation unnoticed.
        source_event_ids = payload.get("source_event_ids")
        try:
            _check_id_list(
                source_event_ids,
                f"{prefix}.source_event_ids",
                max_bytes=limits.max_review_label_bytes,
                required=event.type in SOURCE_EVENT_IDS_REQUIRED_TYPES,
            )
        except ReviewEventError as exc:
            errors.append(str(exc))
            source_event_ids = None
        else:
            if source_event_ids and event.id in source_event_ids:
                errors.append(f"{prefix}: source_event_ids must not reference its own event id")

        supersedes_event_ids = payload.get("supersedes_event_ids")
        try:
            _check_id_list(
                supersedes_event_ids,
                f"{prefix}.supersedes_event_ids",
                max_bytes=limits.max_review_label_bytes,
                required=False,
            )
        except ReviewEventError as exc:
            errors.append(str(exc))
        else:
            supersedes_event_ids = supersedes_event_ids or []
            if event.id in supersedes_event_ids:
                errors.append(f"{prefix}: supersedes_event_ids must not reference its own event id")
            for target_id in supersedes_event_ids:
                if target_id not in ids_in_track:
                    errors.append(
                        f"{prefix}: supersedes_event_ids references {target_id!r}, which does "
                        "not exist in review_events.jsonl"
                    )

        if event.type in JUDGMENT_EVENT_TYPES:
            review_state = payload.get("review_state")
            if review_state is None:
                errors.append(f"{prefix}: {event.type} requires review_state")
            elif not isinstance(review_state, str) or review_state not in ASSERTABLE_REVIEW_STATES:
                # `not isinstance(..., str)` is checked first and
                # short-circuits the `in` test against a frozenset: an
                # unhashable review_state (a list/dict from a hand-edited
                # record) would otherwise raise an uncaught TypeError from
                # `in ASSERTABLE_REVIEW_STATES` and crash validate_package
                # entirely instead of reporting a clean validation error.
                errors.append(
                    f"{prefix}: review_state {review_state!r} is not a directly-assertable "
                    f"review state (must be one of {sorted(ASSERTABLE_REVIEW_STATES)})"
                )
            else:
                review_state_by_id[event.id] = review_state

        if event.type in ("review_correction", "review_override"):
            original_payload = payload.get("original_payload")
            corrected_payload = payload.get("corrected_payload")
            try:
                _check_bounded_payload_patch(
                    original_payload,
                    f"{prefix}.original_payload",
                    max_bytes=limits.max_review_payload_bytes,
                    required=True,
                )
                _check_bounded_payload_patch(
                    corrected_payload,
                    f"{prefix}.corrected_payload",
                    max_bytes=limits.max_review_payload_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))
            else:
                if set(original_payload) != set(corrected_payload):
                    errors.append(
                        f"{prefix}: original_payload and corrected_payload must share the "
                        "exact same set of keys"
                    )

        if event.type == "review_override":
            try:
                _check_bounded_text(
                    payload.get("override_kind"),
                    f"{prefix}.override_kind",
                    max_bytes=limits.max_review_label_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))

            review_state = payload.get("review_state")
            corrected_payload = payload.get("corrected_payload")
            if (
                isinstance(review_state, str)
                and review_state in ("corrected", "rejected")
                and isinstance(corrected_payload, dict)
            ):
                is_reclassify = "type" in corrected_payload
                if not is_reclassify and review_state != "corrected":
                    errors.append(
                        f"{prefix}: review_override review_state must be 'corrected' unless "
                        "corrected_payload reclassifies 'type'"
                    )

        if event.type == "human_note":
            try:
                _check_bounded_text(
                    payload.get("note_text"),
                    f"{prefix}.note_text",
                    max_bytes=limits.max_review_text_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))
            if not source_event_ids:
                warnings.append(
                    f"{prefix}: human_note has empty source_event_ids (general commentary)"
                )

        if event.type == "review_session_summary":
            try:
                _check_bounded_text(
                    payload.get("session_id"),
                    f"{prefix}.session_id",
                    max_bytes=limits.max_review_label_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))

            review_event_ids = payload.get("review_event_ids")
            try:
                _check_id_list(
                    review_event_ids,
                    f"{prefix}.review_event_ids",
                    max_bytes=limits.max_review_label_bytes,
                    required=True,
                )
            except ReviewEventError as exc:
                errors.append(str(exc))
            else:
                if event.id in review_event_ids:
                    errors.append(f"{prefix}: review_event_ids must not reference its own event id")
                computed_counts: dict[str, int] = {}
                for ref_id in review_event_ids:
                    if ref_id not in ids_in_track:
                        errors.append(
                            f"{prefix}: review_event_ids references {ref_id!r}, which does not "
                            "exist in review_events.jsonl"
                        )
                        continue
                    state = review_state_by_id.get(ref_id)
                    if state is not None:
                        computed_counts[state] = computed_counts.get(state, 0) + 1

                declared_counts = payload.get("counts_by_review_state")
                if not isinstance(declared_counts, dict):
                    errors.append(f"{prefix}: counts_by_review_state must be an object")
                else:
                    normalized_declared = {k: v for k, v in declared_counts.items() if v}
                    normalized_computed = {k: v for k, v in computed_counts.items() if v}
                    if normalized_declared != normalized_computed:
                        errors.append(
                            f"{prefix}: counts_by_review_state {declared_counts} does not match "
                            f"the actual distribution of referenced review_event_ids "
                            f"{computed_counts}"
                        )

    errors.extend(_find_supersedes_cycles(events))

    return errors, warnings


def _find_supersedes_cycles(events: list[EventEnvelope]) -> list[str]:
    """Detect cycles in the `supersedes_event_ids` graph (Phase 1.9 rule 5)."""
    graph: dict[str, list[str]] = {}
    for event in events:
        target_ids = event.payload.get("supersedes_event_ids")
        if isinstance(target_ids, list):
            graph[event.id] = [t for t in target_ids if isinstance(t, str)]

    visited: set[str] = set()

    def _dfs(node: str, stack: frozenset[str]) -> bool:
        if node in stack:
            return True
        if node in visited:
            return False
        next_stack = stack | {node}
        for neighbor in graph.get(node, []):
            if _dfs(neighbor, next_stack):
                return True
        visited.add(node)
        return False

    for event_id in graph:
        if _dfs(event_id, frozenset()):
            return ["review_events: supersedes_event_ids graph contains a cycle"]
    return []
