"""V1: V1 evidence playback viewer for a .clulatent package.

Render a single, static, local-first HTML viewer that lets a human play
(or scrub) a package's source media while seeing the package's
timestamped *evidence tracks* laid out on a timeline: keyframes, audio
events, speech events, visual-change candidates, changed-region
candidates, evidence bundles, and agent reviews.

Core rule (it governs everything below):

    MP4 plays media. CLULatent plays media plus evidence.

This is **evidence playback, not scene understanding**. Nothing here runs
visual AI, captions a frame, transcribes speech, infers scene meaning /
object identity / intent, runs OCR or motion analysis, or extracts new
frames. It only reads records the package already stored and lays them on
a timeline so a human can seek to them.

Read-only: it never mutates the package -- never writes a track,
manifest, receipt, index, or lock file inside it. The only file written
is the caller-chosen `--output` HTML (outside, or explicitly inside, the
package at the caller's request).

Boundaries mirror the rest of the V1 surface: no LLM / OpenAI / Claude /
network / local vision model / new ML dependency, no FFmpeg / Pillow
invocation, no web server, no bundler / npm / CDN / external font /
remote asset. The generated document is a single self-contained HTML file
(inline CSS, inline JS, embedded JSON payload) safe to open directly in a
browser. It references the package's own media/keyframe files only via
safe, path-contained relative links computed from the output file's
location; if no playable source is present it degrades to an
evidence-only view.

Language safety: every string this module *authors* (caveats, section
notes, the safety panel) is scanned by a forbidden-language guard so the
viewer never overclaims. Opaque, user-controlled data -- a source
filename, a package id, a track name, an adapter payload field -- is
never scanned, so a video literally named ``the_video_shows_intent.mp4``
never trips the guard and is never censored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import package_reader as package_reader_mod
from . import v1_package_index as v1_package_index_mod
from .agent_context import CONTEXT_CAVEATS, FORBIDDEN_CONTEXT_PHRASES
from .agent_review_retrieval import AGENT_REVIEW_TRACK_NAME
from .changed_region_retrieval import CHANGED_REGION_TRACK_NAME
from .constants import TOOL_NAME, TOOL_VERSION
from .evidence_bundle_retrieval import EVIDENCE_BUNDLE_TRACK_NAME
from .report import _esc, _truncate
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package
from .visual_change_retrieval import VISUAL_CHANGE_TRACK_NAME

VIEWER_SCHEMA_ID = "clulatent.v1_playback_viewer.v0"
VIEWER_SCHEMA_VERSION = "0.1.0"

# Default cap on the total number of timeline events embedded in a single
# viewer so a pathological package cannot produce an unbounded document.
DEFAULT_MAX_EVENTS = 1000

# Independent cap on the number of keyframe thumbnails linked into the
# keyframe strip (kept separate from the timeline-event cap because
# keyframes are also rendered as their own strip).
DEFAULT_MAX_KEYFRAMES = 500

# Bound on any single opaque string echoed into an embedded payload field.
_MAX_TEXT_CHARS = 300

# At most this many scalar payload fields are surfaced per event in the
# details panel, so an adapter cannot inflate the payload unboundedly.
_MAX_EVENT_FIELDS = 8

# The seven core V1 lanes, in a stable rendering order. Any other track a
# package carries is still shown; these only fix ordering/colour.
_CORE_LANES: tuple[str, ...] = (
    "keyframes",
    "audio_events",
    "speech_events",
    VISUAL_CHANGE_TRACK_NAME,
    CHANGED_REGION_TRACK_NAME,
    EVIDENCE_BUNDLE_TRACK_NAME,
    AGENT_REVIEW_TRACK_NAME,
)

_KEYFRAMES_TRACK_NAME = "keyframes"

# Extension -> MIME type guesses used only to hint the <video> element's
# <source type>. Absence of a guess just omits the hint; the browser still
# tries the file. No transcoding, no probing, no FFmpeg.
_VIDEO_MIME: dict[str, str] = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".ogg": "video/ogg",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}

# Authored, load-bearing safety prose. Worded to avoid every banned
# semantic phrase; the guard runs over exactly these strings (plus the
# shared CONTEXT_CAVEATS) and never over package data.
_VIEWER_CAVEATS: tuple[str, ...] = (
    "This viewer plays candidate evidence recorded in the package; it does not assert scene meaning.",
    "Visual-change and changed-region markers are numeric pixel-difference candidates, not recognition.",
    "Evidence bundles collect existing package records for a time range; they do not establish what happened.",
    "Agent review markers report evidence support and gaps only; they do not confirm any event.",
    "Any downstream claim should cite the event ids, timestamps, tracks, bundles, and reviews shown here.",
)


class PlaybackViewerError(ValueError):
    """Raised when a playback viewer cannot be generated at all.

    Only raised for a fundamentally unreadable package (missing path,
    missing/invalid manifest.json) or an authored-prose guard failure. A
    package that reads but has no playable media, empty tracks, or missing
    keyframe images still produces a viewer; those conditions are shown as
    evidence, not raised.
    """


@dataclass
class PlaybackViewerResult:
    """The rendered viewer plus a little metadata for the CLI/tests."""

    html: str
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    output_path: Path | None = None


# --- authored-language guard -------------------------------------------------


def _assert_safe_authored(*texts: str) -> None:
    """Refuse to emit any authored string containing a forbidden phrase.

    Scans only strings this module authors -- never opaque package data.
    """
    for text in texts:
        lowered = text.lower()
        for phrase in FORBIDDEN_CONTEXT_PHRASES:
            if phrase in lowered:
                raise PlaybackViewerError(
                    f"authored viewer text contains forbidden phrase {phrase!r}"
                )


# --- helpers -----------------------------------------------------------------


def _relative_href(package_path: Path, package_relpath: str, output_dir: Path | None) -> str | None:
    """Browser-usable relative link from the output file to a package file.

    Computed lexically (``os.path.abspath`` normalises ``..`` without
    following symlinks) so links stay valid across symlinked prefixes
    (e.g. macOS ``/tmp`` -> ``/private/tmp``). Returns ``None`` if a
    relative path cannot be formed. Never emits an absolute filesystem
    path or a network URL. Path components are URL-quoted (slashes kept).
    """
    if output_dir is None:
        return None
    try:
        actual = os.path.abspath(os.path.join(os.fspath(package_path), package_relpath))
        base = os.path.abspath(os.fspath(output_dir))
        rel = os.path.relpath(actual, base)
    except (ValueError, OSError):
        return None
    return quote(Path(rel).as_posix(), safe="/")


def _scalar_fields(payload: Any) -> dict[str, Any]:
    """Pick a small, bounded set of scalar payload fields for the panel.

    Opaque adapter data -- not authored prose -- so it is copied verbatim
    (strings truncated for size), never scanned or reworded. Only
    str/int/float/bool values are surfaced; nested structures are skipped
    to keep the details panel small and predictable.
    """
    if not isinstance(payload, dict):
        return {}
    fields: dict[str, Any] = {}
    for key, value in payload.items():
        if len(fields) >= _MAX_EVENT_FIELDS:
            break
        if not isinstance(key, str):
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            fields[key] = value
        elif isinstance(value, str):
            fields[key] = _truncate(value, _MAX_TEXT_CHARS)
    return fields


def _media_section(
    reader: package_reader_mod.PackageReader,
    package_path: Path,
    output_dir: Path | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Describe the best playable source, if one is present on disk.

    Never copies or transcodes media. Resolves the manifest's stored
    source path (path-contained, symlink-safe); if it exists and a
    relative link can be computed, the viewer offers a ``<video>``.
    Otherwise the viewer degrades to an evidence-only view.
    """
    source = reader.manifest.source
    stored_path = source.stored_path
    href: str | None = None
    exists = False
    if stored_path:
        try:
            abs_path = resolve_in_package(
                package_path, stored_path, field_name="source.stored_path"
            )
        except PathSecurityError as exc:
            warnings.append(f"source path rejected ({exc})")
            abs_path = None
        if abs_path is not None:
            try:
                exists = abs_path.is_file()
            except OSError:
                exists = False
            if exists:
                href = _relative_href(package_path, stored_path, output_dir)
    playable = bool(exists and href)
    suffix = Path(stored_path).suffix.lower() if stored_path else ""
    return {
        "playable": playable,
        "href": href,
        "filename": source.filename or None,
        "stored_path": stored_path or None,
        "exists": exists,
        "mime_type": _VIDEO_MIME.get(suffix),
        "has_audio": bool(source.has_audio),
    }


def _keyframe_strip(
    reader: package_reader_mod.PackageReader,
    package_path: Path,
    output_dir: Path | None,
    max_keyframes: int,
    warnings: list[str],
) -> dict[str, Any]:
    """Bounded list of keyframe thumbnails with safe relative links."""
    if not reader.has_track(_KEYFRAMES_TRACK_NAME):
        return {"events": [], "total": 0, "shown": 0, "truncated": False}
    try:
        records = reader.load_track(_KEYFRAMES_TRACK_NAME)
    except package_reader_mod.PackageReaderError as exc:
        warnings.append(f"keyframes track could not be read cleanly ({exc})")
        return {"events": [], "total": 0, "shown": 0, "truncated": False}

    total = len(records)
    truncated = total > max_keyframes
    shown = records[:max_keyframes]
    out: list[dict[str, Any]] = []
    for record in shown:
        payload = record.payload if isinstance(record.payload, dict) else {}
        raw_path = payload.get("path")
        rel = raw_path if isinstance(raw_path, str) and raw_path else None
        href: str | None = None
        exists = False
        if rel is not None:
            try:
                abs_path = resolve_in_package(
                    package_path, rel, field_name="keyframe.payload.path"
                )
            except PathSecurityError as exc:
                warnings.append(f"{record.id}: keyframe path rejected ({exc})")
                abs_path = None
            if abs_path is not None:
                try:
                    exists = abs_path.is_file()
                except OSError:
                    exists = False
                if exists:
                    href = _relative_href(package_path, rel, output_dir)
        out.append(
            {
                "id": record.id,
                "t_start_ms": record.t_start_ms,
                "path": rel,
                "exists": exists,
                "href": href,
            }
        )
    return {"events": out, "total": total, "shown": len(out), "truncated": truncated}


def _timeline(
    reader: package_reader_mod.PackageReader,
    max_events: int,
    warnings: list[str],
) -> dict[str, Any]:
    """Merge every track's events into one bounded, time-ordered list."""
    names = list(reader.track_names())
    # Order known core lanes first, then any extra tracks, both stable.
    ordered = [n for n in _CORE_LANES if n in names]
    ordered += [n for n in names if n not in _CORE_LANES]

    collected: list[dict[str, Any]] = []
    for name in ordered:
        try:
            records = reader.load_track(name)
        except package_reader_mod.PackageReaderError as exc:
            warnings.append(f"track {name!r} could not be read cleanly ({exc})")
            continue
        for record in records:
            collected.append(
                {
                    "id": record.id,
                    "track": name,
                    "type": record.type,
                    "t_start_ms": record.t_start_ms,
                    "t_end_ms": record.t_end_ms,
                    "confidence": record.confidence,
                    "fields": _scalar_fields(record.payload),
                }
            )

    collected.sort(key=lambda e: (e["t_start_ms"], str(e["id"])))
    total = len(collected)
    truncated = total > max_events
    events = collected[:max_events]
    return {"events": events, "total": total, "shown": len(events), "truncated": truncated}


def _evidence_section(reader: package_reader_mod.PackageReader) -> dict[str, Any]:
    """Small, bounded index of evidence bundles and agent reviews."""
    bundles: list[dict[str, Any]] = []
    if reader.has_track(EVIDENCE_BUNDLE_TRACK_NAME):
        for record in reader.load_track(EVIDENCE_BUNDLE_TRACK_NAME):
            bundles.append(
                {
                    "id": record.id,
                    "t_start_ms": record.t_start_ms,
                    "t_end_ms": record.t_end_ms,
                }
            )
    reviews: list[dict[str, Any]] = []
    if reader.has_track(AGENT_REVIEW_TRACK_NAME):
        for record in reader.load_track(AGENT_REVIEW_TRACK_NAME):
            payload = record.payload if isinstance(record.payload, dict) else {}
            status = payload.get("review_status")
            bundle_id = payload.get("evidence_bundle_id")
            reviews.append(
                {
                    "id": record.id,
                    "review_status": status if isinstance(status, str) else None,
                    "evidence_bundle_id": bundle_id if isinstance(bundle_id, str) else None,
                }
            )
    return {"evidence_bundles": bundles, "agent_reviews": reviews}


def _index_section(
    package_path: Path,
    output_dir: Path | None,
    limits: Limits,
    warnings: list[str],
) -> dict[str, Any]:
    """Report the built-in V1 index (V1), with safe relative links."""
    present = v1_package_index_mod.v1_index_exists(package_path)
    if not present:
        return {"present": False, "verification_status": "MISSING", "artifacts": []}
    artifacts: list[dict[str, Any]] = []
    try:
        manifest = v1_package_index_mod.load_v1_index_manifest(package_path, limits=limits)
    except Exception as exc:  # noqa: BLE001 - index is optional; degrade gracefully
        warnings.append(f"v1 index manifest could not be read ({exc})")
        manifest = None
    entries: list[dict[str, Any]] = []
    if isinstance(manifest, dict):
        raw_entries = manifest.get("artifacts")
        if isinstance(raw_entries, list):
            entries = [e for e in raw_entries if isinstance(e, dict)]
    if not entries:
        # Fall back to the known artifact filenames if the manifest is
        # unreadable, so the links still work.
        names = list(v1_package_index_mod.INDEX_ARTIFACT_FILENAMES) + [
            v1_package_index_mod.INDEX_MANIFEST_FILENAME
        ]
        entries = [
            {"name": n, "path": f"{v1_package_index_mod.INDEX_V1_DIR}/{n}"} for n in names
        ]
    for entry in entries:
        rel = entry.get("path")
        rel = rel if isinstance(rel, str) and rel else None
        href = _relative_href(package_path, rel, output_dir) if rel else None
        artifacts.append(
            {
                "name": entry.get("name") if isinstance(entry.get("name"), str) else rel,
                "path": rel,
                "media_type": entry.get("media_type")
                if isinstance(entry.get("media_type"), str)
                else None,
                "href": href,
            }
        )
    try:
        verification_status = v1_package_index_mod.verify_v1_package_index(
            package_path, limits=limits
        ).overall_status
    except Exception as exc:  # noqa: BLE001 - status remains honest and bounded
        warnings.append(f"v1 index verification could not complete ({exc})")
        verification_status = "UNKNOWN"
    return {
        "present": True,
        "verification_status": verification_status,
        "artifacts": artifacts,
    }


# --- payload -----------------------------------------------------------------


def build_playback_viewer_payload(
    package_path: Path | str,
    *,
    output_path: Path | str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_keyframes: int = DEFAULT_MAX_KEYFRAMES,
    limits: Limits = DEFAULT_LIMITS,
) -> dict[str, Any]:
    """Build the read-only JSON payload backing a playback viewer.

    `output_path`, when given, is used only to compute relative media /
    keyframe / index links from the output file's directory; it is never
    written to here. Raises `PlaybackViewerError` only when the package
    cannot be read at all.
    """
    package_path = Path(package_path)
    output_dir = Path(output_path).parent if output_path is not None else None
    max_events = max(0, int(max_events))
    max_keyframes = max(0, int(max_keyframes))
    warnings: list[str] = []

    try:
        reader = package_reader_mod.open_package(package_path, strict=False, limits=limits)
    except package_reader_mod.PackageReaderError as exc:
        raise PlaybackViewerError(str(exc)) from exc

    warnings.extend(reader.read_warnings)

    report = reader.validate()
    validation = {
        "valid": bool(report.valid),
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
    }

    try:
        lock = reader.lock_status()
    except package_reader_mod.PackageReaderError as exc:
        warnings.append(f"lock status could not be read ({exc})")
        lock = {"status": "unknown"}

    source = reader.manifest.source
    package_facts = {
        "package_id": reader.package_id,
        "status": reader.status,
        "created_at": reader.created_at,
        "duration_ms": reader.duration_ms,
        "has_audio": bool(reader.has_audio),
        "clulatent_version": reader.manifest.clulatent_version,
        "source_filename": source.filename or None,
        "container_format": source.container_format or None,
        "width": source.width,
        "height": source.height,
        "video_codec": source.video_codec,
        "audio_codec": source.audio_codec,
    }

    tracks = [
        {
            "name": handle.name,
            "record_count": handle.record_count,
            "known": handle.known,
        }
        for handle in reader.list_tracks()
    ]

    payload: dict[str, Any] = {
        "schema_id": VIEWER_SCHEMA_ID,
        "schema_version": VIEWER_SCHEMA_VERSION,
        "generated_by": {"tool": TOOL_NAME, "version": TOOL_VERSION},
        "package": package_facts,
        "validation": validation,
        "lock": lock,
        "tracks": tracks,
        "media": _media_section(reader, package_path, output_dir, warnings),
        "timeline": _timeline(reader, max_events, warnings),
        "keyframes": _keyframe_strip(
            reader, package_path, output_dir, max_keyframes, warnings
        ),
        "evidence": _evidence_section(reader),
        "index_v1": _index_section(package_path, output_dir, limits, warnings),
        "caveats": list(CONTEXT_CAVEATS) + list(_VIEWER_CAVEATS),
        "warnings": warnings,
    }
    try:
        from .v1_profile import verify_v1_profile

        payload["profile_v1"] = {
            "identifier": "clulatent.profile.v1",
            "status": verify_v1_profile(package_path, limits=limits).compatibility,
        }
    except Exception as exc:  # noqa: BLE001 - viewer must degrade to UNKNOWN
        warnings.append(f"V1 profile verification could not complete ({exc})")
        payload["profile_v1"] = {
            "identifier": "clulatent.profile.v1",
            "status": "UNKNOWN",
        }
    return payload


# --- HTML rendering ----------------------------------------------------------


_STYLE = """
:root { --bg:#0f1216; --panel:#171b22; --line:#2a303a; --fg:#e6e9ef; --muted:#9aa4b2;
        --accent:#4aa3ff; --warn:#e0a800; --ok:#27ae60; --bad:#c0392b; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 0; color: var(--fg); background: var(--bg); line-height: 1.45; }
main { max-width: 1180px; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 1.75rem 0 .5rem; border-bottom: 1px solid var(--line);
     padding-bottom: .3rem; }
.sub { color: var(--muted); font-size: .9rem; margin: 0 0 1rem; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
         padding: .9rem 1rem; margin: .6rem 0; }
.kv { width: 100%; border-collapse: collapse; font-size: .88rem; }
.kv th, .kv td { text-align: left; padding: .3rem .5rem; border-bottom: 1px solid var(--line);
                 vertical-align: top; }
.kv th { color: var(--muted); font-weight: 600; width: 190px; }
.badge { display: inline-block; padding: .1rem .5rem; border-radius: 999px; font-size: .78rem;
         border: 1px solid var(--line); }
.badge.ok { color: var(--ok); border-color: var(--ok); }
.badge.bad { color: var(--bad); border-color: var(--bad); }
.badge.warn { color: var(--warn); border-color: var(--warn); }
video { width: 100%; max-height: 60vh; background: #000; border-radius: 6px; }
.media-missing { color: var(--muted); font-style: italic; padding: 1.5rem; text-align: center;
                 border: 1px dashed var(--line); border-radius: 6px; }
.toggles { display: flex; flex-wrap: wrap; gap: .5rem 1rem; margin: .5rem 0; }
.toggles label { font-size: .85rem; display: inline-flex; align-items: center; gap: .35rem;
                 cursor: pointer; }
.swatch { width: .8rem; height: .8rem; border-radius: 2px; display: inline-block; }
#timeline { position: relative; height: 64px; background: #0b0e12; border: 1px solid var(--line);
            border-radius: 6px; margin: .5rem 0; overflow: hidden; }
.marker { position: absolute; top: 8px; width: 3px; height: 48px; cursor: pointer; opacity: .85; }
.marker:hover { opacity: 1; outline: 1px solid #fff; }
.marker { border: 0; padding: 0; }
#playhead { position: absolute; top: 0; width: 2px; height: 100%; background: #fff; opacity: .7;
            pointer-events: none; }
.axis { display: flex; justify-content: space-between; color: var(--muted); font-size: .75rem; }
.strip { display: flex; gap: .4rem; overflow-x: auto; padding: .4rem 0; }
.strip .kf { flex: 0 0 auto; width: 120px; cursor: pointer; text-align: center;
             color: inherit; background: transparent; border: 0; padding: 0; }
.strip .kf img { width: 120px; height: auto; border: 1px solid var(--line); border-radius: 4px;
                 display: block; background: #000; }
.strip .kf .miss { width: 120px; height: 68px; display: flex; align-items: center;
                   justify-content: center; color: var(--muted); font-size: .7rem;
                   border: 1px dashed var(--line); border-radius: 4px; }
.strip .kf small { color: var(--muted); font-size: .7rem; }
#details { min-height: 4rem; font-size: .88rem; }
#details table { border-collapse: collapse; margin-top: .4rem; }
#details td { padding: .2rem .5rem; border-bottom: 1px solid var(--line); }
#details td:first-child { color: var(--muted); }
code { background: #0b0e12; padding: .05rem .3rem; border-radius: 3px; word-break: break-all; }
ul.caveats { padding-left: 1.1rem; margin: .3rem 0; }
ul.caveats li { margin: .2rem 0; }
a { color: var(--accent); }
button:focus-visible, input:focus-visible, a:focus-visible {
  outline: 3px solid var(--accent); outline-offset: 2px;
}
.copy { margin-left: .4rem; color: var(--fg); background: #242b35;
        border: 1px solid var(--line); border-radius: 4px; cursor: pointer; }
.links a { display: inline-block; margin-right: 1rem; font-size: .85rem; }
details.raw { margin-top: .5rem; }
details.raw pre { background: #0b0e12; padding: .6rem; border-radius: 4px; overflow-x: auto;
                  font-size: .78rem; max-height: 260px; }
.muted { color: var(--muted); }
@media (max-width: 700px) {
  main { padding: 1rem .7rem 3rem; }
  .kv th { width: 130px; }
  .panel { overflow-x: auto; }
}
"""


def _embed_json(payload: dict[str, Any]) -> str:
    """Serialise the payload for safe embedding in an HTML script tag."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=False)
    # Neutralise sequences that could break out of the script element or a
    # JS string context. Applies to the whole JSON blob (opaque data too),
    # which is fine: these are escaping transforms, not censorship.
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_playback_viewer_html(payload: dict[str, Any]) -> str:
    """Render the self-contained HTML viewer for a prepared payload.

    Inline CSS + inline JS + one embedded JSON payload. No external
    script, stylesheet, font, image link, or network reference. All
    authored prose is HTML-escaped and guard-checked; the dynamic
    evidence is built client-side from the embedded JSON.
    """
    pkg = payload.get("package", {})
    media = payload.get("media", {})
    validation = payload.get("validation", {})
    lock = payload.get("lock", {})
    timeline = payload.get("timeline", {})
    keyframes = payload.get("keyframes", {})
    index_v1 = payload.get("index_v1", {})
    profile_v1 = payload.get("profile_v1", {})
    caveats = payload.get("caveats", [])

    package_id = str(pkg.get("package_id", "(unknown)"))

    intro = (
        f"Generated by {TOOL_NAME} {TOOL_VERSION} from local package contents. "
        "MP4 plays media; CLULatent plays media plus evidence. This viewer shows candidate "
        "evidence the package recorded and lets you seek to it. It runs no visual AI, no "
        "captioning, no scene inference, no OCR, no motion analysis, and no network access."
    )
    safety_intro = (
        "This viewer displays package facts and timestamped evidence. "
        "It does not certify scene meaning."
    )
    keyframe_note = (
        "Keyframes are evidence, not interpretation. Click a thumbnail to seek the media."
    )
    notices_note = (
        "These notices describe how the evidence was read. They are not claims about the clip."
    )

    # Guard everything this module authored (not package data).
    _assert_safe_authored(
        intro,
        safety_intro,
        keyframe_note,
        notices_note,
        *[str(c) for c in caveats],
        *_VIEWER_CAVEATS,
    )

    # --- header table ---
    def _row(label: str, value: str) -> str:
        return f"<tr><th>{_esc(label)}</th><td>{value}</td></tr>"

    valid = bool(validation.get("valid"))
    valid_badge = (
        f'<span class="badge ok">valid</span>'
        if valid
        else '<span class="badge bad">not valid</span>'
    )
    valid_detail = (
        f' ({_esc(validation.get("error_count", 0))} error(s), '
        f'{_esc(validation.get("warning_count", 0))} warning(s))'
    )
    lock_status = str(lock.get("status", "unknown"))
    lock_class = "ok" if lock_status in {"valid", "locked"} else "warn"
    profile_status = str(profile_v1.get("status", "UNKNOWN"))
    profile_class = "ok" if profile_status == "COMPATIBLE" else (
        "bad" if profile_status == "INCOMPATIBLE" else "warn"
    )
    index_status = str(index_v1.get("verification_status", "MISSING"))
    index_class = "ok" if index_status == "FRESH" else (
        "bad" if index_status == "INVALID" else "warn"
    )

    header_rows = "".join(
        [
            _row("Package id", f"<code>{_esc(package_id)}</code>"),
            _row("Source filename", _esc(pkg.get("source_filename") or "(not recorded)")),
            _row("Status", _esc(pkg.get("status") or "(unknown)")),
            _row("Duration", f"{_esc(pkg.get('duration_ms', 0))} ms"),
            _row(
                "Dimensions",
                f"{_esc(pkg.get('width', 0))}x{_esc(pkg.get('height', 0))}",
            ),
            _row("Has audio", _esc(pkg.get("has_audio"))),
            _row("Created at", _esc(pkg.get("created_at") or "(unknown)")),
            _row("clulatent version", _esc(pkg.get("clulatent_version") or "(unknown)")),
            _row("Validation", f"{valid_badge}{valid_detail}"),
            _row(
                "V1 profile",
                f'<span class="badge {profile_class}">{_esc(profile_status)}</span>',
            ),
            _row("Lock", f'<span class="badge {lock_class}">{_esc(lock_status)}</span>'),
            _row(
                "Tracks",
                _esc(len(payload.get("tracks", []))),
            ),
            _row(
                "V1 index",
                f'<span class="badge {index_class}">{_esc(index_status)}</span>',
            ),
        ]
    )

    # --- media block ---
    if media.get("playable") and media.get("href"):
        mime = media.get("mime_type")
        source_tag = f'<source src="{_esc(media["href"])}"'
        if isinstance(mime, str):
            source_tag += f' type="{_esc(mime)}"'
        source_tag += ">"
        media_html = (
            f'<video id="player" controls preload="metadata">{source_tag}'
            "Your browser cannot play this media file directly."
            "</video>"
        )
    else:
        media_html = (
            '<div class="media-missing">Media is not directly playable from this viewer. '
            "The evidence timeline below still works.</div>"
        )

    # --- track toggles + timeline ---
    toggles_html = '<div class="toggles" id="toggles"></div>'
    timeline_html = (
        '<div id="timeline"><div id="playhead" style="left:0"></div></div>'
        '<div class="axis"><span>0 ms</span>'
        f'<span>{_esc(pkg.get("duration_ms", 0))} ms</span></div>'
    )

    timeline_trunc = ""
    if timeline.get("truncated"):
        timeline_trunc = (
            f'<p class="sub">Showing the first {_esc(timeline.get("shown", 0))} of '
            f'{_esc(timeline.get("total", 0))} events (output is bounded).</p>'
        )

    # --- keyframe strip ---
    if keyframes.get("events"):
        strip_trunc = ""
        if keyframes.get("truncated"):
            strip_trunc = (
                f'<p class="sub">Showing the first {_esc(keyframes.get("shown", 0))} of '
                f'{_esc(keyframes.get("total", 0))} keyframes.</p>'
            )
        strip_html = (
            f'<p class="sub">{_esc(keyframe_note)}</p>{strip_trunc}'
            '<div class="strip" id="strip"></div>'
        )
    else:
        strip_html = '<p class="sub muted">No keyframe images are available for a strip.</p>'

    # --- index links ---
    index_html = '<p class="sub muted">This package has no built-in V1 index.</p>'
    if index_v1.get("present"):
        links = []
        for art in index_v1.get("artifacts", []):
            name = art.get("name") or art.get("path") or "artifact"
            href = art.get("href")
            if isinstance(href, str) and href:
                links.append(f'<a href="{_esc(href)}">{_esc(name)}</a>')
            else:
                links.append(f'<span class="muted">{_esc(name)}</span>')
        index_html = '<div class="links">' + "".join(links) + "</div>"

    # --- caveats ---
    caveat_items = "".join(f"<li>{_esc(c)}</li>" for c in caveats)

    # --- notices ---
    warnings = payload.get("warnings", [])
    notices_html = ""
    if warnings:
        items = "".join(
            f"<li>{_esc(_truncate(str(w), _MAX_TEXT_CHARS))}</li>" for w in warnings
        )
        notices_html = (
            "<h2>Notices</h2>"
            f'<p class="sub">{_esc(notices_note)}</p>'
            f'<ul class="caveats">{items}</ul>'
        )

    data_blob = _embed_json(payload)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CLULatent V1 evidence playback \u2014 {_esc(package_id)}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<h1>CLULatent V1 evidence playback</h1>
<p class="sub">{_esc(intro)}</p>

<div class="panel">
<strong>{_esc(safety_intro)}</strong>
<ul class="caveats">{caveat_items}</ul>
</div>

<h2>Package</h2>
<div class="panel"><table class="kv"><tbody>{header_rows}</tbody></table></div>

<h2>Media</h2>
<div class="panel">{media_html}</div>

<h2>Evidence timeline</h2>
{toggles_html}
{timeline_html}
{timeline_trunc}

<h2>Event details</h2>
<div class="panel"><div id="details"><span class="muted">Click a marker or keyframe to \
inspect an event.</span></div></div>

<h2>Keyframes</h2>
<div class="panel">{strip_html}</div>

<h2>V1 index</h2>
<div class="panel">{index_html}</div>

{notices_html}
</main>

<script type="application/json" id="clulatent-viewer-data">{data_blob}</script>
<script>{_VIEWER_JS}</script>
</body>
</html>
"""
    return doc


_VIEWER_JS = r"""
(function () {
  "use strict";
  var el = document.getElementById("clulatent-viewer-data");
  var data;
  try { data = JSON.parse(el.textContent); } catch (e) { return; }

  var pkg = data.package || {};
  var duration = Number(pkg.duration_ms) || 0;
  var timeline = (data.timeline && data.timeline.events) || [];
  var keyframes = (data.keyframes && data.keyframes.events) || [];

  var player = document.getElementById("player");
  var timelineEl = document.getElementById("timeline");
  var playhead = document.getElementById("playhead");
  var togglesEl = document.getElementById("toggles");
  var detailsEl = document.getElementById("details");
  var stripEl = document.getElementById("strip");

  var palette = ["#4aa3ff", "#ffb74a", "#9b6dff", "#4ad0c0", "#ff6d9b",
                 "#8bc34a", "#e57373", "#b0bec5"];
  var trackColors = {};
  var trackList = [];
  timeline.forEach(function (ev) {
    if (trackColors[ev.track] === undefined) {
      trackColors[ev.track] = palette[trackList.length % palette.length];
      trackList.push(ev.track);
    }
  });

  var visible = {};
  trackList.forEach(function (t) { visible[t] = true; });

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function seek(ms) {
    if (player && isFinite(ms)) {
      try { player.currentTime = Math.max(0, ms / 1000); } catch (e) {}
    }
    if (duration > 0) {
      playhead.style.left = (Math.max(0, Math.min(ms, duration)) / duration * 100) + "%";
    }
  }

  function showDetails(ev) {
    var rows = "";
    function row(k, v, copyable) {
      var copy = copyable ? " <button class='copy' data-copy=\"" +
                 esc(v) + "\" type='button'>Copy</button>" : "";
      rows += "<tr><td>" + esc(k) + "</td><td>" + esc(v) + copy + "</td></tr>";
    }
    row("Event id", ev.id, true);
    row("Track", ev.track);
    row("Type", ev.type);
    row("Start (ms)", ev.t_start_ms, true);
    row("End (ms)", ev.t_end_ms, true);
    if (ev.confidence !== null && ev.confidence !== undefined) row("Confidence", ev.confidence);
    var fields = ev.fields || {};
    Object.keys(fields).forEach(function (k) { row(k, fields[k]); });
    var raw = "<details class='raw'><summary>Raw event JSON</summary><pre>" +
              esc(JSON.stringify(ev, null, 2)) + "</pre></details>";
    detailsEl.innerHTML = "<table><tbody>" + rows + "</tbody></table>" + raw;
    Array.prototype.slice.call(detailsEl.querySelectorAll("[data-copy]")).forEach(
      function (button) {
        button.addEventListener("click", function () {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(button.getAttribute("data-copy")).then(
              function () { button.textContent = "Copied"; },
              function () { button.textContent = "Copy unavailable"; }
            );
          } else {
            button.textContent = "Copy unavailable";
          }
        });
      }
    );
  }

  function renderMarkers() {
    // Remove existing markers (keep the playhead).
    Array.prototype.slice.call(timelineEl.querySelectorAll(".marker")).forEach(function (m) {
      m.parentNode.removeChild(m);
    });
    timeline.forEach(function (ev) {
      if (!visible[ev.track]) return;
      var m = document.createElement("button");
      m.className = "marker";
      m.type = "button";
      m.setAttribute("aria-label", ev.track + " at " + ev.t_start_ms + " milliseconds");
      var pos = duration > 0 ? (ev.t_start_ms / duration * 100) : 0;
      m.style.left = Math.max(0, Math.min(100, pos)) + "%";
      m.style.background = trackColors[ev.track] || "#4aa3ff";
      m.title = ev.track + " @ " + ev.t_start_ms + " ms";
      m.addEventListener("click", function () { seek(ev.t_start_ms); showDetails(ev); });
      timelineEl.appendChild(m);
    });
  }

  // Track toggles.
  trackList.forEach(function (t) {
    var label = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = true;
    cb.addEventListener("change", function () { visible[t] = cb.checked; renderMarkers(); });
    var sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = trackColors[t];
    label.appendChild(cb);
    label.appendChild(sw);
    label.appendChild(document.createTextNode(t));
    togglesEl.appendChild(label);
  });

  renderMarkers();

  // Keyframe strip.
  if (stripEl) {
    keyframes.forEach(function (kf) {
      var cell = document.createElement("button");
      cell.className = "kf";
      cell.type = "button";
      cell.setAttribute("aria-label", "Keyframe " + kf.id + " at " + kf.t_start_ms + " milliseconds");
      var inner;
      if (kf.href) {
        inner = "<img src='" + esc(kf.href) + "' alt='keyframe " + esc(kf.id) + "'>";
      } else {
        inner = "<div class='miss'>image unavailable</div>";
      }
      cell.innerHTML = inner + "<small>" + esc(kf.t_start_ms) + " ms</small>";
      cell.addEventListener("click", function () { seek(kf.t_start_ms); });
      stripEl.appendChild(cell);
    });
  }

  // Keep the playhead following the media.
  if (player) {
    player.addEventListener("timeupdate", function () {
      if (duration > 0) {
        playhead.style.left = (player.currentTime * 1000 / duration * 100) + "%";
      }
    });
  }
})();
"""


# --- top-level build / write -------------------------------------------------


def build_playback_viewer(
    package_path: Path | str,
    *,
    output_path: Path | str | None = None,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_keyframes: int = DEFAULT_MAX_KEYFRAMES,
    limits: Limits = DEFAULT_LIMITS,
) -> PlaybackViewerResult:
    """Build the viewer (payload + HTML) without writing anything.

    Read-only with respect to the package. `output_path` is used only to
    compute relative media/keyframe/index links.
    """
    payload = build_playback_viewer_payload(
        package_path,
        output_path=output_path,
        max_events=max_events,
        max_keyframes=max_keyframes,
        limits=limits,
    )
    html = render_playback_viewer_html(payload)
    return PlaybackViewerResult(
        html=html,
        payload=payload,
        warnings=list(payload.get("warnings", [])),
        output_path=Path(output_path) if output_path is not None else None,
    )


def write_playback_viewer(
    package_path: Path | str,
    output_path: Path | str,
    *,
    max_events: int = DEFAULT_MAX_EVENTS,
    max_keyframes: int = DEFAULT_MAX_KEYFRAMES,
    force: bool = False,
    limits: Limits = DEFAULT_LIMITS,
) -> PlaybackViewerResult:
    """Build the viewer and write it to `output_path`.

    Never mutates the package: the only file written is `output_path`.
    Raises `PlaybackViewerError` if `output_path` already exists without
    `force`, is a directory, or its parent directory does not exist.
    """
    output_path = Path(output_path)
    if output_path.exists() and output_path.is_dir():
        raise PlaybackViewerError(f"output path is a directory, not a file: {output_path}")
    if output_path.exists() and not force:
        raise PlaybackViewerError(
            f"output file already exists (use force to overwrite): {output_path}"
        )
    if not output_path.parent.exists() or not output_path.parent.is_dir():
        raise PlaybackViewerError(f"output directory does not exist: {output_path.parent}")

    result = build_playback_viewer(
        package_path,
        output_path=output_path,
        max_events=max_events,
        max_keyframes=max_keyframes,
        limits=limits,
    )
    try:
        output_path.write_text(result.html, encoding="utf-8")
    except OSError as exc:
        raise PlaybackViewerError(f"could not write viewer to {output_path}: {exc}") from exc
    result.output_path = output_path
    return result
