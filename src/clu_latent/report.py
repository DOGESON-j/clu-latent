"""Phase 3.0: a static, local-first HTML report for a .clulatent package.

This module renders a `.clulatent` package into a single, self-contained
HTML file so a human can inspect a package without Studio UI, a web
server, cloud services, ML, CLUBIN, or a plugin runtime. It is read-only:
it never mutates the package, never writes an index/receipt/lock file,
and never touches anything inside `package_path` -- it only reads
already-canonical data (manifest, tracks, receipts, review state, lock
status, validation) and renders it as escaped, static HTML.

Core principle (restated, because it governs every section below):
adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

The report displays evidence. It does not create truth, and it never
claims an adapter understood the media.

Safety: every piece of package-originated text (manifest fields, track
payload values, receipt fields, validation messages) is stripped of
control characters and HTML-escaped before being written into the
output document -- see `_esc()`. The generated document has no
`<script>` tag, no external stylesheet/font/image link, and no CDN
reference; CSS is a small inline `<style>` block. Large payloads and
long timelines are bounded (see `DEFAULT_MAX_TIMELINE_EVENTS` /
`DEFAULT_MAX_PAYLOAD_CHARS`) so a pathological package cannot produce
an unbounded report.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import lock as lock_mod
from . import tracks as tracks_mod
from .analysis_lanes import SUPPORTED_ANALYSIS_LANES
from .constants import ANALYSIS_RECEIPTS_FILE, REVIEW_EVENTS_TRACK_NAME, TOOL_NAME, TOOL_VERSION
from .event import EventEnvelope
from .manifest import Manifest, TrackDescriptor
from .review_resolver import resolve_package_review_states, summarize_review_states
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package
from .validate import ValidationReport, validate_package

DEFAULT_MAX_TIMELINE_EVENTS = 500
DEFAULT_MAX_PAYLOAD_CHARS = 200
DEFAULT_MAX_MESSAGE_CHARS = 400

_REVIEW_EVENT_TYPE_LABELS: dict[str, str] = {
    "review_approval": "Approvals",
    "review_rejection": "Rejections",
    "review_correction": "Corrections",
    "review_override": "Overrides",
    "human_note": "Notes",
    "review_status": "Status changes",
    "review_session_summary": "Session summaries",
}

# Control characters other than tab/newline -- the same set
# `security.console.safe_console_text` strips for terminal output.
# HTML-escaping alone does not remove these; they are stripped before
# `html.escape()` runs so no raw control byte ever reaches the output
# file, matching every other public-doc/report safety check in this
# codebase.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class ReportError(ValueError):
    """Raised when a static HTML report cannot be generated for a package.

    Only raised for a fundamentally unreadable package (missing path,
    missing/invalid manifest.json) -- a package that exists but fails
    other validation checks (bad hash, missing track file, ...) still
    produces a report; the validation section simply shows FAIL.
    """


@dataclass
class ReportResult:
    html: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class _TrackData:
    descriptor: TrackDescriptor
    events: list[EventEnvelope]
    category: str  # "source" | "review" | "analysis"


def _safe_text(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return _CONTROL_CHAR_RE.sub("", text)


def _esc(value: object) -> str:
    """Escape `value` for safe inclusion as HTML text content."""
    return html.escape(_safe_text(value), quote=True)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + "\u2026"  # ellipsis


def _track_category(track_name: str) -> str:
    if track_name == REVIEW_EVENTS_TRACK_NAME:
        return "review"
    if track_name in SUPPORTED_ANALYSIS_LANES:
        return "analysis"
    return "source"


def _payload_summary(payload: dict[str, Any], *, max_chars: int) -> str:
    if not payload:
        return ""
    if "path" in payload:
        detail = str(payload["path"])
    elif "text" in payload:
        language = payload.get("language")
        detail = f"[{language}] {payload['text']}" if language else str(payload["text"])
    elif "message" in payload:
        detail = str(payload["message"])
    else:
        try:
            detail = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError):
            detail = str(payload)
    return _truncate(detail, max_chars)


def _load_tracks_safe(
    package_path: Path, manifest: Manifest, *, limits: Limits, warnings: list[str]
) -> list[_TrackData]:
    results: list[_TrackData] = []
    for track in manifest.tracks:
        events: list[EventEnvelope] = []
        try:
            track_path = resolve_in_package(
                package_path, track.file, field_name=f"tracks[{track.name}].file"
            )
        except PathSecurityError as exc:
            warnings.append(str(exc))
            results.append(_TrackData(descriptor=track, events=events, category=_track_category(track.name)))
            continue
        try:
            events = tracks_mod.read_track_file(track_path, limits=limits)
        except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
            warnings.append(f"{track.name}: could not be read cleanly ({exc})")
            events = []
        results.append(_TrackData(descriptor=track, events=events, category=_track_category(track.name)))
    return results


def _read_receipts_safe(
    package_path: Path, relative_file: str, *, limits: Limits, warnings: list[str]
) -> list[dict[str, Any]]:
    try:
        receipts_path = resolve_in_package(package_path, relative_file, field_name=relative_file)
    except PathSecurityError as exc:
        warnings.append(str(exc))
        return []
    if not receipts_path.exists() or receipts_path.stat().st_size == 0:
        return []
    entries: list[dict[str, Any]] = []
    try:
        for record in iter_jsonl_bounded(receipts_path, limits=limits):
            if record.error is not None:
                warnings.append(f"{relative_file}:{record.lineno}: {record.error}")
                continue
            if isinstance(record.data, dict):
                entries.append(record.data)
    except JsonlLimitError as exc:
        warnings.append(f"{relative_file}: {exc}")
    return entries


# --- HTML rendering helpers -------------------------------------------------


def _render_table(headers: list[str], rows: list[list[str]], *, empty_message: str) -> str:
    if not rows:
        return f'<p class="empty">{_esc(empty_message)}</p>'
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        body_rows.append(f"<tr>{cells}</tr>")
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'


def _render_kv_table(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(f"<tr><th>{_esc(key)}</th><td>{value}</td></tr>" for key, value in pairs)
    return f"<table class='kv'><tbody>{rows}</tbody></table>"


def _render_summary_section(
    *,
    package_path: Path,
    manifest: Manifest,
    track_datas: list[_TrackData],
    ingest_receipt_count: int,
    analyze_receipt_count: int,
    lock_status_value: str,
    validation: ValidationReport,
) -> str:
    analysis_track_count = sum(1 for t in track_datas if t.category == "analysis")
    pairs = [
        ("Package path", f"<code>{_esc(str(package_path))}</code>"),
        ("Package id", f"<code>{_esc(manifest.package_id)}</code>"),
        ("clulatent_version", _esc(manifest.clulatent_version)),
        ("Status", _esc(manifest.status)),
        (
            "Source",
            f"{_esc(manifest.source.filename)} "
            f"({_esc(manifest.source.width)}x{_esc(manifest.source.height)}, "
            f"{_esc(manifest.source.duration_ms)} ms, "
            f"audio={_esc(manifest.source.has_audio)})",
        ),
        ("Track count", str(len(manifest.tracks))),
        ("Analysis track count", str(analysis_track_count)),
        ("Receipt count", str(ingest_receipt_count + analyze_receipt_count)),
        ("Lock status", _esc(lock_status_value)),
        ("Validation status", "PASS" if validation.valid else "FAIL"),
    ]
    return f"<section><h2>Package summary</h2>{_render_kv_table(pairs)}</section>"


def _render_trust_section(*, validation: ValidationReport, lock_status_value: str) -> str:
    warnings_html = []
    if lock_status_value != "locked":
        warnings_html.append(
            '<li class="warn">Unlocked (or lock status could not be confirmed) -- this package '
            "has no confirmed integrity lock. Anyone with filesystem access could have modified "
            "it since it was produced.</li>"
        )
    if not validation.valid:
        warnings_html.append(
            '<li class="warn">Validation FAILED for this package -- see the Validation section '
            "below before trusting any of its contents.</li>"
        )
    warnings_html.append(
        '<li class="warn">This report was generated from local package contents only. It does '
        "not verify the original media, does not re-run any adapter, and does not confirm "
        "anything beyond what is already recorded in this package.</li>"
    )
    return (
        "<section><h2>Trust status</h2>"
        '<p class="trust-statement"><strong>Adapter outputs are evidence, not truth.</strong> '
        "Generated does not mean canonical. Canonical means validated, bounded, receipted, "
        "reviewable, and lockable.</p>"
        + _render_kv_table(
            [
                ("Validation", "PASS" if validation.valid else "FAIL"),
                ("Lock status", _esc(lock_status_value)),
            ]
        )
        + f'<ul class="warnings">{"".join(warnings_html)}</ul>'
        "</section>"
    )


def _render_timeline_section(
    track_datas: list[_TrackData], *, max_events: int, max_payload_chars: int
) -> str:
    all_events: list[tuple[str, EventEnvelope]] = []
    for track_data in track_datas:
        all_events.extend((track_data.descriptor.name, event) for event in track_data.events)
    all_events.sort(key=lambda pair: (pair[1].t_start_ms, pair[1].id))

    total = len(all_events)
    shown = all_events[:max_events]

    rows = []
    for track_name, event in shown:
        confidence = f"{event.confidence:.3f}" if event.confidence is not None else "\u2014"
        rows.append(
            [
                f"<code>{_esc(track_name)}</code>",
                f"<code>{_esc(event.id)}</code>",
                _esc(event.type),
                str(event.t_start_ms),
                str(event.t_end_ms),
                _esc(confidence),
                _esc(event.producer.name),
                _esc(_payload_summary(event.payload, max_chars=max_payload_chars)),
            ]
        )

    table_html = _render_table(
        ["Track", "Event id", "Type", "Start (ms)", "End (ms)", "Confidence", "Producer", "Payload (bounded)"],
        rows,
        empty_message="No track events found.",
    )
    note = ""
    if total > max_events:
        note = (
            f'<p class="note">Showing {max_events} of {total} events '
            f"(bounded; the full set is available in the package's own tracks/*.jsonl files).</p>"
        )
    return f"<section><h2>Timeline overview</h2>{note}{table_html}</section>"


def _render_track_overview_section(track_datas: list[_TrackData]) -> str:
    rows = []
    for track_data in track_datas:
        rows.append(
            [
                _esc(track_data.descriptor.name),
                _esc(track_data.category),
                str(len(track_data.events)),
                f"<code>{_esc(track_data.descriptor.file)}</code>",
            ]
        )
    table_html = _render_table(
        ["Track", "Category", "Event count", "File"], rows, empty_message="No tracks declared in manifest."
    )
    return f"<section><h2>Track overview</h2>{table_html}</section>"


def _render_adapter_evidence_section(
    track_datas: list[_TrackData], analyze_receipts: list[dict[str, Any]]
) -> str:
    analysis_tracks = [t for t in track_datas if t.category == "analysis"]
    if not analysis_tracks:
        return (
            "<section><h2>Adapter evidence</h2>"
            '<p class="empty">No analysis lanes present in this package.</p></section>'
        )

    blocks = []
    for track_data in analysis_tracks:
        lane = track_data.descriptor.name
        type_counts: dict[str, int] = {}
        confidences: list[float] = []
        for event in track_data.events:
            type_counts[event.type] = type_counts.get(event.type, 0) + 1
            if event.confidence is not None:
                confidences.append(event.confidence)

        confidence_summary = "n/a"
        if confidences:
            confidence_summary = (
                f"min={min(confidences):.3f} avg={(sum(confidences) / len(confidences)):.3f} "
                f"max={max(confidences):.3f}"
            )

        matching_receipts = [r for r in analyze_receipts if lane in (r.get("event_counts") or {})]
        receipt_rows = []
        for receipt in matching_receipts:
            receipt_rows.append(
                [
                    _esc(receipt.get("adapter_name", "\u2014")),
                    _esc(receipt.get("tool_name", "\u2014")),
                    _esc(receipt.get("tool_version", "\u2014")),
                    _esc(receipt.get("model_name") or "\u2014"),
                    _esc(receipt.get("model_version") or "\u2014"),
                    _esc(receipt.get("status", "\u2014")),
                ]
            )
        receipts_table = _render_table(
            ["Adapter", "Tool", "Tool version", "Model", "Model version", "Status"],
            receipt_rows,
            empty_message="No matching entries in receipts/analyze.jsonl.",
        )

        labels_html = ", ".join(
            f"<code>{_esc(event_type)}</code> ({count})" for event_type, count in sorted(type_counts.items())
        )
        warnings_for_lane = [w for r in matching_receipts for w in (r.get("warnings") or [])]
        warnings_html = ""
        if warnings_for_lane:
            items = "".join(f"<li>{_esc(w)}</li>" for w in warnings_for_lane)
            warnings_html = f'<ul class="warnings">{items}</ul>'

        blocks.append(
            f"<h3><code>{_esc(lane)}</code></h3>"
            + _render_kv_table(
                [
                    ("Event count", str(len(track_data.events))),
                    ("Labels (conservative, not identity claims)", labels_html or "\u2014"),
                    ("Confidence", _esc(confidence_summary)),
                ]
            )
            + receipts_table
            + warnings_html
        )

    return (
        "<section><h2>Adapter evidence</h2>"
        '<p class="note">Adapter outputs below are evidence, not truth -- these labels do not '
        "claim the adapter understood the media.</p>" + "".join(blocks) + "</section>"
    )


def _render_receipts_section(
    ingest_receipts: list[dict[str, Any]], analyze_receipts: list[dict[str, Any]]
) -> str:
    ingest_rows = []
    for receipt in ingest_receipts:
        ingest_rows.append(
            [
                _esc(receipt.get("timestamp", "\u2014")),
                _esc(receipt.get("operation", "\u2014")),
                _esc(receipt.get("status", "\u2014")),
                _esc((receipt.get("tool") or {}).get("name", "\u2014")),
                str(len(receipt.get("files_created") or [])),
            ]
        )
    ingest_table = _render_table(
        ["Timestamp", "Operation", "Status", "Tool", "Files created"],
        ingest_rows,
        empty_message="No ingest receipts found.",
    )

    analyze_rows = []
    for receipt in analyze_receipts:
        event_counts = receipt.get("event_counts") or {}
        lane_summary = ", ".join(f"{lane}={count}" for lane, count in sorted(event_counts.items()))
        analyze_rows.append(
            [
                _esc(receipt.get("adapter_name", "\u2014")),
                _esc(receipt.get("tool_name", "\u2014")),
                _esc(receipt.get("tool_version", "\u2014")),
                _esc(receipt.get("status", "\u2014")),
                _esc(lane_summary or "\u2014"),
            ]
        )
    analyze_table = _render_table(
        ["Adapter", "Tool", "Tool version", "Status", "Lane counts"],
        analyze_rows,
        empty_message="No analyze receipts found.",
    )

    return (
        "<section><h2>Receipts</h2>"
        f"<h3>Ingest receipts</h3>{ingest_table}"
        f"<h3>Analyze receipts</h3>{analyze_table}"
        "</section>"
    )


def _render_review_section(track_datas: list[_TrackData], *, package_path: Path, limits: Limits) -> str:
    review_track = next((t for t in track_datas if t.descriptor.name == REVIEW_EVENTS_TRACK_NAME), None)
    if review_track is None or not review_track.events:
        return "<section><h2>Review</h2><p class='empty'>No review events found.</p></section>"

    type_counts: dict[str, int] = {}
    for event in review_track.events:
        type_counts[event.type] = type_counts.get(event.type, 0) + 1

    rows = [
        [_REVIEW_EVENT_TYPE_LABELS.get(event_type, _esc(event_type)), str(count)]
        for event_type, count in sorted(type_counts.items())
    ]
    raw_table = _render_table(["Kind", "Count"], rows, empty_message="No review events found.")

    states, _resolver_warnings = resolve_package_review_states(package_path, limits=limits)
    summary = summarize_review_states(states)
    resolved_pairs = [
        ("Total source events", str(summary["total"])),
        ("Approved", str(summary["approved"])),
        ("Rejected", str(summary["rejected"])),
        ("Corrected", str(summary["corrected"])),
        ("Superseded", str(summary["superseded"])),
        ("Needs review / uncertain", str(summary["needs_review"] + summary["uncertain"])),
        ("Conflicts", str(summary["conflicts"])),
    ]

    return (
        "<section><h2>Review</h2>"
        f"<h3>Review events recorded</h3>{raw_table}"
        f"<h3>Current effective review state</h3>{_render_kv_table(resolved_pairs)}"
        "</section>"
    )


def _render_validation_section(validation: ValidationReport, *, max_message_chars: int) -> str:
    status = "PASS" if validation.valid else "FAIL"
    error_rows = [[_esc(_truncate(msg, max_message_chars))] for msg in validation.errors]
    warning_rows = [[_esc(_truncate(msg, max_message_chars))] for msg in validation.warnings]
    errors_table = _render_table(["Message"], error_rows, empty_message="No errors.")
    warnings_table = _render_table(["Message"], warning_rows, empty_message="No warnings.")
    return (
        "<section><h2>Validation</h2>"
        + _render_kv_table(
            [
                ("Status", status),
                ("Errors", str(len(validation.errors))),
                ("Warnings", str(len(validation.warnings))),
            ]
        )
        + f"<h3>Errors</h3>{errors_table}"
        + f"<h3>Warnings</h3>{warnings_table}"
        + "</section>"
    )


_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 960px; padding: 0 1rem; color: #1a1a1a; background: #fff; }
h1 { font-size: 1.6rem; }
h2 { font-size: 1.2rem; margin-top: 2.5rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
h3 { font-size: 1rem; margin-top: 1.5rem; }
code { background: #f2f2f2; padding: 0.05rem 0.3rem; border-radius: 3px; font-size: 0.9em; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem 0; font-size: 0.9rem; }
th, td { border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; vertical-align: top; }
th { background: #f7f7f7; }
table.kv th { width: 220px; background: #fafafa; }
.trust-statement { font-size: 1.05rem; background: #fff8e1; padding: 0.75rem; border-left: 4px solid #e0a800; }
ul.warnings { padding-left: 1.2rem; }
ul.warnings li.warn { color: #8a4b00; }
p.empty, p.note { color: #555; font-style: italic; }
"""


def generate_report(
    package_path: Path,
    *,
    limits: Limits = DEFAULT_LIMITS,
    max_timeline_events: int = DEFAULT_MAX_TIMELINE_EVENTS,
    max_payload_chars: int = DEFAULT_MAX_PAYLOAD_CHARS,
    max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
) -> ReportResult:
    """Build a static HTML report for `package_path`.

    Read-only: only ever reads manifest.json, tracks/*.jsonl,
    receipts/*.jsonl, and lock/* -- never writes, creates, or mutates
    anything inside the package. Raises `ReportError` only when the
    package cannot be meaningfully read at all (missing path, missing
    or invalid manifest.json); any other validation problem is instead
    reflected in the report's own Validation section.
    """
    package_path = Path(package_path)
    warnings: list[str] = []

    validation = validate_package(package_path, limits=limits)
    if validation.manifest is None:
        message = validation.errors[0] if validation.errors else f"Package could not be read: {package_path}"
        raise ReportError(message)
    manifest = validation.manifest

    try:
        lock_status_value, _lock_report = lock_mod.lock_status(package_path, limits=limits)
    except lock_mod.LockError as exc:
        lock_status_value = "lock-invalid"
        warnings.append(f"lock status could not be determined: {exc}")

    track_datas = _load_tracks_safe(package_path, manifest, limits=limits, warnings=warnings)
    ingest_receipts = _read_receipts_safe(package_path, manifest.receipts.file, limits=limits, warnings=warnings)
    analyze_receipts = _read_receipts_safe(
        package_path, ANALYSIS_RECEIPTS_FILE, limits=limits, warnings=warnings
    )

    sections = [
        _render_summary_section(
            package_path=package_path,
            manifest=manifest,
            track_datas=track_datas,
            ingest_receipt_count=len(ingest_receipts),
            analyze_receipt_count=len(analyze_receipts),
            lock_status_value=lock_status_value,
            validation=validation,
        ),
        _render_trust_section(validation=validation, lock_status_value=lock_status_value),
        _render_timeline_section(
            track_datas, max_events=max_timeline_events, max_payload_chars=max_payload_chars
        ),
        _render_track_overview_section(track_datas),
        _render_adapter_evidence_section(track_datas, analyze_receipts),
        _render_receipts_section(ingest_receipts, analyze_receipts),
        _render_review_section(track_datas, package_path=package_path, limits=limits),
        _render_validation_section(validation, max_message_chars=max_message_chars),
    ]

    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>CLULatent package report \u2014 {_esc(manifest.package_id)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"<h1>CLULatent package report</h1>\n"
        f'<p class="note">Generated by {_esc(TOOL_NAME)} {_esc(TOOL_VERSION)} '
        f"from local package contents. Evidence, not truth -- generated does not mean canonical. "
        f"No network access, no cloud, no Studio UI, no CLUBIN, and no semantic understanding of "
        f"the media are involved in producing this report.</p>\n"
        + "\n".join(sections)
        + "\n</body>\n</html>\n"
    )

    return ReportResult(html=doc, warnings=warnings)
