"""Phase 3.15: non-semantic visual change evidence lane.

Plain-English question this phase answers: **can CLULatent detect
where the video visually changes, without claiming what the scene
means?**

This module defines the `visual_change_candidate` record shape (schema
+ non-raising `(errors, warnings)` validators, mirroring
`audio_digest.py`'s convention) and the bounded, non-semantic
pixel-difference computation between *adjacent stored keyframes* that
produces candidate records. It never runs visual AI, never detects an
object/face/person, never runs OCR, never captions a frame, and never
infers scene meaning, intent, or emotion -- it only measures how much
two already-stored keyframe images differ, numerically, and reports
that difference as a hedged "candidate," never a fact about the scene.

Core rule (restated from the phase brief, governs every check below):

    Visual change evidence, not semantic interpretation.

Allowed claims: "visual change candidate", "frame difference score",
"possible boundary", "low/medium/high visual delta", "linked
keyframes". Forbidden claims: anything asserting what a frame shows,
who/what is in it, or what a viewer would feel (see
`FORBIDDEN_VISUAL_CHANGE_PHRASES` below).

Track naming note: this lane's track is named
`visual_change_candidates` (**not** `visual_change_events`) because
`visual_change_events` already exists as the unrelated Phase 2.15
ffmpeg-`scdet` analysis lane (`analysis_ffmpeg_visual_change_adapter
.py`). The two lanes are independent and never share a track file.

Optional dependency: computing the actual pixel-difference metrics
requires Pillow (extra `visual`, `pip install "clu-latent[visual]"`).
Importing this module never imports Pillow -- that only happens inside
`compute_visual_change_metrics`/`compute_visual_change_events` (i.e.
only when `clulatent visual-change analyze` actually runs), mirroring
`vad.py`/`transcribe.py`'s lazy-import convention for optional ML
dependencies. Every read-only `visual-change` command (`get`,
`query-time`, `summary`) never needs Pillow at all -- they only read
already-computed records.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package, validate_relative_posix

# Placeholder for the optional Pillow symbol. Stays None until
# `_ensure_engine_loaded()` binds it on first real use (or a test
# replaces it via monkeypatch). Importing this module never imports
# PIL.
Image = None


class VisualChangeUnavailableError(RuntimeError):
    """Raised when visual-change analysis is requested but Pillow is not installed."""


class VisualChangeComputeError(ValueError):
    """Raised when adjacent keyframe images cannot be safely compared (unsafe/missing path, unreadable image)."""


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
        raise VisualChangeUnavailableError(
            "Pillow is not installed. Install the optional 'visual' extra "
            "(pip install \"clu-latent[visual]\") to run "
            "`clulatent visual-change analyze`."
        )
    from PIL import Image as _Image

    Image = _Image


# --- Record type catalog ----------------------------------------------------

VISUAL_CHANGE_RECORD_TYPE = "visual_change_candidate"
SUPPORTED_VISUAL_CHANGE_TYPES = frozenset({VISUAL_CHANGE_RECORD_TYPE})

VISUAL_CHANGE_STRENGTHS: tuple[str, ...] = ("low", "medium", "high")
SUPPORTED_VISUAL_CHANGE_STRENGTHS = frozenset(VISUAL_CHANGE_STRENGTHS)

# Fixed, documented caveats every visual_change_candidate record carries.
# Not user-suppliable -- always exactly this pair, written by
# `compute_visual_change_events` and re-checked as a required, unmodified
# field by the validator below, so a hand-edited record cannot drop or
# soften them.
VISUAL_CHANGE_CAVEATS: tuple[str, ...] = (
    "Visual change evidence, not semantic interpretation.",
    "Does not identify objects, people, actions, intent, or scene meaning.",
)

VISUAL_CHANGE_METHOD = "pillow_grayscale_thumbnail_mean_abs_diff"

# --- Bounded computation constants ------------------------------------------

# Both images are downscaled to this fixed thumbnail size before any
# pixel comparison, so compute cost (and therefore wall-clock time) is
# bounded regardless of the source keyframe resolution -- a 4K keyframe
# costs exactly the same to compare as a 240p one.
_THUMBNAIL_SIZE = (64, 64)
_THUMBNAIL_PIXEL_COUNT = _THUMBNAIL_SIZE[0] * _THUMBNAIL_SIZE[1]

# Perceptual (average) hash side length -- a small, fixed 8x8 grid,
# giving a 64-bit hash and a Hamming distance bounded to [0, 64].
_PHASH_SIZE = (8, 8)

# Strength buckets over `normalized_delta` (mean absolute grayscale
# difference / 255, bounded to [0.0, 1.0]). Fixed, documented
# thresholds, not a learned/tuned model -- deliberately conservative
# and predictable, matching the rest of the codebase's "small, fixed
# threshold, not a heuristic classifier" convention (e.g.
# `audio_digest.FORBIDDEN_AUDIO_DIGEST_PHRASES`,
# `constants.EVENT_DURATION_TOLERANCE_MS`).
VISUAL_CHANGE_LOW_THRESHOLD = 0.10
VISUAL_CHANGE_HIGH_THRESHOLD = 0.30

# Safety cap on the number of adjacent keyframe pairs analyzed in one
# `analyze` call, so a pathological package (an unbounded keyframes
# track) cannot make one call do unbounded work.
DEFAULT_MAX_VISUAL_CHANGE_PAIRS = 5000


def strength_for_normalized_delta(normalized_delta: float) -> str:
    """Bucket a `[0.0, 1.0]` normalized delta into "low"/"medium"/"high"."""
    if normalized_delta < VISUAL_CHANGE_LOW_THRESHOLD:
        return "low"
    if normalized_delta < VISUAL_CHANGE_HIGH_THRESHOLD:
        return "medium"
    return "high"


# --- Conservative-language enforcement --------------------------------------

# Phase 3.15 forbidden-claims list, taken directly from the phase
# brief. Checked (case-insensitively) as a substring against any
# free-text field a record carries. This lane's own writer never
# produces free text other than the fixed `VISUAL_CHANGE_CAVEATS`
# above, so this check exists as defense-in-depth against a
# hand-edited record, mirroring `audio_digest._check_conservative_
# language`.
FORBIDDEN_VISUAL_CHANGE_PHRASES: frozenset[str] = frozenset(
    {
        "a person enters",
        "the scene shows",
        "the clip means",
        "tension",
        "intent",
        "emotion",
        "the model understands the scene",
        "clulatent understands the scene",
        "clulatent understands visuals",
    }
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _check_conservative_language(value: str, field_name: str, errors: list[str]) -> None:
    lowered = value.lower()
    for phrase in FORBIDDEN_VISUAL_CHANGE_PHRASES:
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


def _check_metrics(value: Any, field_name: str, *, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{field_name} must be an object")
        return

    mad = value.get("mean_absolute_difference")
    if isinstance(mad, bool) or not isinstance(mad, (int, float)):
        errors.append(f"{field_name}.mean_absolute_difference must be a number")
    elif not (0.0 <= float(mad) <= 255.0):
        errors.append(
            f"{field_name}.mean_absolute_difference must be within [0.0, 255.0], got {mad!r}"
        )

    _check_unit_interval(
        value.get("normalized_delta"), f"{field_name}.normalized_delta", errors=errors
    )

    phash = value.get("perceptual_hash_distance")
    if phash is not None:
        if isinstance(phash, bool) or not isinstance(phash, int):
            errors.append(f"{field_name}.perceptual_hash_distance must be an integer")
        elif not (0 <= phash <= 64):
            errors.append(
                f"{field_name}.perceptual_hash_distance must be within [0, 64], got {phash!r}"
            )

    allowed_metric_keys = {"mean_absolute_difference", "normalized_delta", "perceptual_hash_distance"}
    for key in value:
        if key not in allowed_metric_keys:
            errors.append(f"{field_name}: unexpected metric key {key!r}")


_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "source_keyframe_id",
        "target_keyframe_id",
        "source_image_path",
        "target_image_path",
        "metrics",
        "strength",
        "caveats",
        "method",
        "created_by",
    }
)


def validate_visual_change_event(
    event: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate one raw, untrusted `visual_change_candidate` record dict.

    Returns `(errors, warnings)`; never raises. Checks the shared
    envelope fields, the closed payload whitelist (rejects any
    unrecognized key), bounded metric ranges, a supported `strength`
    value, that the fixed `VISUAL_CHANGE_CAVEATS` pair is present
    unmodified, and (when `package_root` is given) that both image
    paths are containment/symlink-safe inside the package.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(event, dict):
        return [f"event must be an object, got {type(event).__name__}"], warnings

    _check_bounded_string(
        event.get("id"), "id", max_bytes=limits.max_analysis_id_bytes, required=True, errors=errors
    )

    event_type = event.get("type")
    if event_type != VISUAL_CHANGE_RECORD_TYPE:
        errors.append(
            f"type must be {VISUAL_CHANGE_RECORD_TYPE!r}, got {event_type!r}"
        )

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
        payload.get("source_keyframe_id"),
        "payload.source_keyframe_id",
        max_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
    )
    _check_bounded_string(
        payload.get("target_keyframe_id"),
        "payload.target_keyframe_id",
        max_bytes=limits.max_analysis_id_bytes,
        required=True,
        errors=errors,
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
    _check_metrics(payload.get("metrics"), "payload.metrics", errors=errors)

    strength = payload.get("strength")
    if strength not in SUPPORTED_VISUAL_CHANGE_STRENGTHS:
        errors.append(
            f"payload.strength must be one of {sorted(SUPPORTED_VISUAL_CHANGE_STRENGTHS)}, got {strength!r}"
        )

    # Caveats are required to be exactly the fixed, pre-approved pair
    # above (not checked against `FORBIDDEN_VISUAL_CHANGE_PHRASES`: that
    # list is substring-based and the approved caveat text itself uses
    # words like "intent" and "scene" in a *negation* ("does not
    # identify... intent... or scene meaning") -- exact-equality is the
    # correct and sufficient guard here, since it structurally forbids
    # any smuggled or softened wording).
    caveats = payload.get("caveats")
    if caveats != list(VISUAL_CHANGE_CAVEATS):
        errors.append(
            "payload.caveats must be exactly the fixed visual-change caveat pair"
        )

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
    # either. Not applied to `caveats` above -- those are already
    # constrained to exact equality against the fixed, pre-approved
    # pair, which is a strictly stronger guarantee (see the comment
    # above `caveats`).
    for field_name in ("method", "created_by"):
        value = payload.get(field_name)
        if isinstance(value, str):
            _check_conservative_language(value, f"payload.{field_name}", errors)

    return errors, warnings


def validate_visual_change_track(
    events: Any,
    *,
    package_root: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> tuple[list[str], list[str]]:
    """Validate a whole candidate batch of `visual_change_candidate` records.

    Runs `validate_visual_change_event` on every record (prefixing
    errors with `[index] (id)`) and additionally rejects a duplicate
    id *within the batch*. Never raises.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(events, list):
        return [f"events must be an array, got {type(events).__name__}"], warnings

    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        event_id = event.get("id") if isinstance(event, dict) else None
        prefix = f"[{index}] ({event_id!r})"
        event_errors, event_warnings = validate_visual_change_event(
            event, package_root=package_root, limits=limits
        )
        errors.extend(f"{prefix}: {msg}" for msg in event_errors)
        warnings.extend(f"{prefix}: {msg}" for msg in event_warnings)

        if isinstance(event_id, str):
            if event_id in seen_ids:
                errors.append(f"{prefix}: duplicate id {event_id!r} within this batch")
            seen_ids.add(event_id)

    return errors, warnings


_RECEIPT_STATUSES = frozenset({"success", "failure"})


def validate_visual_change_receipt(
    record: Any, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], list[str]]:
    """Validate one raw `receipts/visual_change.jsonl` entry. Never raises."""
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


def validate_visual_change_receipts(
    records: Any, *, limits: Limits = DEFAULT_LIMITS
) -> tuple[list[str], list[str]]:
    """Validate every entry of a raw `receipts/visual_change.jsonl` file. Never raises."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(records, list):
        return [f"receipts must be an array, got {type(records).__name__}"], warnings
    for index, record in enumerate(records):
        record_errors, record_warnings = validate_visual_change_receipt(record, limits=limits)
        errors.extend(f"[{index}]: {msg}" for msg in record_errors)
        warnings.extend(f"[{index}]: {msg}" for msg in record_warnings)
    return errors, warnings


# --- Bounded, non-semantic pixel-difference computation ---------------------


def _load_thumbnail_grayscale(image_path: Path, size: tuple[int, int]) -> Any:
    """Open `image_path` and return a resized grayscale ('L') image of `size`.

    Raises `VisualChangeComputeError` for any unreadable/corrupt image
    file -- never silently substitutes a blank image.
    """
    _ensure_engine_loaded()
    try:
        with Image.open(image_path) as source:
            return source.convert("L").resize(size)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean, module-specific error
        raise VisualChangeComputeError(f"could not read image {image_path}: {exc}") from exc


def _average_hash_bits(thumbnail: Any) -> int:
    pixels = list(thumbnail.getdata())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for pixel in pixels:
        bits = (bits << 1) | (1 if pixel >= mean else 0)
    return bits


def compute_visual_change_metrics(source_path: Path, target_path: Path) -> dict[str, Any]:
    """Compute bounded, non-semantic difference metrics between two images.

    Both images are downscaled to a small fixed thumbnail before any
    comparison, so compute cost is bounded regardless of source
    resolution. Returns a dict with `mean_absolute_difference`
    (`[0.0, 255.0]`, grayscale), `normalized_delta` (`[0.0, 1.0]`), and
    `perceptual_hash_distance` (`[0, 64]`, Hamming distance between two
    8x8 average hashes). Purely numeric -- no object/scene/label output
    of any kind. Requires Pillow (raises `VisualChangeUnavailableError`
    if not installed).
    """
    source_thumb = _load_thumbnail_grayscale(source_path, _THUMBNAIL_SIZE)
    target_thumb = _load_thumbnail_grayscale(target_path, _THUMBNAIL_SIZE)

    source_pixels = source_thumb.getdata()
    target_pixels = target_thumb.getdata()
    total_diff = sum(abs(a - b) for a, b in zip(source_pixels, target_pixels))
    mean_absolute_difference = total_diff / _THUMBNAIL_PIXEL_COUNT
    normalized_delta = mean_absolute_difference / 255.0

    source_hash_thumb = source_thumb.resize(_PHASH_SIZE)
    target_hash_thumb = target_thumb.resize(_PHASH_SIZE)
    source_bits = _average_hash_bits(source_hash_thumb)
    target_bits = _average_hash_bits(target_hash_thumb)
    perceptual_hash_distance = bin(source_bits ^ target_bits).count("1")

    return {
        "mean_absolute_difference": round(mean_absolute_difference, 4),
        "normalized_delta": round(normalized_delta, 4),
        "perceptual_hash_distance": perceptual_hash_distance,
    }


def compute_visual_change_events(
    package_root: Path | str,
    *,
    tool_name: str,
    tool_version: str,
    limits: Limits = DEFAULT_LIMITS,
) -> list[dict[str, Any]]:
    """Compute `visual_change_candidate` records for every adjacent pair of stored keyframes.

    Loads the package's `keyframes` track (via `keyframe_retrieval
    .load_keyframe_events`, so keyframe image paths are already
    containment/symlink-checked), then for each consecutive pair
    (ordered by `t_start_ms`) computes bounded, non-semantic
    pixel-difference metrics between their two images. Returns plain,
    JSONL-safe dicts -- not yet validated or written; the caller
    (`visual_change_writer.analyze_visual_change`) is responsible for
    that.

    Raises `VisualChangeComputeError` if:
      - the package has no keyframes track, or fewer than two stored
        keyframes (nothing to compare).
      - more than `DEFAULT_MAX_VISUAL_CHANGE_PAIRS` adjacent pairs
        would be produced (safety cap on unbounded work).
      - a keyframe's image file is missing from disk or unreadable.

    Also propagates `keyframe_retrieval.KeyframeRetrievalError` (e.g.
    an unsafe/escaping stored image path, an oversized/malformed
    track) and `VisualChangeUnavailableError` (Pillow not installed)
    unchanged -- both are already clean, specific errors.
    """
    from . import keyframe_retrieval as keyframe_retrieval_mod

    package_root = Path(package_root)
    keyframes = keyframe_retrieval_mod.load_keyframe_events(package_root, limits=limits)
    if len(keyframes) < 2:
        raise VisualChangeComputeError(
            "visual-change analysis requires at least 2 stored keyframes to compute "
            f"pairwise differences; found {len(keyframes)}"
        )

    pair_count = len(keyframes) - 1
    if pair_count > DEFAULT_MAX_VISUAL_CHANGE_PAIRS:
        raise VisualChangeComputeError(
            f"{pair_count} adjacent keyframe pairs exceeds the "
            f"{DEFAULT_MAX_VISUAL_CHANGE_PAIRS}-pair bound for one analyze call"
        )

    _ensure_engine_loaded()

    def _image_path(event: dict[str, Any]) -> str | None:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return None
        path = payload.get("path")
        return path if isinstance(path, str) and path else None

    created_by = f"{tool_name} {tool_version}"
    events: list[dict[str, Any]] = []
    for index in range(pair_count):
        source = keyframes[index]
        target = keyframes[index + 1]
        source_path = _image_path(source)
        target_path = _image_path(target)
        if not source_path or not target_path:
            raise VisualChangeComputeError(
                f"keyframe {source.get('id')!r} or {target.get('id')!r} has no recorded image path"
            )

        try:
            source_abs = resolve_in_package(package_root, source_path, field_name="keyframe.payload.path")
            target_abs = resolve_in_package(package_root, target_path, field_name="keyframe.payload.path")
        except PathSecurityError as exc:
            raise VisualChangeComputeError(str(exc)) from exc
        if not source_abs.is_file():
            raise VisualChangeComputeError(f"keyframe image file is missing: {source_path}")
        if not target_abs.is_file():
            raise VisualChangeComputeError(f"keyframe image file is missing: {target_path}")

        metrics = compute_visual_change_metrics(source_abs, target_abs)
        strength = strength_for_normalized_delta(metrics["normalized_delta"])

        t_start = source.get("t_start_ms", 0)
        t_end = target.get("t_start_ms", t_start)
        if t_end < t_start:
            t_end = t_start

        events.append(
            {
                "id": f"vc_{index:06d}",
                "type": VISUAL_CHANGE_RECORD_TYPE,
                "t_start_ms": t_start,
                "t_end_ms": t_end,
                "producer": {"name": tool_name, "version": tool_version},
                "payload": {
                    "source_keyframe_id": source.get("id"),
                    "target_keyframe_id": target.get("id"),
                    "source_image_path": source_path,
                    "target_image_path": target_path,
                    "metrics": metrics,
                    "strength": strength,
                    "caveats": list(VISUAL_CHANGE_CAVEATS),
                    "method": VISUAL_CHANGE_METHOD,
                    "created_by": created_by,
                },
            }
        )

    return events
