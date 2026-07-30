"""Phase 3.16: non-semantic changed-region evidence lane.

Plain-English question this phase answers: **which region of a frame
changed most, without claiming what changed?**

Phase 3.15 (`visual_change.py`) can already say *when* two adjacent
stored keyframes visually differ. This module goes one step further and
says *where inside the frame* that difference is strongest -- a small
fixed grid (`GRID_ROWS` x `GRID_COLS`) laid over both images, a bounded
grayscale mean-absolute-difference computed per cell, and the strongest
cell/cluster reported as a pixel-space and normalized bounding box. It
never runs visual AI, never detects an object/face/person, never runs
OCR, never reads text, and never infers scene meaning, intent, or
emotion -- it only reports *where* the pixels differ most, numerically.

Core rule (restated from the phase brief, governs every check below):

    Changed-region evidence, not semantic interpretation.

This lane answers "which region of the frame changed most?" It does
NOT answer "what changed?", "who changed?", "what object changed?",
"what action happened?", or "what does the scene mean?".

Allowed claims: "changed region candidate", "strongest changed
region", "grid cell change", "bounding box of visual difference",
"localized change", "global change", "region delta score", "linked
visual change candidate", "linked source/target keyframes". Forbidden
claims: anything naming an object/person/face/action, or asserting
what the changed pixels represent (see
`FORBIDDEN_CHANGED_REGION_PHRASES` below).

Track naming: this lane's track is named `changed_region_candidates`
(`tracks/changed_region_candidates.jsonl`), independent of both the
Phase 2.15 `visual_change_events` ffmpeg-`scdet` lane and the Phase
3.15 `visual_change_candidates` lane.

Design note -- source of truth for `analyze`: a changed-region analysis
run is computed entirely from the already-stored
`visual_change_candidates` track (Phase 3.15's output), not from the
raw `keyframes` track directly. Every visual change candidate record
already carries its linked `source_keyframe_id`/`target_keyframe_id`
and `source_image_path`/`target_image_path`, so re-deriving those from
`keyframes` would be redundant. This also means `changed-regions
analyze` requires `clulatent visual-change analyze` to have already run
successfully on the package.

Optional dependency: computing the actual grid-difference metrics
requires Pillow (extra `visual`, `pip install "clu-latent[visual]"`),
exactly like `visual_change.py`. Importing this module never imports
Pillow -- that only happens inside `compute_changed_region_metrics`/
`compute_changed_region_events`. Every read-only `changed-regions`
command (`get`, `query-time`, `query-visual-change`, `summary`) never
needs Pillow at all -- they only read already-computed records.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix
from .visual_change import strength_for_normalized_delta

# Placeholder for the optional Pillow symbol. Stays None until
# `_ensure_engine_loaded()` binds it on first real use (or a test
# replaces it via monkeypatch). Importing this module never imports
# PIL.
Image = None


class ChangedRegionUnavailableError(RuntimeError):
    """Raised when changed-region analysis is requested but Pillow is not installed."""


class ChangedRegionComputeError(ValueError):
    """Raised when changed-region evidence cannot be safely computed (unsafe/missing path, unreadable image, no visual change candidates)."""


def is_available() -> bool:
    """True if the optional `Pillow` dependency is installed.

    Uses `importlib.util.find_spec` so merely asking whether Pillow is
    present never imports it as a side effect.
    """
    import importlib.util

    return importlib.util.find_spec("PIL") is not None


def get_engine_version() -> str:
    """Return the installed Pillow version, or "unknown" if not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("Pillow")
    except PackageNotFoundError:
        return "unknown"


def _ensure_engine_loaded() -> None:
    """Import Pillow on first use, binding the module-level `Image` name."""
    global Image
    if Image is not None:
        return
    if not is_available():
        raise ChangedRegionUnavailableError(
            "Pillow is not installed. Install the optional 'visual' extra "
            "(pip install \"clu-latent[visual]\") to run "
            "`clulatent changed-regions analyze`."
        )
    from PIL import Image as _Image

    Image = _Image


# --- Record type catalog ----------------------------------------------------

CHANGED_REGION_RECORD_TYPE = "changed_region_candidate"
SUPPORTED_CHANGED_REGION_TYPES = frozenset({CHANGED_REGION_RECORD_TYPE})

CHANGED_REGION_STRENGTHS: tuple[str, ...] = ("low", "medium", "high")
SUPPORTED_CHANGED_REGION_STRENGTHS = frozenset(CHANGED_REGION_STRENGTHS)

CHANGE_SCOPES: tuple[str, ...] = ("localized", "distributed", "global", "unknown")
SUPPORTED_CHANGE_SCOPES = frozenset(CHANGE_SCOPES)

# Fixed, documented caveats every changed_region_candidate record
# carries. Not user-suppliable -- always exactly this pair, written by
# `compute_changed_region_events` and re-checked as a required,
# unmodified field by the validator below, so a hand-edited record
# cannot drop or soften them.
CHANGED_REGION_CAVEATS: tuple[str, ...] = (
    "Changed-region evidence, not semantic interpretation.",
    "Does not identify objects, people, text, actions, intent, or scene meaning.",
)

CHANGED_REGION_METHOD = "pillow_grayscale_grid_mean_abs_diff"

# --- Bounded computation constants ------------------------------------------

# A small, fixed grid laid over both images. Both images are downscaled
# to `_GRID_THUMBNAIL_SIZE` (evenly divisible by the grid) before any
# per-cell comparison, so compute cost is bounded regardless of the
# source keyframe resolution -- a 4K keyframe costs exactly the same to
# compare as a 240p one.
GRID_ROWS = 8
GRID_COLS = 8
_GRID_THUMBNAIL_SIZE = (64, 64)  # divisible by GRID_ROWS/GRID_COLS -> 8x8px cells

# A cell is included in the selected cluster if its delta is within
# this fraction of the single strongest cell's delta. Fixed, documented
# threshold -- not a learned/tuned model.
SELECTED_CELL_RATIO = 0.8

# `change_scope` classification thresholds. The "no meaningful change"
# check is deliberately based on the *strongest selected region's* own
# delta (`region_normalized_delta`), not the whole-frame average
# (`frame_normalized_delta`): a small, intense, truly localized change
# (e.g. one bright cell in an otherwise static frame) can be diluted to
# a tiny frame-average delta by the many unchanged cells around it, but
# the region itself is still real, meaningful change -- averaging it
# away would misclassify a strongly localized change as "unknown".
# Fixed, documented thresholds, matching the rest of the codebase's
# "small, fixed threshold, not a heuristic classifier" convention (see
# `visual_change.VISUAL_CHANGE_LOW_THRESHOLD`).
CHANGE_SCOPE_UNKNOWN_REGION_DELTA_THRESHOLD = 0.02
CHANGE_SCOPE_GLOBAL_CELL_RATIO = 0.5
CHANGE_SCOPE_LOCALIZED_CELL_RATIO = 0.15

# Safety cap on the number of visual change candidates processed in one
# `analyze` call, so a pathological package (an unbounded visual change
# track) cannot make one call do unbounded work.
DEFAULT_MAX_CHANGED_REGION_PAIRS = 5000


def classify_change_scope(region_normalized_delta: float, selected_cell_ratio: float) -> str:
    """Bucket a region-level delta + selected-cell ratio into a `change_scope`.

    "unknown" when even the strongest region barely changed (nothing
    meaningful to localize); otherwise "global" when most of the grid
    was selected, "localized" when only a small cluster was, and
    "distributed" in between.
    """
    if region_normalized_delta < CHANGE_SCOPE_UNKNOWN_REGION_DELTA_THRESHOLD:
        return "unknown"
    if selected_cell_ratio >= CHANGE_SCOPE_GLOBAL_CELL_RATIO:
        return "global"
    if selected_cell_ratio <= CHANGE_SCOPE_LOCALIZED_CELL_RATIO:
        return "localized"
    return "distributed"


# --- Conservative-language enforcement --------------------------------------

# Phase 3.16 forbidden-claims list, taken directly from the phase
# brief. Checked (case-insensitively) as a substring against any
# free-text field a record carries. This lane's own writer never
# produces free text other than the fixed `CHANGED_REGION_CAVEATS`
# above, so this check exists as defense-in-depth against a
# hand-edited record, mirroring `visual_change._check_conservative_
# language`.
FORBIDDEN_CHANGED_REGION_PHRASES: frozenset[str] = frozenset(
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
        "the clip shows",
        "the model understands",
        "clulatent understands the scene",
        "clulatent understands visuals",
    }
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _check_conservative_language(value: str, field_name: str, errors: list[str]) -> None:
    lowered = value.lower()
    for phrase in FORBIDDEN_CHANGED_REGION_PHRASES:
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


def _check_non_negative_int(value: Any, field_name: str, *, errors: list[str], allow_zero: bool = True) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{field_name} must be an integer, got {type(value).__name__}")
        return
    minimum = 0 if allow_zero else 1
    if value < minimum:
        errors.append(f"{field_name} must be >= {minimum}, got {value!r}")


_REGION_KEYS = frozenset({"x", "y", "width", "height", "coordinate_system"})


def _check_pixel_region(value: Any, field_name: str, *, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    for key in value:
        if key not in _REGION_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")

    _check_non_negative_int(value.get("x"), f"{field_name}.x", errors=errors)
    _check_non_negative_int(value.get("y"), f"{field_name}.y", errors=errors)
    _check_non_negative_int(value.get("width"), f"{field_name}.width", errors=errors, allow_zero=False)
    _check_non_negative_int(value.get("height"), f"{field_name}.height", errors=errors, allow_zero=False)

    coordinate_system = value.get("coordinate_system")
    if coordinate_system != "pixel":
        errors.append(f"{field_name}.coordinate_system must be 'pixel', got {coordinate_system!r}")


def _check_normalized_region(value: Any, field_name: str, *, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    for key in value:
        if key not in _REGION_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")

    for sub_field in ("x", "y", "width", "height"):
        _check_unit_interval(value.get(sub_field), f"{field_name}.{sub_field}", errors=errors)

    x, y, width, height = (value.get("x"), value.get("y"), value.get("width"), value.get("height"))
    all_numeric = all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y, width, height)
    )
    if all_numeric:
        # Allow a small floating-point rounding tolerance rather than a
        # hard 1.0 cutoff.
        if float(x) + float(width) > 1.0 + 1e-6:
            errors.append(f"{field_name}: x + width exceeds 1.0 (got {x!r} + {width!r})")
        if float(y) + float(height) > 1.0 + 1e-6:
            errors.append(f"{field_name}: y + height exceeds 1.0 (got {y!r} + {height!r})")

    coordinate_system = value.get("coordinate_system")
    if coordinate_system != "normalized_0_1":
        errors.append(
            f"{field_name}.coordinate_system must be 'normalized_0_1', got {coordinate_system!r}"
        )


_GRID_KEYS = frozenset({"rows", "cols", "selected_cells"})


def _check_grid(value: Any, field_name: str, *, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    for key in value:
        if key not in _GRID_KEYS:
            errors.append(f"{field_name}: unexpected field {key!r}")

    rows = value.get("rows")
    cols = value.get("cols")
    if rows != GRID_ROWS:
        errors.append(f"{field_name}.rows must be exactly {GRID_ROWS}, got {rows!r}")
    if cols != GRID_COLS:
        errors.append(f"{field_name}.cols must be exactly {GRID_COLS}, got {cols!r}")

    selected_cells = value.get("selected_cells")
    if not isinstance(selected_cells, list) or not selected_cells:
        errors.append(f"{field_name}.selected_cells must be a non-empty array")
        return
    if len(selected_cells) > GRID_ROWS * GRID_COLS:
        errors.append(
            f"{field_name}.selected_cells has more entries ({len(selected_cells)}) than "
            f"grid cells ({GRID_ROWS * GRID_COLS})"
        )
    row_bound = rows if isinstance(rows, int) and not isinstance(rows, bool) else GRID_ROWS
    col_bound = cols if isinstance(cols, int) and not isinstance(cols, bool) else GRID_COLS
    for index, cell in enumerate(selected_cells):
        if (
            not isinstance(cell, list)
            or len(cell) != 2
            or not all(isinstance(c, int) and not isinstance(c, bool) for c in cell)
        ):
            errors.append(f"{field_name}.selected_cells[{index}] must be a [row, col] integer pair")
            continue
        row, col = cell
        if not (0 <= row < row_bound):
            errors.append(f"{field_name}.selected_cells[{index}]: row {row} out of bounds")
        if not (0 <= col < col_bound):
            errors.append(f"{field_name}.selected_cells[{index}]: col {col} out of bounds")


_METRICS_KEYS = frozenset(
    {
        "region_mean_absolute_difference",
        "region_normalized_delta",
        "frame_normalized_delta",
        "region_to_frame_ratio",
    }
)

# `region_to_frame_ratio` has no natural fixed upper bound (it is a
# ratio of two bounded deltas), but a hand-edited or corrupted record
# should still be rejected if it carries an absurd/non-finite value.
_MAX_REGION_TO_FRAME_RATIO = 1000.0


def _check_metrics(value: Any, field_name: str, *, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return
    for key in value:
        if key not in _METRICS_KEYS:
            errors.append(f"{field_name}: unexpected metric key {key!r}")

    mad = value.get("region_mean_absolute_difference")
    if isinstance(mad, bool) or not isinstance(mad, (int, float)):
        errors.append(f"{field_name}.region_mean_absolute_difference must be a number")
    elif not (0.0 <= float(mad) <= 255.0):
        errors.append(
            f"{field_name}.region_mean_absolute_difference must be within [0.0, 255.0], got {mad!r}"
        )

    _check_unit_interval(
        value.get("region_normalized_delta"), f"{field_name}.region_normalized_delta", errors=errors
    )
    _check_unit_interval(
        value.get("frame_normalized_delta"), f"{field_name}.frame_normalized_delta", errors=errors
    )

    ratio = value.get("region_to_frame_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
        errors.append(f"{field_name}.region_to_frame_ratio must be a number")
    elif not (0.0 <= float(ratio) <= _MAX_REGION_TO_FRAME_RATIO):
        errors.append(
            f"{field_name}.region_to_frame_ratio must be within [0.0, {_MAX_REGION_TO_FRAME_RATIO}], "
            f"got {ratio!r}"
        )


_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "visual_change_id",
        "source_keyframe_id",
        "target_keyframe_id",
        "source_image_path",
        "target_image_path",
        "region",
        "normalized_region",
        "grid",
        "metrics",
        "change_scope",
        "strength",
        "caveats",
        "method",
        "created_by",
    }
)


def validate_changed_region_event(
    event: Any,
    *,
    package_root: Path | None = None,
    known_visual_change_ids: set[str] | None = None,
    known_keyframe_ids: set[str] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `changed_region_candidate` record dict.

    Returns `(errors, warnings)`; never raises. Checks the shared
    envelope fields, the closed payload whitelist, the pixel/normalized
    region shapes (including that a normalized region stays within
    `[0, 1]`), the fixed 8x8 grid shape, bounded metric ranges, a
    supported `strength` and `change_scope` value, and that the fixed
    `CHANGED_REGION_CAVEATS` pair is present unmodified. When
    `package_root` is given, both image paths are additionally checked
    for containment/symlink safety. When `known_visual_change_ids` /
    `known_keyframe_ids` are given, the record's linked ids are checked
    for membership (a package-level cross-check `clulatent validate`
    performs; skipped when the caller doesn't supply the sets, e.g. when
    validating one record in isolation).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(event, dict):
        return [f"event must be an object, got {type(event).__name__}"], warnings

    _check_bounded_string(
        event.get("id"), "id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )

    event_type = event.get("type")
    if event_type != CHANGED_REGION_RECORD_TYPE:
        errors.append(f"type must be {CHANGED_REGION_RECORD_TYPE!r}, got {event_type!r}")

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
        payload.get("visual_change_id"),
        "payload.visual_change_id",
        max_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
    )
    if known_visual_change_ids is not None:
        visual_change_id = payload.get("visual_change_id")
        if isinstance(visual_change_id, str) and visual_change_id not in known_visual_change_ids:
            errors.append(
                f"payload.visual_change_id {visual_change_id!r} does not match any record in "
                "tracks/visual_change_candidates.jsonl"
            )

    for id_field in ("source_keyframe_id", "target_keyframe_id"):
        _check_bounded_string(
            payload.get(id_field),
            f"payload.{id_field}",
            max_bytes=limits.max_analysis_id_bytes,
            required=True,
            errors=errors,
        )
        if known_keyframe_ids is not None:
            keyframe_id = payload.get(id_field)
            if isinstance(keyframe_id, str) and keyframe_id not in known_keyframe_ids:
                errors.append(
                    f"payload.{id_field} {keyframe_id!r} does not match any record in tracks/keyframes.jsonl"
                )

    _check_path_field(
        payload.get("source_image_path"),
        "payload.source_image_path",
        package_root=package_root,
        errors=errors,
    )
    _check_path_field(
        payload.get("target_image_path"),
        "payload.target_image_path",
        package_root=package_root,
        errors=errors,
    )

    _check_pixel_region(payload.get("region"), "payload.region", errors=errors)
    _check_normalized_region(payload.get("normalized_region"), "payload.normalized_region", errors=errors)
    _check_grid(payload.get("grid"), "payload.grid", errors=errors)
    _check_metrics(payload.get("metrics"), "payload.metrics", errors=errors)

    change_scope = payload.get("change_scope")
    if change_scope not in SUPPORTED_CHANGE_SCOPES:
        errors.append(
            f"payload.change_scope must be one of {sorted(SUPPORTED_CHANGE_SCOPES)}, got {change_scope!r}"
        )

    strength = payload.get("strength")
    if strength not in SUPPORTED_CHANGED_REGION_STRENGTHS:
        errors.append(
            f"payload.strength must be one of {sorted(SUPPORTED_CHANGED_REGION_STRENGTHS)}, got {strength!r}"
        )

    # Caveats are required to be exactly the fixed, pre-approved pair
    # above (not checked against `FORBIDDEN_CHANGED_REGION_PHRASES`:
    # that list is substring-based and the approved caveat text itself
    # uses words like "intent" and "text" in a *negation* ("does not
    # identify... text... intent... or scene meaning") -- exact-equality
    # is the correct and sufficient guard here, since it structurally
    # forbids any smuggled or softened wording, mirroring
    # `visual_change.validate_visual_change_event`'s identical caveat
    # check.
    caveats = payload.get("caveats")
    if caveats != list(CHANGED_REGION_CAVEATS):
        errors.append("payload.caveats must be exactly the fixed changed-region caveat pair")

    _check_bounded_string(
        payload.get("method"),
        "payload.method",
        max_bytes=limits.max_analysis_label_bytes,
        required=True,
        errors=errors,
    )
    _check_bounded_string(
        payload.get("created_by"),
        "payload.created_by",
        max_bytes=limits.max_analysis_label_bytes,
        required=True,
        errors=errors,
    )

    # Defense-in-depth against a hand-edited record: this lane's own
    # writer only ever puts a fixed method string and a plain
    # "<tool_name> <tool_version>" string into these two fields, but a
    # hand-edited record could try to smuggle a semantic claim into
    # either. Not applied to `caveats` above -- see the comment there.
    for field_name in ("method", "created_by"):
        value = payload.get(field_name)
        if isinstance(value, str):
            _check_conservative_language(value, f"payload.{field_name}", errors)

    return errors, warnings


def validate_changed_region_track(
    events: Any,
    *,
    package_root: Path | None = None,
    known_visual_change_ids: set[str] | None = None,
    known_keyframe_ids: set[str] | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a whole candidate batch of `changed_region_candidate` records.

    Runs `validate_changed_region_event` on every record (prefixing
    errors with `[index] (id)`) and additionally rejects a duplicate id
    *within the batch*. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(events, list):
        return [f"events must be an array, got {type(events).__name__}"], warnings

    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("id") if isinstance(event, dict) else None
        prefix = f"[{index}] ({event_id!r})"
        event_errors, event_warnings = validate_changed_region_event(
            event,
            package_root=package_root,
            known_visual_change_ids=known_visual_change_ids,
            known_keyframe_ids=known_keyframe_ids,
            limits=limits,
        )
        errors.extend(f"{prefix}: {msg}" for msg in event_errors)
        warnings.extend(f"{prefix}: {msg}" for msg in event_warnings)

        if isinstance(event_id, str):
            if event_id in seen_ids:
                errors.append(f"{prefix}: duplicate id {event_id!r} within this batch")
            seen_ids.add(event_id)

    return errors, warnings


_RECEIPT_STATUSES = frozenset({"success", "failure"})


def validate_changed_region_receipt(
    record: Any, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], list[str]]:
    """Validate one raw `receipts/changed_region.jsonl` entry. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(record, dict):
        return [f"receipt must be an object, got {type(record).__name__}"], warnings

    if record.get("status") not in _RECEIPT_STATUSES:
        errors.append(
            f"receipt.status must be one of {sorted(_RECEIPT_STATUSES)}, got {record.get('status')!r}"
        )
    for field_name in ("operation", "tool_name", "tool_version", "output_track"):
        _check_bounded_string(
            record.get(field_name),
            f"receipt.{field_name}",
            max_bytes=limits.max_analysis_label_bytes,
            required=True,
            errors=errors,
        )
    _check_bounded_string(
        record.get("failure_details"),
        "receipt.failure_details",
        max_bytes=limits.max_analysis_text_bytes,
        required=False,
        errors=errors,
    )
    return errors, warnings


def validate_changed_region_receipts(
    records: Any, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], list[str]]:
    """Validate every entry of a raw `receipts/changed_region.jsonl` file. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(records, list):
        return [f"receipts must be an array, got {type(records).__name__}"], warnings
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_changed_region_receipt(record, limits=limits)
        errors.extend(f"[{index}]: {msg}" for msg in record_errors)
        warnings.extend(f"[{index}]: {msg}" for msg in record_warnings)
    return errors, warnings


# --- Bounded, non-semantic grid-difference computation ----------------------


def _load_grayscale_for_grid(image_path: Path) -> tuple[tuple[int, int], Any]:
    """Open `image_path` and return `(original_size, resized_grayscale_thumbnail)`.

    Raises `ChangedRegionComputeError` for any unreadable/corrupt image
    file, or one with a zero-area size -- never silently substitutes a
    blank image.
    """
    _ensure_engine_loaded()
    try:
        with Image.open(image_path) as source:
            original_size = source.size
            resized = source.convert("L").resize(_GRID_THUMBNAIL_SIZE)
            resized.load()
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise ChangedRegionComputeError(f"could not read image {image_path}: {exc}") from exc

    width, height = original_size
    if width <= 0 or height <= 0:
        raise ChangedRegionComputeError(f"image has invalid dimensions: {image_path}")
    return original_size, resized


def compute_changed_region_metrics(source_path: Path, target_path: Path) -> dict[str, Any]:
    """Compute a bounded, non-semantic changed-region record between two images.

    Both images are downscaled to a small fixed `GRID_ROWS` x
    `GRID_COLS` grid before any comparison, so compute cost is bounded
    regardless of source resolution. Returns a dict with `region`
    (pixel-space bounding box, scaled back to the source image's actual
    dimensions), `normalized_region` (`[0, 1]`-space bounding box),
    `grid` (`rows`, `cols`, `selected_cells`), `metrics`
    (`region_mean_absolute_difference`, `region_normalized_delta`,
    `frame_normalized_delta`, `region_to_frame_ratio`), and
    `change_scope`/`strength` classifications. Purely numeric -- no
    object/scene/label output of any kind. Requires Pillow (raises
    `ChangedRegionUnavailableError` if not installed).
    """
    source_size, source_thumb = _load_grayscale_for_grid(source_path)
    _target_size, target_thumb = _load_grayscale_for_grid(target_path)

    thumb_w, _thumb_h = _GRID_THUMBNAIL_SIZE
    cell_w = thumb_w // GRID_COLS
    cell_h = _GRID_THUMBNAIL_SIZE[1] // GRID_ROWS

    source_pixels = list(source_thumb.getdata())
    target_pixels = list(target_thumb.getdata())

    cell_deltas: list[list[float]] = [[0.0] * GRID_COLS for _ in range(GRID_ROWS)]
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            total = 0
            for dy in range(cell_h):
                py = row * cell_h + dy
                base = py * thumb_w
                for dx in range(cell_w):
                    px = col * cell_w + dx
                    idx = base + px
                    total += abs(source_pixels[idx] - target_pixels[idx])
            cell_deltas[row][col] = total / (cell_w * cell_h)

    frame_mad = sum(sum(row) for row in cell_deltas) / (GRID_ROWS * GRID_COLS)
    frame_normalized_delta = frame_mad / 255.0

    max_delta = max(max(row) for row in cell_deltas)
    if max_delta <= 0:
        selected_cells = [[0, 0]]
    else:
        threshold = max_delta * SELECTED_CELL_RATIO
        selected_cells = [
            [row, col]
            for row in range(GRID_ROWS)
            for col in range(GRID_COLS)
            if cell_deltas[row][col] >= threshold
        ]

    rows_sel = [cell[0] for cell in selected_cells]
    cols_sel = [cell[1] for cell in selected_cells]
    min_row, max_row = min(rows_sel), max(rows_sel)
    min_col, max_col = min(cols_sel), max(cols_sel)

    region_mad = sum(cell_deltas[row][col] for row, col in selected_cells) / len(selected_cells)
    region_normalized_delta = region_mad / 255.0
    region_to_frame_ratio = (
        region_normalized_delta / frame_normalized_delta if frame_normalized_delta > 0 else 0.0
    )

    src_w, src_h = source_size
    px_cell_w = src_w / GRID_COLS
    px_cell_h = src_h / GRID_ROWS

    x = int(round(min_col * px_cell_w))
    y = int(round(min_row * px_cell_h))
    width = int(round((max_col - min_col + 1) * px_cell_w))
    height = int(round((max_row - min_row + 1) * px_cell_h))

    # Clamp against rounding drift so the box always stays within the
    # source image's actual pixel bounds.
    x = max(0, min(x, src_w - 1))
    y = max(0, min(y, src_h - 1))
    width = max(1, min(width, src_w - x))
    height = max(1, min(height, src_h - y))

    region = {"x": x, "y": y, "width": width, "height": height, "coordinate_system": "pixel"}
    normalized_region = {
        "x": round(x / src_w, 6),
        "y": round(y / src_h, 6),
        "width": round(width / src_w, 6),
        "height": round(height / src_h, 6),
        "coordinate_system": "normalized_0_1",
    }

    selected_cell_ratio = len(selected_cells) / (GRID_ROWS * GRID_COLS)
    change_scope = classify_change_scope(region_normalized_delta, selected_cell_ratio)
    strength = strength_for_normalized_delta(region_normalized_delta)

    return {
        "region": region,
        "normalized_region": normalized_region,
        "grid": {"rows": GRID_ROWS, "cols": GRID_COLS, "selected_cells": selected_cells},
        "metrics": {
            "region_mean_absolute_difference": round(region_mad, 4),
            "region_normalized_delta": round(region_normalized_delta, 4),
            "frame_normalized_delta": round(frame_normalized_delta, 4),
            "region_to_frame_ratio": round(region_to_frame_ratio, 4),
        },
        "change_scope": change_scope,
        "strength": strength,
    }


def compute_changed_region_events(
    package_root: Path | str,
    *,
    tool_name: str,
    tool_version: str,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Compute `changed_region_candidate` records for every stored visual change candidate.

    Loads the package's `visual_change_candidates` track (via
    `visual_change_retrieval.load_visual_change_events`, so its image
    paths are already containment/symlink-checked), then for each
    record computes a bounded, non-semantic grid-difference region
    between its linked source/target images. Returns plain, JSONL-safe
    dicts -- not yet validated or written; the caller
    (`changed_region_writer.analyze_changed_regions`) is responsible for
    that.

    Raises `ChangedRegionComputeError` if:
      - the package has no visual change candidates, or the track is
        empty (nothing to localize -- run `clulatent visual-change
        analyze` first).
      - more than `DEFAULT_MAX_CHANGED_REGION_PAIRS` visual change
        candidates would be processed (safety cap on unbounded work).
      - a linked keyframe's image file is missing from disk or
        unreadable.

    Also propagates `visual_change_retrieval.VisualChangeRetrievalError`
    (e.g. an unsafe/escaping stored image path, an oversized/malformed
    track) and `ChangedRegionUnavailableError` (Pillow not installed)
    unchanged -- both are already clean, specific errors.
    """
    from . import visual_change_retrieval as visual_change_retrieval_mod

    package_root = Path(package_root)
    try:
        visual_change_events = visual_change_retrieval_mod.load_visual_change_events(
            package_root, limits=limits
        )
    except visual_change_retrieval_mod.VisualChangeRetrievalError as exc:
        raise ChangedRegionComputeError(str(exc)) from exc

    if not visual_change_events:
        raise ChangedRegionComputeError(
            "changed-region analysis requires at least 1 stored visual change candidate; "
            "run `clulatent visual-change analyze` first"
        )

    if len(visual_change_events) > DEFAULT_MAX_CHANGED_REGION_PAIRS:
        raise ChangedRegionComputeError(
            f"{len(visual_change_events)} visual change candidates exceeds the "
            f"{DEFAULT_MAX_CHANGED_REGION_PAIRS}-record bound for one analyze call"
        )

    _ensure_engine_loaded()

    created_by = f"{tool_name} {tool_version}"
    events: list[dict[str, Any]] = []
    for index, vc_event in enumerate(visual_change_events):
        vc_payload = vc_event.get("payload")
        vc_payload = vc_payload if isinstance(vc_payload, dict) else {}
        vc_id = vc_event.get("id")
        source_path = vc_payload.get("source_image_path")
        target_path = vc_payload.get("target_image_path")
        if not source_path or not target_path:
            raise ChangedRegionComputeError(
                f"visual change candidate {vc_id!r} has no recorded image path"
            )

        try:
            source_abs = resolve_in_package(
                package_root, source_path, field_name="visual_change.payload.source_image_path"
            )
            target_abs = resolve_in_package(
                package_root, target_path, field_name="visual_change.payload.target_image_path"
            )
        except PathSecurityError as exc:
            raise ChangedRegionComputeError(str(exc)) from exc
        if not source_abs.is_file():
            raise ChangedRegionComputeError(f"keyframe image file is missing: {source_path}")
        if not target_abs.is_file():
            raise ChangedRegionComputeError(f"keyframe image file is missing: {target_path}")

        region_data = compute_changed_region_metrics(source_abs, target_abs)

        t_start = vc_event.get("t_start_ms", 0)
        t_end = vc_event.get("t_end_ms", t_start)

        events.append(
            {
                "id": f"cr_{index:06d}",
                "type": CHANGED_REGION_RECORD_TYPE,
                "t_start_ms": t_start,
                "t_end_ms": t_end,
                "producer": {"name": tool_name, "version": tool_version},
                "payload": {
                    "visual_change_id": vc_id,
                    "source_keyframe_id": vc_payload.get("source_keyframe_id"),
                    "target_keyframe_id": vc_payload.get("target_keyframe_id"),
                    "source_image_path": source_path,
                    "target_image_path": target_path,
                    "region": region_data["region"],
                    "normalized_region": region_data["normalized_region"],
                    "grid": region_data["grid"],
                    "metrics": region_data["metrics"],
                    "change_scope": region_data["change_scope"],
                    "strength": region_data["strength"],
                    "caveats": list(CHANGED_REGION_CAVEATS),
                    "method": CHANGED_REGION_METHOD,
                    "created_by": created_by,
                },
            }
        )

    return events
