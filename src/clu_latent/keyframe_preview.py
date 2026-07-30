"""Phase 3.12: a static, local-first keyframe contact sheet for a package.

This module renders the keyframes already stored in a `.clulatent`
package into a single, self-contained HTML "contact sheet" so a human --
or a future LLM-adapter -- can inspect the *visual evidence* (the frame
sequence) of a clip without a Studio UI, a web server, cloud services,
ML, or a plugin runtime.

Core rule (it governs everything below):

    Show visual evidence. Do not interpret visual evidence.

This is a **visual evidence browser, not a visual understanding
system**. It never runs visual AI, never captions a frame, never infers
scene meaning / object identity / intent / emotion, never runs OCR or
motion analysis, and never extracts new frames. It only reads keyframes
that ingest already recorded.

Read-only: it never mutates the package, never writes an index, receipt,
or lock file, and never touches anything inside `package_path` -- it
only reads already-canonical data (manifest, keyframes track) and
renders it as escaped, static HTML.

Safety mirrors `report.py`: every piece of package-originated text is
stripped of control characters and HTML-escaped before it reaches the
output document (see `_esc`, reused from `report.py`). The generated
document has no `<script>` tag, no external stylesheet/font/image link,
and no CDN/network reference; CSS is a small inline `<style>` block. The
number of rendered frames is bounded (`DEFAULT_MAX_KEYFRAMES`) so a
pathological package cannot produce an unbounded contact sheet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from . import tracks as tracks_mod
from .constants import TOOL_NAME, TOOL_VERSION
from .event import EventEnvelope
from .manifest import Manifest, TrackDescriptor
from .report import _esc, _truncate
from .security.jsonl import JsonlLimitError
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package
from .validate import ValidationReport, validate_package

KEYFRAMES_TRACK_NAME = "keyframes"

# Bound on the number of frames rendered into a single contact sheet so a
# pathological package (millions of keyframe records) cannot produce an
# unbounded HTML document.
DEFAULT_MAX_KEYFRAMES = 2000

# Bound on the length of any single path/id string echoed into the page.
_MAX_TEXT_CHARS = 300

# The two mandatory, non-negotiable caveats. They are plain evidence
# statements -- they contain no forbidden interpretation phrasing.
_EVIDENCE_CAVEAT = "Keyframes are evidence, not interpretation."
_NO_DESCRIPTION_CAVEAT = "This preview does not describe what happens in the clip."


class KeyframePreviewError(ValueError):
    """Raised when a keyframe contact sheet cannot be generated at all.

    Only raised for a fundamentally unreadable package (missing path,
    missing/invalid manifest.json). A package that exists and parses but
    fails other validation checks (bad hash, missing keyframe image,
    empty keyframes track, ...) still produces a contact sheet; the
    trust/validation caveat simply reflects the problem as evidence.
    """


@dataclass
class KeyframePreviewResult:
    html: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Frame:
    event_id: str
    frame_index: int | None
    t_start_ms: int
    t_end_ms: int
    package_relpath: str  # path as recorded in the package (evidence text)
    exists: bool
    href: str | None  # relative link usable from the output file, if resolvable


def _keyframes_descriptor(manifest: Manifest) -> TrackDescriptor | None:
    for track in manifest.tracks:
        if track.name == KEYFRAMES_TRACK_NAME:
            return track
    return None


def _load_keyframe_events(
    package_path: Path,
    descriptor: TrackDescriptor,
    *,
    limits: Limits,
    warnings: list[str],
) -> list[EventEnvelope]:
    try:
        track_path = resolve_in_package(
            package_path, descriptor.file, field_name=f"tracks[{descriptor.name}].file"
        )
    except PathSecurityError as exc:
        warnings.append(f"keyframes track path rejected: {exc}")
        return []
    try:
        return tracks_mod.read_track_file(track_path, limits=limits)
    except (tracks_mod.TrackReadError, JsonlLimitError) as exc:
        warnings.append(f"keyframes track could not be read cleanly ({exc})")
        return []


def _relative_href(
    package_path: Path, package_relpath: str, output_dir: Path | None
) -> str | None:
    """Return a browser-usable relative link to a keyframe image.

    The link is computed from the output HTML file's directory to the
    keyframe image *as it sits inside the package* -- i.e. from the
    un-resolved join ``package_path / package_relpath`` relative to
    ``output_dir``. Both sides are made absolute with ``os.path.abspath``
    (which normalises ``..`` lexically) rather than ``Path.resolve``
    (which follows symlinks): resolving only one side -- as the frozen
    Phase 3.12 code did -- produced broken links whenever the package or
    output lived under a symlinked directory (e.g. macOS ``/tmp`` ->
    ``/private/tmp``), so the link crossed symlink namespaces.

    Falls back to `None` (no live link, evidence text only) if a relative
    path cannot be computed. Never emits an absolute filesystem path or a
    network URL. Path components are URL-quoted (slashes preserved) so a
    keyframe filename containing spaces or other characters still yields a
    valid, safe relative href.
    """
    if output_dir is None:
        return None
    try:
        actual = os.path.abspath(os.path.join(os.fspath(package_path), package_relpath))
        base = os.path.abspath(os.fspath(output_dir))
        rel = os.path.relpath(actual, base)
    except (ValueError, OSError):
        return None
    # Normalise to POSIX separators, then URL-quote (keeping the path
    # separators) so the href is valid regardless of the filename.
    return quote(Path(rel).as_posix(), safe="/")


def _build_frame(
    event: EventEnvelope,
    package_path: Path,
    output_dir: Path | None,
    *,
    warnings: list[str],
) -> _Frame:
    payload = event.payload if isinstance(event.payload, dict) else {}

    raw_path = payload.get("path")
    package_relpath = raw_path if isinstance(raw_path, str) else ""

    raw_index = payload.get("frame_index")
    frame_index = raw_index if isinstance(raw_index, int) else None

    exists = False
    href: str | None = None
    if package_relpath:
        try:
            image_abs = resolve_in_package(
                package_path, package_relpath, field_name="keyframe.payload.path"
            )
        except PathSecurityError as exc:
            warnings.append(f"{event.id}: keyframe path rejected ({exc})")
            image_abs = None
        if image_abs is not None:
            try:
                exists = image_abs.is_file()
            except OSError:
                exists = False
            if exists:
                href = _relative_href(package_path, package_relpath, output_dir)

    return _Frame(
        event_id=event.id,
        frame_index=frame_index,
        t_start_ms=event.t_start_ms,
        t_end_ms=event.t_end_ms,
        package_relpath=package_relpath,
        exists=exists,
        href=href,
    )


def _format_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    total_seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _format_duration(ms: int) -> str:
    return f"{_format_timestamp(ms)} ({ms} ms)"


# --- HTML rendering ---------------------------------------------------------

_STYLE = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 1100px; padding: 0 1rem; color: #1a1a1a; background: #fff; }
h1 { font-size: 1.6rem; }
h2 { font-size: 1.2rem; margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.3rem; }
p.note { color: #555; font-style: italic; }
p.empty { color: #555; font-style: italic; }
.caveat { font-size: 1.05rem; background: #fff8e1; padding: 0.75rem 1rem; margin: 0.75rem 0;
          border-left: 4px solid #e0a800; }
.trust { background: #fdecea; border-left: 4px solid #c0392b; padding: 0.6rem 1rem; margin: 0.75rem 0; }
.trust.ok { background: #eafaf1; border-left-color: #27ae60; }
table.kv { border-collapse: collapse; margin: 0.5rem 0 1rem 0; font-size: 0.9rem; }
table.kv th, table.kv td { border: 1px solid #ddd; padding: 0.35rem 0.5rem; text-align: left; }
table.kv th { background: #fafafa; width: 220px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 1rem; margin-top: 1rem; }
.frame { border: 1px solid #ddd; border-radius: 4px; padding: 0.5rem; background: #fafafa; }
.frame img { width: 100%; height: auto; display: block; border: 1px solid #ccc; background: #fff; }
.frame .missing { width: 100%; padding: 2rem 0; text-align: center; color: #8a4b00;
                  background: #fff3cd; border: 1px dashed #e0a800; font-size: 0.85rem; }
.frame .meta { font-size: 0.8rem; margin-top: 0.4rem; line-height: 1.4; }
.frame .meta code { background: #eee; padding: 0.05rem 0.3rem; border-radius: 3px;
                    word-break: break-all; }
ul.warnings { padding-left: 1.2rem; }
ul.warnings li { color: #8a4b00; }
"""


def _render_frame(frame: _Frame) -> str:
    label_index = frame.frame_index if frame.frame_index is not None else "?"
    if frame.exists and frame.href is not None:
        image_html = f'<img src="{_esc(frame.href)}" alt="Keyframe {_esc(label_index)}">'
    elif frame.exists:
        # File is present but no relative link could be formed; show the
        # recorded path as evidence text rather than a broken link.
        image_html = '<div class="missing">Keyframe file present; link unavailable</div>'
    else:
        image_html = '<div class="missing">Missing keyframe image (evidence not found)</div>'

    relpath_text = frame.package_relpath or "(no path recorded)"
    meta = (
        f'<div class="meta">'
        f"<div>Timestamp: {_esc(_format_timestamp(frame.t_start_ms))}</div>"
        f"<div>Event id: {_esc(frame.event_id)}</div>"
        f"<div>Package path: <code>{_esc(_truncate(relpath_text, _MAX_TEXT_CHARS))}</code></div>"
        f"</div>"
    )
    return f'<div class="frame">{image_html}{meta}</div>'


def _render_trust(validation: ValidationReport) -> str:
    if validation.valid:
        return (
            '<div class="trust ok"><strong>Validation status:</strong> this package '
            "currently validates. Validation is reported as evidence only; it does not "
            "assert anything about what the frames depict.</div>"
        )
    error_count = len(validation.errors)
    warn_count = len(validation.warnings)
    return (
        '<div class="trust"><strong>Validation status:</strong> this package does not '
        f"validate ({error_count} error(s), {warn_count} warning(s)). The frames below are "
        "shown as recorded evidence and may be incomplete or unverified. Validation is "
        "reported as evidence only.</div>"
    )


def generate_contact_sheet(
    package_path: Path,
    *,
    output_path: Path | None = None,
    limits: Limits = DEFAULT_LIMITS,
    max_keyframes: int = DEFAULT_MAX_KEYFRAMES,
) -> KeyframePreviewResult:
    """Build a static HTML keyframe contact sheet for `package_path`.

    Read-only: only ever reads manifest.json and the keyframes track --
    never writes, creates, or mutates anything inside the package.

    `output_path`, when given, is used only to compute relative `<img>`
    links from the output file's directory to each keyframe image; it is
    never written to here (the CLI is responsible for writing the file).

    Raises `KeyframePreviewError` only when the package cannot be read at
    all (missing path, missing/invalid manifest.json). Any other problem
    (invalid package, empty keyframes track, missing images) is reflected
    in the page as evidence, not raised.
    """
    package_path = Path(package_path)
    output_dir = Path(output_path).parent if output_path is not None else None
    warnings: list[str] = []

    validation = validate_package(package_path, limits=limits)
    if validation.manifest is None:
        message = (
            validation.errors[0]
            if validation.errors
            else f"Package could not be read: {package_path}"
        )
        raise KeyframePreviewError(message)
    manifest = validation.manifest

    descriptor = _keyframes_descriptor(manifest)
    events: list[EventEnvelope] = []
    if descriptor is None:
        warnings.append("no keyframes track is declared in the manifest")
    else:
        events = _load_keyframe_events(
            package_path, descriptor, limits=limits, warnings=warnings
        )

    total_events = len(events)
    truncated = total_events > max_keyframes
    if truncated:
        warnings.append(
            f"keyframe count ({total_events}) exceeds the render bound "
            f"({max_keyframes}); showing the first {max_keyframes}"
        )
        events = events[:max_keyframes]

    frames = [
        _build_frame(event, package_path, output_dir, warnings=warnings)
        for event in events
    ]
    missing = sum(1 for frame in frames if not frame.exists)

    source = manifest.source
    kv_pairs = [
        ("Package id", _esc(manifest.package_id)),
        ("Source filename", _esc(source.filename) if source.filename else "(not recorded)"),
        ("Duration", _esc(_format_duration(source.duration_ms))),
        ("Keyframe count", _esc(total_events)),
    ]
    kv_rows = "".join(f"<tr><th>{key}</th><td>{value}</td></tr>" for key, value in kv_pairs)
    kv_table = f'<table class="kv"><tbody>{kv_rows}</tbody></table>'

    if frames:
        grid = '<div class="grid">' + "".join(_render_frame(frame) for frame in frames) + "</div>"
    else:
        grid = (
            '<p class="empty">No keyframes are stored in this package. There is no visual '
            "evidence to display.</p>"
        )

    notes: list[str] = []
    if truncated:
        notes.append(
            f'<p class="note">Only the first {_esc(max_keyframes)} of '
            f"{_esc(total_events)} keyframes are shown (output is bounded).</p>"
        )
    if missing:
        notes.append(
            f'<p class="note">{_esc(missing)} keyframe image(s) could not be found on disk '
            "and are shown as missing evidence.</p>"
        )

    warnings_section = ""
    if warnings:
        items = "".join(f"<li>{_esc(_truncate(w, _MAX_TEXT_CHARS))}</li>" for w in warnings)
        warnings_section = (
            "<h2>Notices</h2>\n"
            '<p class="note">These notices describe how the evidence was read. They are not '
            "claims about the clip.</p>\n"
            f'<ul class="warnings">{items}</ul>\n'
        )

    doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>CLULatent keyframe contact sheet \u2014 {_esc(manifest.package_id)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        "<h1>CLULatent keyframe contact sheet</h1>\n"
        f'<p class="note">Generated by {_esc(TOOL_NAME)} {_esc(TOOL_VERSION)} from local '
        "package contents. This is a visual evidence browser, not a visual understanding "
        "system: no visual AI, no frame captioning, no scene inference, no OCR, no motion "
        "analysis, and no network access are involved. Adapters produce evidence, not truth; "
        "generated does not mean canonical.</p>\n"
        f'<div class="caveat"><strong>{_esc(_EVIDENCE_CAVEAT)}</strong></div>\n'
        f'<div class="caveat"><strong>{_esc(_NO_DESCRIPTION_CAVEAT)}</strong></div>\n'
        "<h2>Package</h2>\n"
        f"{kv_table}\n"
        f"{_render_trust(validation)}\n"
        "<h2>Keyframes</h2>\n"
        + "".join(notes)
        + f"\n{grid}\n"
        + warnings_section
        + "</body>\n</html>\n"
    )

    return KeyframePreviewResult(html=doc, warnings=warnings)
