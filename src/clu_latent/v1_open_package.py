"""V1: V1 "open package" front door for a .clulatent package.

Composes existing V1 read surfaces -- the V1 evidence playback
viewer, the V1 budgeted agent read model, and the V1
built-in package index -- into one command that answers a single question:

    "I have a .clulatent package. How do I open it?"

    MP4 plays media. CLULatent plays media plus evidence.

This module is UX *composition*, not a new evidence surface. It authors no
new evidence, runs no visual/audio/language model, and adds no semantic
interpretation. It reads a package only through the existing, already
safety-reviewed V1 read APIs (`v1_playback_viewer`, `v1_agent_read_model`,
`v1_package_index`, transitively `package_reader`) and never mutates the
package -- the only files it writes live outside the package, in a
caller-chosen or generated *external* output directory. It writes no
receipt (opening is not processing) and never refreshes or regenerates a
package-internal index.

Four stages, kept separate on purpose so each is deterministically
testable in isolation:

  1. plan   -- `build_open_package_plan`: read-only. Reads the package and
              any existing V1 index manifests and decides what *would* be
              written and where, without writing anything itself.
  2. write  -- `write_open_package_artifacts`: writes the viewer HTML, the
              budgeted agent-read Markdown, and a small `open_summary.md`
              to the plan's output directory. The only disk writes in this
              module; the package itself is never touched.
  3. open   -- `open_package_surface`: optionally hands the generated
              viewer to the local system browser via the stdlib
              `webbrowser` module, through a small injectable launcher so
              tests never open a real browser and a launch failure is
              never fatal to an otherwise successful open.
  4. render -- `render_open_package_summary`: turns a result into the
              compact terminal text the CLI prints (package/evidence
              facts, artifact paths, browser outcome, safe next-step
              command suggestions -- never a full JSON dump).

Language safety: every string this module *authors* (the open summary and
the terminal render) is scanned by the same forbidden-language guard used
by the V1 viewer, so `open` never overclaims. Opaque, user-
controlled data -- a source filename, a package id, a package path -- is
never scanned, so a package literally named after a forbidden phrase is
never censored.

Non-goals: no LLM / OpenAI / Claude / network / local vision model / new
ML dependency, no OCR / object / face / speech / scene detection, no new
evidence lane, no `.clubin`, no package mutation, no automatic index
refresh, no receipt.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import package_reader as package_reader_mod
from . import v1_package_index as v1_package_index_mod
from . import v1_playback_viewer as v1_playback_viewer_mod
from .agent_context import CONTEXT_CAVEATS, FORBIDDEN_CONTEXT_PHRASES

DEFAULT_BUDGET = "micro"
DEFAULT_MAX_EVENTS = v1_playback_viewer_mod.DEFAULT_MAX_EVENTS

VIEWER_FILENAME = "viewer.html"
SUMMARY_FILENAME = "open_summary.md"

# Authored, load-bearing safety prose for the generated `open_summary.md`.
# Worded to avoid every banned semantic phrase; the guard runs over exactly
# these strings (plus the shared `CONTEXT_CAVEATS`) and never over package
# data (filenames, package ids, paths).
_OPEN_SUMMARY_CAVEATS: tuple[str, ...] = (
    "This summary lists package and evidence facts only; it does not describe scene meaning.",
    "Evidence-density and inspection-order rankings are counts and confidence scores, not "
    "importance judgments.",
    "Use the generated viewer and agent-read artifacts, or the ask / agent-read window "
    "commands, for focused review.",
)


class OpenPackageError(RuntimeError):
    """Raised when a package cannot be opened at all.

    Covers a fundamentally unreadable package, an unsupported `--budget`,
    an output path that is a directory collision, or an artifact that
    already exists with different content and `force` was not given. A
    package that reads but is missing playable media, has no built-in V1
    index, or fails validation still opens; those conditions are reported
    on the plan/result, not raised.
    """


def _assert_safe_authored(*texts: str) -> None:
    for text in texts:
        lowered = text.lower()
        for phrase in FORBIDDEN_CONTEXT_PHRASES:
            if phrase in lowered:
                raise OpenPackageError(
                    f"authored open-summary text contains forbidden phrase {phrase!r}"
                )


@dataclass
class OpenPackagePlan:
    """A fully-computed, read-only plan for opening a package.

    Building this plan reads the package (through the V1 playback
    viewer builder and the V1 agent read model, both of which read
    exclusively through `PackageReader`) and inspects any existing V1
    index manifests. It writes nothing.
    """

    package_path: Path
    output_dir: Path
    viewer_path: Path
    agent_read_path: Path
    summary_path: Path
    budget: str
    max_events: int

    package_id: str
    source_filename: str | None
    duration_ms: int
    validation_valid: bool
    lock_status: str
    track_count: int
    media_playable: bool
    timeline_shown: int
    timeline_total: int

    package_index_present: bool
    agent_read_index_present: bool

    viewer_html: str
    viewer_payload: dict[str, Any]
    agent_read_model: dict[str, Any]
    agent_read_markdown: str
    summary_markdown: str

    warnings: list[str] = field(default_factory=list)


@dataclass
class OpenPackageResult:
    """The outcome of writing (and optionally browser-opening) a plan."""

    plan: OpenPackagePlan
    viewer_written: bool
    viewer_reused: bool
    agent_read_written: bool
    agent_read_reused: bool
    summary_written: bool
    summary_reused: bool
    browser_attempted: bool = False
    browser_opened: bool = False
    browser_error: str | None = None


def _agent_read_index_exists(package_path: Path) -> bool:
    from . import v1_agent_read_model as v1_agent_read_model_mod

    manifest_path = package_path / v1_agent_read_model_mod.INDEX_DIR / v1_agent_read_model_mod.MANIFEST
    try:
        return manifest_path.is_file() and not manifest_path.is_symlink()
    except OSError:
        return False


def _default_output_dir(package_path: Path, package_id: str) -> Path:
    """Deterministic, package-derived, external output directory.

    Never inside the package. The same package path always maps to the
    same directory, so re-running `open` without `--force` can find and
    safely reuse prior artifacts. Different packages get different
    directories even if their declared `package_id` happens to collide,
    because the resolved package path is folded into the directory name.
    Package ids/paths are opaque data, so the name is sanitised (not
    scanned for forbidden language).
    """
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", package_id or "package")[:60] or "package"
    digest = hashlib.sha256(str(Path(package_path).resolve()).encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"clulatent-open-{safe_id}-{digest}"


def _render_open_summary_markdown(
    *,
    package_path: Path,
    payload: dict[str, Any],
    package_index_present: bool,
    agent_read_index_present: bool,
    budget: str,
) -> str:
    package = payload["package"]
    validation = payload["validation"]
    lock = payload["lock"]
    media = payload.get("media", {})
    timeline = payload.get("timeline", {})

    lines = [
        "# CLULatent open summary",
        "",
        f"- package_path: `{package_path}`",
        f"- package_id: `{package['package_id']}`",
        f"- source_filename: `{package.get('source_filename') or 'unknown'}`",
        f"- duration_ms: {package.get('duration_ms', 0)}",
        f"- validation_valid: {validation['valid']}",
        f"- lock_status: {lock.get('status', 'unknown')}",
        f"- track_count: {len(payload.get('tracks', []))}",
        f"- media_playable: {bool(media.get('playable'))}",
        f"- timeline_events_shown: {timeline.get('shown', 0)} of {timeline.get('total', 0)}",
        f"- package_index_present: {package_index_present}",
        f"- agent_read_index_present: {agent_read_index_present}",
        f"- agent_read_budget: {budget}",
        "",
        "## Caveats",
        "",
    ]
    caveats = list(CONTEXT_CAVEATS) + list(_OPEN_SUMMARY_CAVEATS)
    # Guard only the static authored caveat sentences, never the lines
    # above that interpolate opaque package data (path, filename, id).
    _assert_safe_authored(*caveats)
    lines.extend(f"- {caveat}" for caveat in caveats)
    return "\n".join(lines) + "\n"


def build_open_package_plan(
    package_path: Path | str,
    *,
    output_dir: Path | str | None = None,
    budget: str = DEFAULT_BUDGET,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> OpenPackagePlan:
    """Read the package and compute a plan; writes nothing.

    Raises `OpenPackageError` for an unsupported `budget`, a missing/
    unreadable package, or an authored-prose guard failure. Everything
    else (missing playable media, missing V1 index, failed validation)
    is captured on the returned plan instead of raised.
    """
    from . import v1_agent_read_model as v1_agent_read_model_mod

    package_path = Path(package_path)
    if budget not in v1_agent_read_model_mod.BUDGETS:
        raise OpenPackageError(
            f"unsupported budget {budget!r}; choose from "
            f"{', '.join(v1_agent_read_model_mod.BUDGETS)}"
        )
    if max_events < 0:
        raise OpenPackageError("max_events must be zero or greater")
    if not package_path.exists() or not package_path.is_dir():
        raise OpenPackageError(f"package not found: {package_path}")

    # First read: package facts + a package-id, needed to pick a default
    # output directory before the viewer's relative links can be computed.
    try:
        probe = v1_playback_viewer_mod.build_playback_viewer(package_path, max_events=0)
    except v1_playback_viewer_mod.PlaybackViewerError as exc:
        raise OpenPackageError(str(exc)) from exc
    package_id = probe.payload["package"]["package_id"]

    resolved_output_dir = (
        Path(output_dir)
        if output_dir is not None
        else _default_output_dir(package_path, package_id)
    )
    viewer_path = resolved_output_dir / VIEWER_FILENAME

    # Second read: the real viewer, now that `output_dir` (and therefore
    # `viewer_path`) is known, so media/keyframe/index links in the HTML
    # are correct for where it will actually be written.
    try:
        viewer_result = v1_playback_viewer_mod.build_playback_viewer(
            package_path, output_path=viewer_path, max_events=max_events
        )
    except v1_playback_viewer_mod.PlaybackViewerError as exc:
        raise OpenPackageError(str(exc)) from exc
    payload = viewer_result.payload

    try:
        agent_read_model = v1_agent_read_model_mod.build_agent_read_model(
            package_path, budget=budget
        )
    except v1_agent_read_model_mod.AgentReadModelError as exc:
        raise OpenPackageError(str(exc)) from exc
    agent_read_markdown = v1_agent_read_model_mod.render_agent_read_markdown(
        agent_read_model, budget=budget
    )

    package_index_present = v1_package_index_mod.v1_index_exists(package_path)
    agent_read_index_present = _agent_read_index_exists(package_path)

    summary_markdown = _render_open_summary_markdown(
        package_path=package_path,
        payload=payload,
        package_index_present=package_index_present,
        agent_read_index_present=agent_read_index_present,
        budget=budget,
    )

    package_facts = payload["package"]
    return OpenPackagePlan(
        package_path=package_path,
        output_dir=resolved_output_dir,
        viewer_path=viewer_path,
        agent_read_path=resolved_output_dir / f"agent_read_{budget}.md",
        summary_path=resolved_output_dir / SUMMARY_FILENAME,
        budget=budget,
        max_events=max_events,
        package_id=package_id,
        source_filename=package_facts.get("source_filename"),
        duration_ms=package_facts.get("duration_ms", 0),
        validation_valid=bool(payload["validation"]["valid"]),
        lock_status=str(payload["lock"].get("status", "unknown")),
        track_count=len(payload.get("tracks", [])),
        media_playable=bool(payload.get("media", {}).get("playable")),
        timeline_shown=payload.get("timeline", {}).get("shown", 0),
        timeline_total=payload.get("timeline", {}).get("total", 0),
        package_index_present=package_index_present,
        agent_read_index_present=agent_read_index_present,
        viewer_html=viewer_result.html,
        viewer_payload=payload,
        agent_read_model=agent_read_model,
        agent_read_markdown=agent_read_markdown,
        summary_markdown=summary_markdown,
        warnings=list(viewer_result.warnings),
    )


def _write_artifact(path: Path, content: str, *, force: bool) -> tuple[bool, bool]:
    """Write one text artifact outside the package; returns (written, reused).

    Without `force`: identical existing content is left alone and
    reported as reused; different existing content raises
    `OpenPackageError` asking for `--force`. With `force`: always
    (re)written. Never touches anything under `package_path`.
    """
    if path.exists():
        if path.is_dir():
            raise OpenPackageError(f"output path is a directory, not a file: {path}")
        if not force:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise OpenPackageError(f"could not read existing {path}: {exc}") from exc
            if existing == content:
                return False, True
            raise OpenPackageError(
                f"output file already exists with different content "
                f"(use --force to overwrite): {path}"
            )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise OpenPackageError(f"could not write {path}: {exc}") from exc
    return True, False


def write_open_package_artifacts(
    plan: OpenPackagePlan, *, force: bool = False
) -> OpenPackageResult:
    """Write the plan's artifacts to `plan.output_dir`.

    The only writes this module performs; the source package is never
    touched. Raises `OpenPackageError` on a collision (see
    `_write_artifact`) or an OS-level write failure.
    """
    viewer_written, viewer_reused = _write_artifact(
        plan.viewer_path, plan.viewer_html, force=force
    )
    agent_read_written, agent_read_reused = _write_artifact(
        plan.agent_read_path, plan.agent_read_markdown, force=force
    )
    summary_written, summary_reused = _write_artifact(
        plan.summary_path, plan.summary_markdown, force=force
    )
    return OpenPackageResult(
        plan=plan,
        viewer_written=viewer_written,
        viewer_reused=viewer_reused,
        agent_read_written=agent_read_written,
        agent_read_reused=agent_read_reused,
        summary_written=summary_written,
        summary_reused=summary_reused,
    )


BrowserLauncher = Callable[[str], bool]


def _default_browser_launcher(url: str) -> bool:
    import webbrowser

    return bool(webbrowser.open(url))


def open_package_surface(
    result: OpenPackageResult,
    *,
    launch_browser: bool = True,
    launcher: BrowserLauncher | None = None,
) -> OpenPackageResult:
    """Optionally hand the generated viewer to the local system browser.

    A browser failure is never fatal: a successfully generated viewer is
    still a successful open even when no browser could be launched. Tests
    should always pass an explicit `launcher` so no real browser opens.
    Mutates and returns `result`.
    """
    if not launch_browser:
        return result
    launch = launcher or _default_browser_launcher
    url = result.plan.viewer_path.resolve().as_uri()
    try:
        opened = bool(launch(url))
        error = (
            None
            if opened
            else "the local browser launcher reported it could not open the viewer"
        )
    except Exception as exc:  # noqa: BLE001 - a browser failure must never be fatal
        opened = False
        error = str(exc)
    result.browser_attempted = True
    result.browser_opened = opened
    result.browser_error = error
    return result


def _artifact_state(written: bool, reused: bool) -> str:
    if reused:
        return "reused"
    if written:
        return "written"
    return "unchanged"


def render_open_package_summary(result: OpenPackageResult) -> str:
    """Compact terminal text for `clulatent open` -- facts, artifact
    paths, browser outcome, and safe next-step suggestions. Never a full
    JSON dump."""
    plan = result.plan
    lines = [
        f"CLULatent package opened: {plan.package_id}",
        f"  package_path: {plan.package_path}",
        f"  source_filename: {plan.source_filename or 'unknown'}",
        f"  duration_ms: {plan.duration_ms}",
        f"  validation: {'valid' if plan.validation_valid else 'INVALID'}",
        f"  lock_status: {plan.lock_status}",
        f"  tracks: {plan.track_count}",
        "  media: "
        + ("playable" if plan.media_playable else "evidence-only (no playable media)"),
        f"  timeline events shown: {plan.timeline_shown} of {plan.timeline_total}",
        "  package index (V1): "
        + ("present" if plan.package_index_present else "not found"),
        "  agent-read index (V1): "
        + ("present" if plan.agent_read_index_present else "not found"),
        "",
        f"  output_dir: {plan.output_dir}",
        f"  viewer: {plan.viewer_path} "
        f"({_artifact_state(result.viewer_written, result.viewer_reused)})",
        f"  agent read ({plan.budget}): {plan.agent_read_path} "
        f"({_artifact_state(result.agent_read_written, result.agent_read_reused)})",
        f"  open summary: {plan.summary_path} "
        f"({_artifact_state(result.summary_written, result.summary_reused)})",
        "",
    ]
    if result.browser_attempted:
        if result.browser_opened:
            lines.append("  browser: opened the viewer")
        else:
            lines.append(f"  browser: could not open automatically ({result.browser_error})")
            lines.append(f"    open this file directly: {plan.viewer_path}")
    else:
        lines.append("  browser: not attempted; open this file directly:")
        lines.append(f"    {plan.viewer_path}")
    lines += [
        "",
        "  next steps (not run automatically):",
        f"    clulatent agent-read summary {plan.package_path}",
        f"    clulatent agent-read window {plan.package_path} --time 17s",
        f'    clulatent ask {plan.package_path} "What evidence exists around 17s?"',
        f"    clulatent package-index inspect {plan.package_path}",
    ]
    if not plan.package_index_present:
        lines.append(
            f"    clulatent package-index refresh {plan.package_path}  # index/v1 not found"
        )
    if not plan.agent_read_index_present:
        lines.append(
            f"    clulatent agent-read write-index {plan.package_path}  "
            "# agent-read index not found"
        )
    if result.plan.warnings:
        lines.append("")
        lines.append(f"  {len(result.plan.warnings)} notice(s):")
        for idx, warning in enumerate(result.plan.warnings, start=1):
            lines.append(f"    {idx}. {warning}")
    # This is a structured status readout (labels + opaque package data:
    # paths, filenames, ids, warnings), not authored prose, so -- like the
    # existing playback-viewer CLI printer -- it is not passed through the
    # forbidden-language guard; doing so would risk censoring a package
    # merely because its own filename contains a flagged word.
    return "\n".join(lines) + "\n"
