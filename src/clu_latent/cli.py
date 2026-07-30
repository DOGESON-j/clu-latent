"""clulatent: the V1 CLI.

CLULatent is NOT a video codec, and this CLI does NOT build CLUBIN
(the future compiled binary format). It only ingests a video into a
human-inspectable `.clulatent` package folder and lets you inspect it.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from . import agent_context as agent_context_mod
from . import agent_review as agent_review_mod
from . import agent_review_retrieval as agent_review_retrieval_mod
from . import agent_review_writer as agent_review_writer_mod
from . import analysis_lanes as analysis_lanes_mod
from . import analysis_writer as analysis_writer_mod
from . import archive as archive_mod
from . import audio_digest as audio_digest_mod
from . import audio_digest_retrieval as audio_digest_retrieval_mod
from . import audio_digest_writer as audio_digest_writer_mod
from . import changed_region as changed_region_mod
from . import changed_region_retrieval as changed_region_retrieval_mod
from . import changed_region_writer as changed_region_writer_mod
from . import evidence_bundle_retrieval as evidence_bundle_retrieval_mod
from . import evidence_bundle_writer as evidence_bundle_writer_mod
from . import index as index_mod
from . import keyframe_retrieval as keyframe_retrieval_mod
from . import lock as lock_mod
from . import package_reader as package_reader_mod
from . import review_writer as review_writer_mod
from . import tracks as tracks_mod
from . import v1_ask_bundle as v1_ask_bundle_mod
from . import v1_build as v1_build_mod
from . import v1_open_ask as v1_open_ask_mod
from . import v1_package_index as v1_package_index_mod
from . import v1_playback_viewer as v1_playback_viewer_mod
from . import visual_change as visual_change_mod
from . import visual_change_retrieval as visual_change_retrieval_mod
from . import visual_change_writer as visual_change_writer_mod
from .analysis_adapter_dry_run import (
    AdapterResultLoadError,
    DryRunReport,
    dry_run_adapter_result,
    dry_run_is_hard_package_error,
    load_adapter_result_json,
)
from .analysis_adapters import AnalysisAdapterError, write_adapter_result
from .analysis_ffmpeg_visual_change_adapter import (
    ADAPTER_NAME as VISUAL_CHANGE_ADAPTER_NAME,
)
from .analysis_ffmpeg_visual_change_adapter import (
    DEFAULT_MAX_EVENTS as VISUAL_CHANGE_DEFAULT_MAX_EVENTS,
    DEFAULT_THRESHOLD as VISUAL_CHANGE_DEFAULT_THRESHOLD,
    VisualChangeAdapterError,
    build_visual_change_adapter_result,
    visual_change_adapter_result_to_dict,
)
from .analysis_fixture_adapter import fixture_adapter_result_to_dict
from .constants import TOOL_NAME, TOOL_VERSION, WHISPER_DEFAULT_MODEL
from .doctor import run_doctor_checks
from .hash import read_sha256_sidecar, sha256_file
from .ingest import IngestError, ingest_video
from .keyframe_preview import KeyframePreviewError, generate_contact_sheet
from .manifest import Manifest
from .presentation import (
    make_console,
    print_json,
    render_doctor,
    render_registration_result,
    render_registration_status,
    render_welcome,
)
from .reindex import ReindexError, reindex_package
from .report import ReportError, generate_report
from .review_resolver import resolve_package_review_states, summarize_review_states
from .security.console import safe_console_text
from .security.jsonl import JsonlLimitError, iter_jsonl_bounded, read_bytes_bounded
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package
from .system_integration import ActionOutcome, get_system_integration
from .timecode import ms_to_timecode
from .validate import validate_package
from .version import version_report

app = typer.Typer(
    name="clulatent",
    help="CLULatent V1 CLI — ingest and inspect .clulatent packages.",
    add_completion=False,
    rich_markup_mode=None,
)
review_app = typer.Typer(
    name="review",
    help="Write additive review_events.jsonl entries (V1).",
    add_completion=False,
)
app.add_typer(review_app, name="review")
analysis_app = typer.Typer(
    name="analysis",
    help=(
        "Write already-produced analysis-lane events (V1 schema + "
        "V1 writer). No adapter runtime: this group never runs "
        "FFmpeg/OCR/an object detector/any ML model itself — it only "
        "validates and appends events a caller already produced."
    ),
    add_completion=False,
)
app.add_typer(analysis_app, name="analysis")
audio_digest_app = typer.Typer(
    name="audio-digest",
    help=(
        "Validate and write already-produced audio digest records (Phase "
        "3.4 schema + V1 writer). No audio adapter: this group "
        "never runs FFmpeg/librosa/Essentia/Demucs or any ML model -- it "
        "only validates and appends digest records a caller already "
        "produced."
    ),
    add_completion=False,
)
app.add_typer(audio_digest_app, name="audio-digest")
keyframes_app = typer.Typer(
    name="keyframes",
    help=(
        "Read-only keyframe evidence surfaces (V1). This group "
        "never runs visual AI, never captions frames, never infers scene "
        "meaning/object identity/intent, never runs OCR or motion "
        "analysis, and never extracts new frames -- it only renders "
        "keyframes ingest already recorded. Show visual evidence; do not "
        "interpret visual evidence."
    ),
    add_completion=False,
)
app.add_typer(keyframes_app, name="keyframes")
system_app = typer.Typer(
    name="system",
    help=(
        "OS-level system integration status/register/unregister (Phase "
        "3.14). Read-only status; register/unregister only ever report "
        "genuine outcomes -- never a false 'registered' claim."
    ),
    add_completion=False,
)
app.add_typer(system_app, name="system")
visual_change_app = typer.Typer(
    name="visual-change",
    help=(
        "Non-semantic visual change evidence lane (V1): bounded "
        "pixel-difference candidates between adjacent stored keyframes. "
        "This group never runs visual AI, never detects objects/faces/"
        "people, never runs OCR, never captions a frame, and never infers "
        "scene meaning, intent, or emotion -- it only measures how much "
        "two already-stored keyframe images differ, numerically."
    ),
    add_completion=False,
)
app.add_typer(visual_change_app, name="visual-change")
changed_regions_app = typer.Typer(
    name="changed-regions",
    help=(
        "Non-semantic changed-region evidence lane (V1): for each "
        "V1 visual change candidate, localizes the strongest "
        "grid-cell region of difference between its linked source/target "
        "keyframe images. This group never runs visual AI, never detects "
        "objects/faces/people/text/logos, never runs OCR, never captions "
        "a frame, and never infers scene meaning, intent, or action -- it "
        "only reports where a bounded pixel-difference candidate was "
        "strongest, numerically."
    ),
    add_completion=False,
)
app.add_typer(changed_regions_app, name="changed-regions")
evidence_bundles_app = typer.Typer(
    name="evidence-bundles",
    help=(
        "Evidence bundle lane (V1): collects already-existing "
        "package evidence (keyframes, visual change candidates, "
        "changed-region candidates, audio/speech events, audio digest "
        "events, review events, analysis lane events, receipts, "
        "validation status) for a bounded time range into one record. "
        "This group never runs visual AI, never decodes an image, never "
        "runs Pillow/FFmpeg/any ML model, and never infers what the "
        "collected evidence means -- evidence bundle, not semantic "
        "interpretation."
    ),
    add_completion=False,
)
app.add_typer(evidence_bundles_app, name="evidence-bundles")
agent_review_app = typer.Typer(
    name="agent-review",
    help=(
        "Agent review v0 (V1): a bounded, deterministic, "
        "rule-based review layer over exactly one evidence bundle. Never "
        "calls an external model, never adds an external agent runtime, "
        "never adds model adapters -- it inspects a bundle's already- "
        "computed evidence counts/coverage/missing-evidence fields and "
        "reports support, gaps, and next review steps. Agent review is "
        "review of evidence, not invention of truth."
    ),
    add_completion=False,
)
app.add_typer(agent_review_app, name="agent-review")
package_app = typer.Typer(
    name="package",
    help=(
        "Official read-only package reader (V1): open a "
        ".clulatent package and inspect its manifest, tracks, receipts, "
        "lock state, and validation status without knowing the internal "
        "folder layout. This group never mutates the package, never "
        "writes receipts, never touches lock state, and never runs "
        "FFmpeg/Pillow/any ML or network call -- it reads and exposes "
        "structured access; it does not interpret content."
    ),
    add_completion=False,
)
app.add_typer(package_app, name="package")
agent_context_app = typer.Typer(
    name="agent-context",
    help=(
        "Agent context export (V1): open a .clulatent package "
        "through the read-only reader and emit one stable JSON (or "
        "Markdown) document another agent can read to learn what candidate "
        "evidence exists, what it must not conclude, and what to safely do "
        "next. This group never mutates the package, never writes a "
        "receipt, never runs FFmpeg/Pillow/any ML or network call, and "
        "never interprets content -- candidate evidence only, no semantic "
        "interpretation."
    ),
    add_completion=False,
)
app.add_typer(agent_context_app, name="agent-context")
package_index_app = typer.Typer(
    name="package-index",
    help=(
        "Built-in V1 package index (V1): the self-describing "
        "index/v1/ artifacts inside a .clulatent package -- agent_context "
        "JSON/Markdown, a safe ask prompt, and an index manifest. 'refresh' "
        "regenerates them from the package's current tracks (writes no "
        "receipt and no new evidence lane); 'inspect' reports them "
        "read-only. Candidate evidence only, no semantic interpretation."
    ),
    add_completion=False,
)
app.add_typer(package_index_app, name="package-index")
agent_read_app = typer.Typer(
    name="agent-read",
    help="Token-budgeted evidence windows, safe rankings, and focused retrieval (V1).",
    add_completion=False,
    rich_markup_mode=None,
)
app.add_typer(agent_read_app, name="agent-read")
profile_app = typer.Typer(
    name="profile",
    help=(
        "The CLULatent V1 compatibility profile / format contract: "
        "'inspect v1' describes the contract; 'verify PACKAGE' checks compatibility. "
        "Both are read-only and make no semantic claim about media content."
    ),
    add_completion=False,
)
app.add_typer(profile_app, name="profile")
conformance_app = typer.Typer(
    name="conformance",
    help="Run deterministic, offline CLULatent V1 compatibility fixtures.",
    add_completion=False,
)
app.add_typer(conformance_app, name="conformance")
archive_app = typer.Typer(
    name="archive",
    help="Verify the deterministic portable .clulatent ZIP transport form.",
    add_completion=False,
)
app.add_typer(archive_app, name="archive")
console = Console()
error_console = Console(stderr=True, style="bold red")

_DEFAULT_ANALYSIS_ADAPTER_NAME = "manual-cli"


@app.callback(invoke_without_command=True)
def root_options(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed CLI version, V1 profile, and Python version.",
        is_eager=True,
    ),
) -> None:
    if version:
        typer.echo(version_report())
        raise typer.Exit()


def _fail(message: str) -> None:
    error_console.print(f"Error: {safe_console_text(message)}")
    raise typer.Exit(code=1)


def _load_manifest(package_path: Path) -> Manifest:
    manifest_path = package_path / "manifest.json"
    if not package_path.exists() or not package_path.is_dir():
        _fail(f"Package not found: {package_path}")
    if not manifest_path.exists():
        _fail(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path)
    except (json.JSONDecodeError, ValidationError) as exc:
        _fail(f"manifest.json is invalid: {exc}")
    except JsonlLimitError as exc:
        _fail(str(exc))
    raise AssertionError("unreachable")  # _fail always raises


@app.command()
def ingest(
    video_path: Path = typer.Argument(..., help="Path to the input video file."),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to the output .clulatent package folder."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite the output path if it already exists."
    ),
    vad: bool = typer.Option(
        False,
        "--vad/--no-vad",
        help=(
            "Run Silero VAD to detect speech_activity spans. Optional ML "
            "dependency (extra 'vad'); never runs unless passed."
        ),
    ),
    transcribe: bool = typer.Option(
        False,
        "--transcribe/--no-transcribe",
        help=(
            "Run faster-whisper to produce speech_segment events with text "
            "and language. Optional ML dependency (extra 'whisper'); never "
            "runs unless passed."
        ),
    ),
    whisper_model: str = typer.Option(
        WHISPER_DEFAULT_MODEL,
        "--whisper-model",
        help="faster-whisper model name to resolve via Hugging Face Hub (used with --transcribe).",
    ),
    whisper_model_path: str | None = typer.Option(
        None,
        "--whisper-model-path",
        help=(
            "Path to a local faster-whisper model directory (used with "
            "--transcribe). Overrides --whisper-model and avoids any "
            "network access."
        ),
    ),
    force_stale_lock: bool = typer.Option(
        False,
        "--force-stale-lock",
        help="Clear a stale ingest lock on the output path (pid gone or lock older than the safe threshold) before proceeding.",
    ),
) -> None:
    """Ingest a video file into a .clulatent package."""
    try:
        result = ingest_video(
            video_path,
            output,
            force=force,
            force_stale_lock=force_stale_lock,
            enable_vad=vad,
            enable_transcription=transcribe,
            whisper_model=whisper_model,
            whisper_model_path=whisper_model_path,
        )
    except IngestError as exc:
        _fail(str(exc))
        return

    manifest = result.manifest
    console.print(f"[bold green]Ingest complete[/bold green] -> {result.package_path}")
    console.print(f"  package_id:   {safe_console_text(manifest.package_id)}")
    console.print(
        f"  source:       {safe_console_text(manifest.source.filename)} "
        f"({manifest.source.sha256[:12]}...)"
    )
    console.print(
        f"  duration:     {ms_to_timecode(manifest.source.duration_ms)} "
        f"({manifest.source.width}x{manifest.source.height})"
    )
    console.print(f"  keyframes:    {manifest.media.keyframes.count}")
    console.print(f"  has_audio:    {manifest.source.has_audio}")
    for track in manifest.tracks:
        console.print(f"  track:        {safe_console_text(track.name)} ({track.record_count} records)")


@app.command(name="build-video")
def build_video(
    input_video: Path = typer.Argument(..., help="Path to the input video file."),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to the output .clulatent package folder."
    ),
    profile: str = typer.Option(
        v1_build_mod.V1_PROFILE,
        "--profile",
        help="Build profile. Only 'v1' is supported.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite the output path and re-run present lanes/bundles."
    ),
    allow_partial: bool = typer.Option(
        False,
        "--allow-partial",
        help=(
            "If the optional 'visual' extra (Pillow) is unavailable, skip "
            "the visual-change and changed-region lanes and build a partial "
            "package instead of failing. Never used silently: the summary is "
            "marked partial."
        ),
    ),
    force_stale_lock: bool = typer.Option(
        False,
        "--force-stale-lock",
        help="Clear a stale ingest lock on the output path before proceeding.",
    ),
    no_agent_index: bool = typer.Option(
        False,
        "--no-agent-index",
        help=(
            "Skip writing the built-in V1 agent index (index/v1/) into the "
            "package. By default a V1 build makes the package self-describing "
            "by writing agent_context.json/.md, ask_prompt.md, and "
            "index_manifest.json under index/v1/."
        ),
    ),
) -> None:
    """Build a V1 .clulatent package from a video in one command (V1).

    Runs the deterministic local pipeline: ingest, validate, visual-change
    (if Pillow available), changed-regions, one full-package evidence
    bundle, one agent review, then validate again. By default it also
    writes a built-in agent index under index/v1/ so the package is
    self-describing (V1). Produces candidate evidence only -- no
    semantic interpretation, no network/model call.
    """
    try:
        result = v1_build_mod.build_v1_package(
            input_video,
            output,
            profile=profile,
            force=force,
            allow_partial=allow_partial,
            force_stale_lock=force_stale_lock,
            write_index=not no_agent_index,
        )
    except v1_build_mod.V1BuildError as exc:
        _fail(str(exc))
        return

    marker = " [yellow](partial)[/yellow]" if result.partial else ""
    console.print(f"[bold green]V1 build complete[/bold green]{marker} -> {result.package_path}")
    console.print(f"  profile:      {safe_console_text(result.profile)}")
    console.print(f"  duration:     {ms_to_timecode(result.duration_ms)}")
    console.print(
        f"  validation:   before={'valid' if result.validation_before.valid else 'INVALID'} "
        f"after={'valid' if result.validation_after.valid else 'INVALID'} "
        f"({result.validation_after.error_count} error(s))"
    )
    console.print(f"  visual-change events:  {result.visual_change_events}")
    console.print(f"  changed-region events: {result.changed_region_events}")
    console.print(f"  evidence bundles:      {', '.join(result.bundle_ids) or 'none'}")
    console.print(f"  agent reviews:         {', '.join(result.review_ids) or 'none'}")
    if result.skipped_lanes:
        console.print(f"  skipped lanes:         {', '.join(result.skipped_lanes)}")
    for name, count in result.track_counts.items():
        console.print(f"  track:        {safe_console_text(name)} ({count} records)")
    for receipt in result.receipts:
        console.print(f"  receipt:      {safe_console_text(receipt)}")
    if result.index_written:
        console.print(f"  v1 index:     {v1_package_index_mod.INDEX_V1_DIR}/ (self-describing)")
        for artifact in result.index_artifacts:
            console.print(f"    index:      {safe_console_text(artifact)}")
    else:
        console.print("  v1 index:     skipped (--no-agent-index)")


@agent_context_app.command(name="export")
def agent_context_export(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(
        ..., "--output", help="Path to write the agent context document."
    ),
    output_format: str = typer.Option(
        "json",
        "--format",
        help="Output format: 'json' (the contract) or 'markdown' (a readable view).",
    ),
) -> None:
    """Export a package's agent context (V1).

    Opens the package through the read-only reader and writes one stable
    document describing what candidate evidence exists, what must not be
    concluded from it, and safe next steps. Never mutates the package,
    never writes a receipt, never interprets content.
    """
    if output_format not in ("json", "markdown"):
        _fail(f"unknown --format {output_format!r}; expected 'json' or 'markdown'")
        return
    try:
        context = agent_context_mod.build_agent_context(package_path)
    except agent_context_mod.AgentContextError as exc:
        _fail(str(exc))
        return

    if output_format == "markdown":
        text = agent_context_mod.render_markdown(context)
    else:
        text = agent_context_mod.context_to_json(context)

    try:
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        _fail(f"could not write agent context to {output}: {exc}")
        return
    console.print(
        f"[bold green]Agent context exported[/bold green] ({output_format}) -> {output}"
    )


@agent_context_app.command(name="summarize")
def agent_context_summarize(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print an evidence-only, human-readable summary of a package (V1).

    Opens the package through the read-only agent-context path and prints
    package facts, tracks present, candidate-evidence counts, evidence
    bundle and agent review IDs, unavailable evidence, caveats, and safe
    next steps. Makes no semantic claim about what the video depicts.
    Never mutates the package and never writes a receipt.
    """
    try:
        summary = v1_open_ask_mod.build_evidence_summary(package_path)
    except v1_open_ask_mod.OpenAskError as exc:
        _fail(str(exc))
        return
    # typer.echo, not console.print: the summary contains literal '[...]'
    # windows that rich would try to parse as markup.
    typer.echo(summary)


@agent_context_app.command(name="prompt")
def agent_context_prompt(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(
        None,
        "--output",
        help="Optional path to write the ask prompt. If omitted, prints to stdout.",
    ),
) -> None:
    """Generate a ready-to-paste, evidence-grounded agent ask prompt (V1).

    Builds a prompt instructing an agent to answer only from CLULatent
    evidence, cite IDs/timestamps/tracks/bundles/reviews, report unknowns,
    and never infer people/objects/actions/intent/identity/scene meaning.
    Does not call any model. Never mutates the package or writes a receipt.
    """
    try:
        prompt = v1_open_ask_mod.build_ask_prompt(package_path)
    except v1_open_ask_mod.OpenAskError as exc:
        _fail(str(exc))
        return
    if output is not None:
        try:
            output.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            _fail(f"could not write ask prompt to {output}: {exc}")
            return
        console.print(f"[bold green]Ask prompt exported[/bold green] -> {output}")
        return
    typer.echo(prompt)


@app.command()
def ask(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    question: str = typer.Argument(..., help="Your question about the video."),
    output: Path = typer.Option(
        None,
        "--output",
        help="Optional path to write the ask bundle. If omitted, prints to stdout.",
    ),
    output_format: str = typer.Option(
        "markdown",
        "--format",
        help="Output format: 'markdown' (default) or 'json'.",
    ),
) -> None:
    """Generate a safe, evidence-grounded agent ask bundle (V1).

    Turns a user question plus a `.clulatent` package into a ready-to-paste
    handoff bundle that Codex/CLU/another agent can use to answer *from
    package evidence* without reading package internals. Quotes the
    question verbatim, classifies its broad (non-semantic) intent, and --
    when the question names a timestamp -- lists the candidate evidence
    records around that window via the read-only reader. This is not an
    answer: it is the safe packet that lets an agent answer from evidence.
    Does not call any model. Never mutates the package or writes a receipt.
    """
    if output_format not in ("markdown", "json"):
        _fail(f"unknown --format {output_format!r}; expected 'markdown' or 'json'")
        return
    try:
        with archive_mod.resolved_package(package_path) as resolved:
            bundle = v1_ask_bundle_mod.build_ask_bundle(
                resolved, question, output_format=output_format
            )
    except (v1_ask_bundle_mod.AskBundleError, archive_mod.ArchiveError) as exc:
        _fail(str(exc))
        return
    if output is not None:
        try:
            output.write_text(bundle, encoding="utf-8")
        except OSError as exc:
            _fail(f"could not write ask bundle to {output}: {exc}")
            return
        console.print(f"[bold green]Ask bundle exported[/bold green] -> {output}")
        return
    # typer.echo, not console.print: the bundle contains literal '[...]'
    # windows and markdown that rich would try to parse as markup.
    typer.echo(bundle)


def _agent_read_time_ms(value: str) -> int:
    token = value.strip().lower()
    try:
        if token.endswith("ms"):
            return int(float(token[:-2]))
        if token.endswith("s"):
            return int(float(token[:-1]) * 1000)
        parts = token.split(":")
        if len(parts) in (2, 3):
            seconds = float(parts[-1]) + int(parts[-2]) * 60
            if len(parts) == 3:
                seconds += int(parts[0]) * 3600
            return int(seconds * 1000)
        return int(float(token) * 1000)
    except ValueError as exc:
        raise ValueError(f"invalid time {value!r}; use seconds (17s) or a timecode") from exc


@agent_read_app.command(name="summary")
def agent_read_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    budget: str = typer.Option("micro", "--budget", help="micro|summary|standard|full"),
) -> None:
    """Print a cheap evidence-only package orientation packet."""
    from . import v1_agent_read_model as v1_agent_read_model_mod

    try:
        with archive_mod.resolved_package(package_path) as resolved:
            model = v1_agent_read_model_mod.build_agent_read_model(resolved, budget=budget)
        typer.echo(v1_agent_read_model_mod.render_agent_read_summary(model), nl=False)
    except (v1_agent_read_model_mod.AgentReadModelError, archive_mod.ArchiveError) as exc:
        _fail(str(exc))


@agent_read_app.command(name="export")
def agent_read_export(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(..., "--output", help="Output file."),
    output_format: str = typer.Option("json", "--format", help="json|markdown"),
    budget: str = typer.Option("standard", "--budget", help="micro|summary|standard|full"),
    window_ms: int = typer.Option(1000, "--window-ms", min=1),
    max_windows: int | None = typer.Option(None, "--max-windows", min=1),
) -> None:
    """Export a bounded agent evidence map without mutating the package."""
    from . import v1_agent_read_model as v1_agent_read_model_mod

    if output_format not in ("json", "markdown"):
        _fail("--format must be json or markdown")
    try:
        model = v1_agent_read_model_mod.build_agent_read_model(
            package_path, budget=budget, window_ms=window_ms, max_windows=max_windows
        )
        text = (json.dumps(model, indent=2) + "\n" if output_format == "json" else
                v1_agent_read_model_mod.render_agent_read_markdown(model, budget=budget))
        output.write_text(text, encoding="utf-8")
    except (v1_agent_read_model_mod.AgentReadModelError, OSError) as exc:
        _fail(str(exc))
    console.print(f"[bold green]Agent read model exported[/bold green] -> {output}")


@agent_read_app.command(name="window")
def agent_read_window(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    time: str | None = typer.Option(None, "--time", help="Focused time, for example 17s."),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    budget: str = typer.Option("standard", "--budget", help="micro|summary|standard|full"),
) -> None:
    """Print one focused evidence window as JSON."""
    from . import v1_agent_read_model as v1_agent_read_model_mod

    try:
        with archive_mod.resolved_package(package_path) as resolved:
            result = v1_agent_read_model_mod.get_agent_read_window(
                resolved,
                time_ms=_agent_read_time_ms(time) if time is not None else None,
                start_ms=_agent_read_time_ms(start) if start is not None else None,
                end_ms=_agent_read_time_ms(end) if end is not None else None,
                budget=budget,
            )
        typer.echo(json.dumps(result, indent=2))
    except (
        v1_agent_read_model_mod.AgentReadModelError,
        archive_mod.ArchiveError,
        ValueError,
    ) as exc:
        _fail(str(exc))


@agent_read_app.command(name="write-index")
def agent_read_write_index(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock"),
) -> None:
    """Atomically write derived agent-read artifacts under index/v1."""
    from . import v1_agent_read_model as v1_agent_read_model_mod

    try:
        result = v1_agent_read_model_mod.write_agent_read_artifacts(
            package_path, force_stale_lock=force_stale_lock
        )
    except v1_agent_read_model_mod.AgentReadModelError as exc:
        _fail(str(exc))
    console.print(f"[bold green]Agent read index written[/bold green] -> {result.package_path}")
    for relative in result.written_paths:
        console.print(f"  wrote: {safe_console_text(relative)}")


@package_index_app.command(name="refresh")
def package_index_refresh(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force_stale_lock: bool = typer.Option(
        False,
        "--force-stale-lock",
        help="Clear a stale operation lock on the package before proceeding.",
    ),
) -> None:
    """Regenerate a package's built-in V1 index under index/v1/ (V1).

    Re-renders the agent context, Markdown brief, safe ask prompt, and
    index manifest from the package's *current* tracks and overwrites the
    index/v1/ artifacts. Generates no new evidence lane and writes no
    receipt. Refuses to write against a package that carries a valid
    integrity lock. Makes no semantic claim about what the video depicts.
    """
    try:
        result = v1_package_index_mod.refresh_v1_package_index(
            package_path, force_stale_lock=force_stale_lock
        )
    except v1_package_index_mod.V1PackageIndexError as exc:
        _fail(str(exc))
        return
    console.print(
        f"[bold green]V1 package index refreshed[/bold green] -> {result.package_path}"
    )
    console.print(f"  package id:   {safe_console_text(result.package_id)}")
    console.print(f"  generated at: {safe_console_text(result.generated_at)}")
    console.print(
        f"  validation:   {'valid' if result.validation_valid else 'INVALID'} (at generation)"
    )
    for relative in result.written_paths:
        console.print(f"  wrote:        {safe_console_text(relative)}")


@package_index_app.command(name="inspect")
def package_index_inspect(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Inspect a package's built-in V1 index (read-only, V1).

    Reports whether index/v1/ exists and, if so, the index manifest's
    schema, package id, generation time and tool, the package's validation
    status when the index was generated, each artifact's package-relative
    path and content hash, and the caveats. Never mutates the package and
    never writes a receipt.
    """
    if not v1_package_index_mod.v1_index_exists(package_path):
        console.print(
            f"[yellow]No built-in V1 index[/yellow] found under "
            f"{v1_package_index_mod.INDEX_V1_DIR}/ in {package_path}"
        )
        console.print("  Run 'clulatent package-index refresh PACKAGE' to create it.")
        return
    try:
        manifest = v1_package_index_mod.load_v1_index_manifest(package_path)
    except v1_package_index_mod.V1PackageIndexError as exc:
        _fail(str(exc))
        return

    generated_by = manifest.get("generated_by", {})
    validation = manifest.get("source_validation", {})
    lines: list[str] = []
    lines.append(f"Built-in V1 index for package {manifest.get('package_id')}")
    lines.append(f"  index dir:    {v1_package_index_mod.INDEX_V1_DIR}/")
    lines.append(f"  schema:       {manifest.get('schema_id')} (v{manifest.get('schema_version')})")
    lines.append(
        f"  generated by: {generated_by.get('tool')} {generated_by.get('version')} "
        f"at {manifest.get('generated_at')}"
    )
    lines.append(
        "  validation:   "
        + ("valid" if validation.get("valid") else "INVALID")
        + f" ({validation.get('error_count', 0)} error(s), "
        + f"{validation.get('warning_count', 0)} warning(s)) at generation"
    )
    lines.append("  artifacts:")
    for artifact in manifest.get("artifacts", []):
        sha = artifact.get("sha256")
        sha_display = f"{sha[:12]}..." if isinstance(sha, str) else "(self)"
        lines.append(f"    {artifact.get('path')}  [{sha_display}]")
    lines.append("  caveats:")
    for caveat in manifest.get("caveats", []):
        lines.append(f"    - {caveat}")
    # typer.echo, not console.print: hashes/paths contain no markup but the
    # bracketed hash display would be mis-parsed by rich.
    typer.echo("\n".join(lines))


@package_index_app.command(name="verify")
def package_index_verify(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Verify a package's built-in V1 indexes against its current state (V1).

    Read-only: reports whether the package-contained index (V1) and
    the agent-read index (V1) are present, internally consistent
    with their own recorded hashes, and fresh relative to the package's
    current tracks and evidence references. Never refreshes either index,
    never writes agent-read artifacts, never alters the manifest, tracks,
    receipts, or lock state, and runs no evidence generation, media
    analysis, or model/network call. When a component is missing, stale, or
    invalid, prints the exact command to fix it -- it never repairs
    anything itself.
    """
    try:
        with archive_mod.resolved_package(package_path) as resolved:
            result = v1_package_index_mod.verify_v1_package_index(resolved)
    except (v1_package_index_mod.V1PackageIndexError, archive_mod.ArchiveError) as exc:
        _fail(str(exc))
        return
    typer.echo(v1_package_index_mod.render_v1_package_index_verification(result))


@profile_app.command(name="inspect")
def profile_inspect(
    profile: str = typer.Argument(..., help="Profile identifier to describe, e.g. 'v1'."),
) -> None:
    """Describe the public CLULatent V1 compatibility contract."""
    from . import v1_profile as v1_profile_mod

    if profile != "v1":
        _fail(f"unknown profile {profile!r}; only 'v1' is currently defined")
        return
    typer.echo(
        v1_profile_mod.render_v1_profile_contract(
            v1_profile_mod.get_v1_profile_contract()
        )
    )


@profile_app.command(name="verify")
def profile_verify(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package."),
) -> None:
    """Check structural V1 compatibility without mutating the package."""
    from . import v1_profile as v1_profile_mod

    try:
        result = v1_profile_mod.verify_v1_profile(package_path)
    except v1_profile_mod.V1ProfileError as exc:
        _fail(str(exc))
        return
    typer.echo(v1_profile_mod.render_v1_profile_verification(result))
    if result.compatibility == "INCOMPATIBLE":
        raise typer.Exit(code=2)
    if result.compatibility == "UNKNOWN":
        raise typer.Exit(code=3)


@conformance_app.command(name="run")
def conformance_run(
    fixtures: Path | None = typer.Option(
        None,
        "--fixtures",
        help="External fixture root containing fixture_manifest.json; defaults to bundled fixtures.",
    ),
) -> None:
    """Run the public V1 conformance suite without network or model calls."""
    from . import conformance as conformance_mod

    try:
        result = conformance_mod.run_conformance(fixtures)
    except conformance_mod.ConformanceError as exc:
        _fail(str(exc))
        return
    typer.echo(conformance_mod.render_conformance(result), nl=False)
    if not result.passed:
        raise typer.Exit(code=1)


@app.command(name="demo")
def demo_command(
    output: Path | None = typer.Option(
        None, "-o", "--output", help="Portable demo output path."
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Validate the demo without opening its viewer."
    ),
) -> None:
    """Generate, validate, and profile-check a tiny synthetic V1 demo."""
    from . import conformance_fixtures as fixture_mod
    from . import v1_agent_read_model as agent_read_mod
    from . import v1_profile as profile_mod

    default_output = Path(tempfile.gettempdir()) / "clulatent-v1-demo.clulatent"
    destination = output or default_output
    if destination.exists():
        if output is not None:
            _fail(f"output already exists: {destination}")
            return
        destination.unlink()
    try:
        with tempfile.TemporaryDirectory(prefix="clulatent-demo-") as temp:
            package = fixture_mod.build_fixture(
                "valid_keyframes_audio", Path(temp) / "demo.clulatent"
            )
            v1_package_index_mod.write_v1_package_index(package)
            agent_read_mod.write_agent_read_artifacts(package)
            archive_mod.pack_package(package, destination)
        archive_mod.verify_archive(destination)
        profile = profile_mod.verify_v1_profile(destination)
    except (
        archive_mod.ArchiveError,
        profile_mod.V1ProfileError,
        v1_package_index_mod.V1PackageIndexError,
        OSError,
    ) as exc:
        destination.unlink(missing_ok=True)
        _fail(str(exc))
        return
    if profile.compatibility != "COMPATIBLE":
        destination.unlink(missing_ok=True)
        _fail(f"generated demo profile status: {profile.compatibility}")
        return
    typer.echo(f"PASS demo: {destination}")
    if not no_browser:
        open_package(
            package_path=destination,
            output_dir=None,
            no_browser=False,
            force=True,
            max_events=v1_playback_viewer_mod.DEFAULT_MAX_EVENTS,
            budget="micro",
        )


@app.command(name="pack")
def pack_package_command(
    package_path: Path = typer.Argument(..., help="Editable .clulatent package directory."),
    output: Path = typer.Option(..., "-o", "--output", help="Portable .clulatent file."),
) -> None:
    """Create the deterministic single-file transport form."""
    try:
        result = archive_mod.pack_package(package_path, output)
    except archive_mod.ArchiveError as exc:
        _fail(str(exc))
        return
    typer.echo(
        f"PASS archive written: {result.path} "
        f"members={result.members} sha256={result.sha256}"
    )


@app.command(name="unpack")
def unpack_archive_command(
    archive: Path = typer.Argument(..., help="Portable .clulatent file."),
    output: Path = typer.Option(..., "-o", "--output", help="Editable package directory."),
) -> None:
    """Safely unpack a portable archive with bounded extraction."""
    try:
        package = archive_mod.unpack_archive(archive, output)
    except archive_mod.ArchiveError as exc:
        _fail(str(exc))
        return
    typer.echo(f"PASS package written: {package}")


@archive_app.command(name="verify")
def archive_verify_command(
    archive: Path = typer.Argument(..., help="Portable .clulatent file."),
) -> None:
    """Verify archive identity, safe members, and conservative limits."""
    try:
        result = archive_mod.verify_archive(archive)
    except archive_mod.ArchiveError as exc:
        _fail(str(exc))
        return
    typer.echo(
        f"PASS members={result.members} uncompressed_bytes={result.uncompressed_bytes} "
        f"sha256={result.sha256}"
    )


@app.command()
def inspect(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a summary of a .clulatent package."""
    manifest = _load_manifest(package_path)

    try:
        sha_sidecar_path = resolve_in_package(
            package_path, "sources/source.sha256", field_name="sources/source.sha256"
        )
        stored_source_path = resolve_in_package(
            package_path, manifest.source.stored_path, field_name="source.stored_path"
        )
    except PathSecurityError as exc:
        _fail(str(exc))
        return
    hash_status = "unknown"
    if sha_sidecar_path.exists() and stored_source_path.exists():
        try:
            sidecar_digest = read_sha256_sidecar(sha_sidecar_path)
        except (OSError, IndexError, JsonlLimitError) as exc:
            _fail(f"sources/source.sha256 could not be read: {exc}")
            return
        actual_digest = sha256_file(stored_source_path)
        if sidecar_digest == manifest.source.sha256 == actual_digest:
            hash_status = "[green]verified[/green]"
        else:
            hash_status = "[bold red]MISMATCH[/bold red]"
    else:
        hash_status = "[yellow]missing sidecar or source file[/yellow]"

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("clulatent_version", safe_console_text(manifest.clulatent_version))
    table.add_row("package_id", safe_console_text(manifest.package_id))
    table.add_row("status", safe_console_text(manifest.status))
    table.add_row("source filename", safe_console_text(manifest.source.filename))
    table.add_row("duration", ms_to_timecode(manifest.source.duration_ms))
    table.add_row("dimensions", f"{manifest.source.width}x{manifest.source.height}")
    table.add_row("fps", str(manifest.source.fps) if manifest.source.fps else "unknown")
    table.add_row("video codec", safe_console_text(manifest.source.video_codec or "unknown"))
    table.add_row("audio codec", safe_console_text(manifest.source.audio_codec or "none"))
    table.add_row("has audio", str(manifest.source.has_audio))
    table.add_row("keyframe count", str(manifest.media.keyframes.count))
    table.add_row("index status", f"{manifest.index.status} (canonical={manifest.index.canonical})")
    table.add_row("source sha256", safe_console_text(manifest.source.sha256))
    table.add_row("source hash check", hash_status)
    console.print(table)

    tracks_table = Table(title="Tracks")
    tracks_table.add_column("Name")
    tracks_table.add_column("Records", justify="right")
    tracks_table.add_column("File")
    for track in manifest.tracks:
        tracks_table.add_row(
            safe_console_text(track.name), str(track.record_count), safe_console_text(track.file)
        )
    console.print(tracks_table)


@app.command()
def timeline(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a chronological, merged timeline of all track events."""
    manifest = _load_manifest(package_path)

    all_events: list[tuple[str, Any]] = []
    for track in manifest.tracks:
        try:
            track_path = resolve_in_package(
                package_path, track.file, field_name=f"tracks[{track.name}].file"
            )
        except PathSecurityError as exc:
            _fail(str(exc))
            return
        try:
            events = tracks_mod.read_track_file(track_path)
        except (JsonlLimitError, tracks_mod.TrackReadError) as exc:
            _fail(str(exc))
            return
        all_events.extend((track.name, event) for event in events)

    if not all_events:
        console.print("[yellow]No track events found.[/yellow]")
        return

    all_events.sort(key=lambda pair: (pair[1].t_start_ms, pair[1].id))

    type_width = max(len(event.type) for _, event in all_events) + 2
    id_width = max(len(event.id) for _, event in all_events) + 1

    for _track_name, event in all_events:
        payload = event.payload
        if "path" in payload:
            detail = str(payload["path"])
        elif "text" in payload:
            language = payload.get("language")
            detail = f"[{language}] {payload['text']}" if language else str(payload["text"])
        elif "message" in payload:
            detail = str(payload["message"])
        else:
            detail = json.dumps(payload, sort_keys=True)
        timecode = ms_to_timecode(event.t_start_ms)
        event_type = event.type.ljust(type_width)
        event_id = event.id.ljust(id_width)
        console.print(safe_console_text(f"{timecode}  {event_type}{event_id}{detail}"))


@app.command()
def query(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    search_text: str = typer.Argument(..., help="Text to search for across all tracks."),
) -> None:
    """Search the derived index/search.sqlite for matching records."""
    manifest = _load_manifest(package_path)
    try:
        sqlite_path = resolve_in_package(package_path, manifest.index.file, field_name="index.file")
    except PathSecurityError as exc:
        _fail(str(exc))
        return

    try:
        results = index_mod.query_search_index(sqlite_path, search_text)
    except index_mod.SqliteOpenError as exc:
        _fail(str(exc))
        return

    if not results:
        console.print(f"[yellow]No matches for '{search_text}'.[/yellow]")
        return

    table = Table(title=f"Query: '{safe_console_text(search_text)}' ({len(results)} match(es))")
    table.add_column("Timecode")
    table.add_column("Type")
    table.add_column("Track")
    table.add_column("ID")
    table.add_column("Payload")
    for row in results:
        table.add_row(
            ms_to_timecode(row["t_start_ms"]),
            safe_console_text(row["record_type"]),
            safe_console_text(row["track_name"]),
            safe_console_text(row["record_id"]),
            safe_console_text(row["payload_text"]),
        )
    console.print(table)


@app.command()
def validate(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Validate a .clulatent package: canonical data, derived index, receipts.

    Exits 0 if the package is valid, 1 otherwise. Read-only — never
    modifies the package.
    """
    try:
        with archive_mod.resolved_package(package_path) as resolved:
            report = validate_package(resolved)
    except archive_mod.ArchiveError as exc:
        _fail(str(exc))
        return

    console.print(f"Validating [bold]{safe_console_text(str(report.package_path))}[/bold]")
    if report.manifest is not None:
        console.print(f"  package_id: {safe_console_text(report.manifest.package_id)}")

    if report.warnings:
        console.print(f"[yellow]{len(report.warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(report.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    if report.valid:
        console.print("[bold green]PASS[/bold green] — package is valid")
        return

    console.print(f"[bold red]FAIL[/bold red] — {len(report.errors)} error(s) found:")
    for idx, error in enumerate(report.errors, start=1):
        console.print(f"  [red]{idx}.[/red] {safe_console_text(error)}")
    raise typer.Exit(code=1)


@app.command()
def report(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to write the static HTML report file."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite --output if it already exists."
    ),
) -> None:
    """Generate a static, local-first HTML report for a .clulatent package.

    Read-only: never mutates the package, never writes an index, receipt,
    or lock file inside it, and never touches anything except `--output`.
    The report is a single self-contained HTML file (inline CSS only, no
    JavaScript, no external scripts/fonts/CDN, no network access) safe to
    open directly in a browser. It displays evidence recorded in the
    package -- adapter outputs are evidence, not truth; generated does
    not mean canonical.

    Exits 1 with a clean error message -- never a traceback -- if the
    package cannot be read at all, `--output` already exists without
    `--force`, or `--output`'s parent directory does not exist.
    """
    if output.exists() and not force:
        _fail(f"output file already exists (use --force to overwrite): {output}")
        return
    if output.exists() and output.is_dir():
        _fail(f"output path is a directory, not a file: {output}")
        return
    if not output.parent.exists() or not output.parent.is_dir():
        _fail(f"output directory does not exist: {output.parent}")
        return

    try:
        result = generate_report(package_path)
    except ReportError as exc:
        _fail(str(exc))
        return

    output.write_text(result.html, encoding="utf-8")

    console.print(f"[bold green]Report written[/bold green] -> {safe_console_text(str(output))}")
    if result.warnings:
        console.print(f"[yellow]{len(result.warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(result.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")


@app.command(name="playback-viewer")
def playback_viewer(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to write the static HTML playback viewer."
    ),
    max_events: int = typer.Option(
        v1_playback_viewer_mod.DEFAULT_MAX_EVENTS,
        "--max-events",
        help="Cap on the number of timeline events embedded in the viewer.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite --output if it already exists."
    ),
) -> None:
    """Generate a static, local-first evidence playback viewer (V1).

    "MP4 plays media. CLULatent plays media plus evidence." The viewer is
    a single self-contained HTML file (inline CSS, inline JS, one embedded
    JSON payload -- no external script/stylesheet/font/image link, no CDN,
    no network access) that plays the package's source media (when a
    playable file is present) alongside a timeline of the package's
    timestamped evidence: keyframes, audio/speech events, visual-change
    and changed-region candidates, evidence bundles, and agent reviews.

    Evidence playback, not scene understanding: it runs no visual AI, makes
    no object/person/action/identity claim, and interprets nothing. It
    references the package's own media/keyframe files only via safe,
    path-contained relative links; with no playable source it degrades to
    an evidence-only view.

    Read-only: never mutates the package, never writes an index, receipt,
    or lock file inside it, and touches nothing except `--output`. Exits 1
    with a clean error message -- never a traceback -- if the package
    cannot be read, `--output` already exists without `--force`, or
    `--output`'s parent directory does not exist.
    """
    if output.exists() and output.is_dir():
        _fail(f"output path is a directory, not a file: {output}")
        return
    if output.exists() and not force:
        _fail(f"output file already exists (use --force to overwrite): {output}")
        return
    if not output.parent.exists() or not output.parent.is_dir():
        _fail(f"output directory does not exist: {output.parent}")
        return

    try:
        result = v1_playback_viewer_mod.write_playback_viewer(
            package_path, output, max_events=max_events, force=force
        )
    except v1_playback_viewer_mod.PlaybackViewerError as exc:
        _fail(str(exc))
        return

    media = result.payload.get("media", {})
    media_note = "playable media" if media.get("playable") else "evidence-only (no playable media)"
    timeline = result.payload.get("timeline", {})
    console.print(
        f"[bold green]Playback viewer written[/bold green] -> {safe_console_text(str(output))}"
    )
    console.print(
        f"  {media_note}; {timeline.get('shown', 0)} of {timeline.get('total', 0)} "
        "timeline event(s) shown"
    )
    if result.warnings:
        console.print(f"[yellow]{len(result.warnings)} notice(s):[/yellow]")
        for idx, warning in enumerate(result.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")


@app.command(name="open")
def open_package(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help=(
            "External directory to write open artifacts into. Defaults to a "
            "package-derived directory under the system temp dir; never inside "
            "the package."
        ),
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Generate the viewer but do not try to launch a browser."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing open artifacts in --output-dir."
    ),
    max_events: int = typer.Option(
        v1_playback_viewer_mod.DEFAULT_MAX_EVENTS,
        "--max-events",
        help="Cap on the number of timeline events embedded in the generated viewer.",
    ),
    budget: str = typer.Option(
        "micro", "--budget", help="Agent-read budget: micro|summary|standard|full."
    ),
) -> None:
    """Open a `.clulatent` package: the front door to its evidence surfaces (V1).

    "MP4 plays media. CLULatent plays media plus evidence." `open` does no
    new analysis; it composes existing V1 read surfaces into one command:
    it generates the V1 evidence playback viewer and a V1
    budgeted agent-read Markdown brief, reports whether the package's
    built-in V1 index and V1 agent-read index are present
    (without regenerating either), and -- unless `--no-browser` is given
    -- tries to open the viewer in the local system browser.

    Read-only with respect to the package: it never mutates the package,
    writes no receipt, and never refreshes a package-internal index. The
    only files written are the generated viewer, agent-read Markdown, and
    a small open summary, in an external `--output-dir` (default: a
    package-derived directory under the system temp dir). A failed
    browser launch is reported, not fatal -- the viewer's path is always
    printed so it can be opened by hand.
    """
    from . import v1_open_package as v1_open_package_mod

    resolved_package = package_path
    resolved_output_dir = output_dir
    try:
        if package_path.is_file():
            verification = archive_mod.verify_archive(package_path)
            if resolved_output_dir is None:
                safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", package_path.stem)[:48]
                resolved_output_dir = (
                    Path(tempfile.gettempdir())
                    / f"clulatent-open-{safe_name}-{verification.sha256[:12]}"
                )
            archive_package = Path(resolved_output_dir) / "archive-package"
            source_marker = Path(resolved_output_dir) / ".archive-source-sha256"
            marker_matches = (
                source_marker.is_file()
                and source_marker.read_text(encoding="utf-8").strip() == verification.sha256
            )
            if archive_package.exists() and not marker_matches:
                if not force:
                    raise v1_open_package_mod.OpenPackageError(
                        "archive extraction collision in output directory; use --force "
                        "or choose a different --output-dir"
                    )
                shutil.rmtree(archive_package)
            if not archive_package.exists():
                Path(resolved_output_dir).mkdir(parents=True, exist_ok=True)
                archive_mod.unpack_archive(package_path, archive_package)
                source_marker.write_text(verification.sha256 + "\n", encoding="utf-8")
            resolved_package = archive_package
        plan = v1_open_package_mod.build_open_package_plan(
            resolved_package,
            output_dir=resolved_output_dir,
            budget=budget,
            max_events=max_events,
        )
        result = v1_open_package_mod.write_open_package_artifacts(plan, force=force)
    except (v1_open_package_mod.OpenPackageError, archive_mod.ArchiveError, OSError) as exc:
        _fail(str(exc))
        return

    result = v1_open_package_mod.open_package_surface(result, launch_browser=not no_browser)

    # typer.echo, not console.print: the summary embeds literal package
    # paths/filenames that rich would otherwise try to parse as markup.
    typer.echo(v1_open_package_mod.render_open_package_summary(result), nl=False)


@keyframes_app.command(name="contact-sheet")
def keyframes_contact_sheet(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    output: Path = typer.Option(
        ..., "-o", "--output", help="Path to write the static HTML contact sheet."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite --output if it already exists."
    ),
) -> None:
    """Render a static keyframe contact sheet from a package's keyframes.

    Read-only: only reads manifest.json and the keyframes track, and
    never mutates the package, writes an index/receipt/lock file, or
    touches anything except `--output`. The contact sheet is a single
    self-contained HTML file (inline CSS only, no JavaScript, no external
    scripts/fonts/CDN, no network access) safe to open in a browser.

    This is a visual evidence browser, not a visual understanding system:
    it shows the frames ingest recorded and never interprets them --
    keyframes are evidence, not interpretation, and this preview does not
    describe what happens in the clip.

    Exits 1 with a clean error message -- never a traceback -- if the
    package cannot be read at all, `--output` already exists without
    `--force`, or `--output`'s parent directory does not exist.
    """
    if output.exists() and not force:
        _fail(f"output file already exists (use --force to overwrite): {output}")
        return
    if output.exists() and output.is_dir():
        _fail(f"output path is a directory, not a file: {output}")
        return
    if not output.parent.exists() or not output.parent.is_dir():
        _fail(f"output directory does not exist: {output.parent}")
        return

    try:
        result = generate_contact_sheet(package_path, output_path=output)
    except KeyframePreviewError as exc:
        _fail(str(exc))
        return

    output.write_text(result.html, encoding="utf-8")

    console.print(
        f"[bold green]Contact sheet written[/bold green] -> {safe_console_text(str(output))}"
    )
    if result.warnings:
        console.print(f"[yellow]{len(result.warnings)} notice(s):[/yellow]")
        for idx, warning in enumerate(result.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")


# --- clulatent keyframes ... retrieval commands (V1) ----------------
#
# Read-only retrieval over the keyframe evidence ingest already stored
# (V1 primitives, `keyframe_retrieval.py`). No FFmpeg, no frame
# extraction, no visual interpretation, no mutation, no receipt, no
# index rebuild. See docs/PHASE_3_13_KEYFRAME_EVIDENCE_RETRIEVAL_CLI.md.


def _keyframe_image_relpath(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        path = payload.get("path")
        if isinstance(path, str) and path:
            return path
    return "(no path recorded)"


def _print_keyframe_record(event: dict[str, Any], package_path: Path) -> None:
    event_id = event.get("id")
    console.print(f"[bold]{safe_console_text(str(event_id))}[/bold]")
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))} "
            f"({t_start_ms} ms)"
        )
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    frame_index = payload.get("frame_index")
    if isinstance(frame_index, int):
        console.print(f"  frame_index: {frame_index}")
    width = payload.get("width")
    height = payload.get("height")
    if isinstance(width, int) and isinstance(height, int):
        console.print(f"  size: {width}x{height}")
    console.print(f"  package path: {safe_console_text(_keyframe_image_relpath(event))}")
    missing = keyframe_retrieval_mod.keyframe_image_missing(package_path, event)
    console.print(f"  image on disk: {'missing' if missing else 'present'}")


def _print_keyframe_table(
    events: list[dict[str, Any]], package_path: Path, *, title: str
) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("ID")
    table.add_column("Package path")
    table.add_column("Image")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        missing = keyframe_retrieval_mod.keyframe_image_missing(package_path, event)
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("id"))),
            safe_console_text(_keyframe_image_relpath(event)),
            "missing" if missing else "present",
        )
    console.print(table)


@keyframes_app.command(name="get")
def keyframes_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="The keyframe event id to retrieve (e.g. kf_000014)."),
) -> None:
    """Print one stored keyframe record by id.

    Read-only: never mutates the package, never writes a receipt, never
    touches the index, never extracts a frame. Prints "no result"
    cleanly (exit 0) if the id is not found; fails cleanly (exit 1) if
    the package or its keyframes track cannot be safely read.
    """
    try:
        event = keyframe_retrieval_mod.get_keyframe_by_id(package_path, event_id)
    except keyframe_retrieval_mod.KeyframeRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(
            f"[yellow]No keyframe found with id '{safe_console_text(event_id)}'.[/yellow]"
        )
        return

    _print_keyframe_record(event, Path(package_path))


@keyframes_app.command(name="query-time")
def keyframes_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Range end, in milliseconds."),
) -> None:
    """List stored keyframes overlapping a time range, ordered by timestamp.

    Read-only. An empty result (no keyframe overlaps the range, or no
    keyframes track) is printed cleanly (exit 0), not an error. An
    inverted range (`--end-ms` < `--start-ms`) fails cleanly (exit 1).
    """
    try:
        events = keyframe_retrieval_mod.query_keyframes_by_time_range(
            package_path, start_ms, end_ms
        )
    except keyframe_retrieval_mod.KeyframeRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No keyframes overlap that time range.[/yellow]")
        return

    _print_keyframe_table(events, Path(package_path), title="Keyframes (time range)")


@keyframes_app.command(name="nearest")
def keyframes_nearest(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    time_ms: int = typer.Option(..., "--time-ms", help="Target timestamp, in milliseconds."),
) -> None:
    """Print the stored keyframe closest in time to a timestamp.

    Ties are broken deterministically (smallest distance, then earliest
    timestamp, then id). Read-only. An empty keyframes track prints a
    "no result" message cleanly (exit 0), not an error.
    """
    try:
        event = keyframe_retrieval_mod.get_nearest_keyframe(package_path, time_ms)
    except keyframe_retrieval_mod.KeyframeRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print("[yellow]No keyframes are stored in this package.[/yellow]")
        return

    _print_keyframe_record(event, Path(package_path))


@keyframes_app.command(name="retrieval-summary")
def keyframes_retrieval_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's keyframe track.

    Keyframe count, first/last timestamp, the ordered event ids, and any
    keyframe image files missing from disk -- no prose, no inference.
    Read-only. A package with no keyframes track prints a zeroed-out
    summary cleanly (exit 0), not an error.
    """
    try:
        summary = keyframe_retrieval_mod.summarize_keyframe_retrieval(package_path)
    except keyframe_retrieval_mod.KeyframeRetrievalError as exc:
        _fail(str(exc))
        return

    console.print(f"keyframe_count: {summary['keyframe_count']}")
    console.print(f"first_timestamp_ms: {summary['first_timestamp_ms']}")
    console.print(f"last_timestamp_ms: {summary['last_timestamp_ms']}")
    console.print(f"missing_image_count: {summary['missing_image_count']}")
    if summary["missing_image_paths"]:
        console.print("missing_image_paths:")
        for path in summary["missing_image_paths"]:
            console.print(f"  - {safe_console_text(path)}")
    console.print("event_ids:")
    for event_id in summary["event_ids"]:
        console.print(f"  - {safe_console_text(event_id)}")


@package_app.command(name="inspect")
def package_inspect(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a bounded, structured summary of a package (V1).

    Opens the package through the official read-only reader and prints
    its `summary()` as JSON: ids, duration, status, per-track counts +
    known/unknown, receipt files, and lock status. Never mutates the
    package, never writes receipts, never interprets content.
    """
    try:
        with package_reader_mod.open_package(package_path) as reader:
            summary = reader.summary()
    except package_reader_mod.PackageReaderError as exc:
        _fail(str(exc))
        return
    print_json(summary)


@package_app.command(name="tracks")
def package_tracks(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """List a package's tracks by name (V1), no filename knowledge needed.

    One line per manifest-declared track: name, record count, whether it
    is a known canonical CLULatent lane, and its file. Read-only.
    """
    try:
        reader = package_reader_mod.open_package(package_path)
    except package_reader_mod.PackageReaderError as exc:
        _fail(str(exc))
        return
    for track in reader.list_tracks():
        known = "known" if track.known else "unknown"
        console.print(
            f"{safe_console_text(track.name)}  "
            f"count={track.record_count}  {known}  "
            f"{safe_console_text(track.file)}"
        )


@app.command(name="welcome")
def welcome_cmd(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored/styled output."),
    quiet: bool = typer.Option(
        False, "--quiet", help="Suppress the decorative welcome panel entirely."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the panel."
    ),
) -> None:
    """Show a small text-first welcome/status panel (V1).

    Skips the ASCII panel when stdout is not a TTY (one plain status
    line instead), respects `NO_COLOR`/`--no-color`, and prints nothing
    at all with `--quiet`. Read-only: never touches a package.
    """
    report = run_doctor_checks()
    if as_json:
        print_json(report.to_dict())
        return
    console_ = make_console(no_color=no_color)
    render_welcome(console_, report.checks, quiet=quiet)


@app.command(name="doctor")
def doctor_cmd(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored/styled output."),
    quiet: bool = typer.Option(
        False, "--quiet", help="Suppress the decorative header (capability lines still print)."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the report."
    ),
) -> None:
    """Report local capability availability: CLI, reader, validator,
    keyframe retrieval, and OS system-integration status (V1).

    Read-only, no network, no package mutation. Each check is a genuine
    probe -- a broken/partial install is reported as unavailable, not
    silently assumed to work.
    """
    report = run_doctor_checks()
    if as_json:
        print_json(report.to_dict())
        return
    console_ = make_console(no_color=no_color)
    render_doctor(console_, report, quiet=quiet)


@system_app.command(name="status")
def system_status_cmd(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored/styled output."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the decorative header."),
    as_json: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the status text."
    ),
) -> None:
    """Report whether OS-level `.clulatent` file registration is installed.

    Read-only. Never says "registered" unless the platform adapter
    genuinely confirms it.
    """
    status = get_system_integration().status()
    if as_json:
        print_json(status.to_dict())
        return
    console_ = make_console(no_color=no_color)
    render_registration_status(console_, status, quiet=quiet)


@system_app.command(name="register")
def system_register_cmd(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored/styled output."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the decorative header."),
    as_json: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the result."
    ),
) -> None:
    """Register OS-level `.clulatent` file-type integration (V1).

    Only steps that genuinely completed are ever reported as done. If
    automatic registration is not safely implemented on this platform,
    this prints a clear "not implemented" / "manual registration
    required" result rather than pretending to have registered anything.
    """
    result = get_system_integration().register()
    if as_json:
        print_json(result.to_dict())
        return
    console_ = make_console(no_color=no_color)
    render_registration_result(console_, result, quiet=quiet)
    if result.outcome not in (ActionOutcome.SUCCESS, ActionOutcome.NOT_IMPLEMENTED):
        raise typer.Exit(code=1)


@system_app.command(name="unregister")
def system_unregister_cmd(
    no_color: bool = typer.Option(False, "--no-color", help="Disable colored/styled output."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the decorative header."),
    as_json: bool = typer.Option(
        False, "--json", help="Print machine-readable JSON instead of the result."
    ),
) -> None:
    """Remove OS-level `.clulatent` file-type integration, if implemented.

    Safe and scoped: never mutates package contents, only ever undoes
    what this tool's own `system register` genuinely put in place. Fails
    clearly if not implemented on this platform.
    """
    result = get_system_integration().unregister()
    if as_json:
        print_json(result.to_dict())
        return
    console_ = make_console(no_color=no_color)
    render_registration_result(console_, result, quiet=quiet)
    if result.outcome not in (ActionOutcome.SUCCESS, ActionOutcome.NOT_IMPLEMENTED):
        raise typer.Exit(code=1)


@app.command(name="review-state")
def review_state_cmd(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a read-time summary of reviewed state (V1).

    Computes the current review state of every source event from the
    package's canonical tracks plus tracks/review_events.jsonl (additive
    evidence only). Read-only: never writes anything, and never writes a
    "reviewed_truth" track. Does not require a review_events track to be
    present — packages without one report every source event as
    unreviewed.
    """
    states, warnings = resolve_package_review_states(package_path)
    summary = summarize_review_states(states)

    console.print(f"Review state [bold]{safe_console_text(str(Path(package_path)))}[/bold]")
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column(justify="right")
    table.add_row("total source events", str(summary["total"]))
    table.add_row(
        "reviewed",
        str(summary["reviewed"] + summary["approved"] + summary["rejected"] + summary["corrected"]),
    )
    table.add_row("approved", str(summary["approved"]))
    table.add_row("rejected", str(summary["rejected"]))
    table.add_row("corrected", str(summary["corrected"]))
    table.add_row("superseded", str(summary["superseded"]))
    table.add_row("needs_review / uncertain", str(summary["needs_review"] + summary["uncertain"]))
    table.add_row("conflicts", str(summary["conflicts"]))
    console.print(table)


_FORCE_STALE_LOCK_HELP = (
    "Clear a stale operation lock (pid gone or lock older than the safe threshold) before proceeding."
)


def _run_review_write(
    package_path: Path,
    event_type: str,
    *,
    force_stale_lock: bool,
    **kwargs: Any,
) -> None:
    # Checked before entering `operation_lock` (which resolves the package
    # path with `strict=True` and raises a raw FileNotFoundError on a
    # missing package) so a nonexistent package always surfaces as a clean
    # `_fail(...)` message, matching every other command in this file.
    if not package_path.exists() or not package_path.is_dir():
        _fail(f"Package not found: {package_path}")
    try:
        with operation_lock(package_path, operation="review", force_stale=force_stale_lock):
            result = review_writer_mod.append_review_event(package_path, event_type, **kwargs)
    except OperationLockError as exc:
        _fail(str(exc))
        return
    except review_writer_mod.ReviewWriteError as exc:
        _fail(str(exc))
        return

    console.print(f"[bold green]Review event written[/bold green] -> {safe_console_text(result.event_id)}")
    if result.review_track_created:
        console.print("  created tracks/review_events.jsonl")


def _parse_payload_json(raw: str, option_name: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail(f"{option_name} must be valid JSON: {exc}")
        raise AssertionError("unreachable")  # _fail always raises
    if not isinstance(parsed, dict):
        _fail(f"{option_name} must be a JSON object")
    return parsed


@review_app.command(name="approve")
def review_approve(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="Id of the source event being approved."),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    reason: str | None = typer.Option(None, "--reason"),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a review_approval event: confirms event_id is correct as-is."""
    _run_review_write(
        package_path,
        "review_approval",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        confidence=certainty,
        reviewed_at=reviewed_at,
    )


@review_app.command(name="reject")
def review_reject(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="Id of the source event being rejected."),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    reason: str | None = typer.Option(None, "--reason", help="Why event_id should not be trusted."),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a review_rejection event: asserts event_id should not be trusted."""
    _run_review_write(
        package_path,
        "review_rejection",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        confidence=certainty,
        reviewed_at=reviewed_at,
    )


@review_app.command(name="correct")
def review_correct(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="Id of the source event being corrected."),
    original_payload_json: str = typer.Option(
        ..., "--original-payload-json", help="JSON object: dotted field path -> original value."
    ),
    corrected_payload_json: str = typer.Option(
        ..., "--corrected-payload-json", help="JSON object: dotted field path -> corrected value."
    ),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    reason: str | None = typer.Option(None, "--reason"),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a review_correction event: a field-level corrected value for event_id."""
    original_payload = _parse_payload_json(original_payload_json, "--original-payload-json")
    corrected_payload = _parse_payload_json(corrected_payload_json, "--corrected-payload-json")
    _run_review_write(
        package_path,
        "review_correction",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        confidence=certainty,
        reviewed_at=reviewed_at,
        original_payload=original_payload,
        corrected_payload=corrected_payload,
    )


@review_app.command(name="override")
def review_override(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="Id of the source event being overridden."),
    override_kind: str = typer.Option(
        ..., "--override-kind", help="Short label for the kind of override (e.g. reclassify/respan)."
    ),
    state: str = typer.Option(..., "--state", help="review_state to assert: corrected or rejected."),
    original_payload_json: str = typer.Option(
        ..., "--original-payload-json", help="JSON object: dotted field path -> original value."
    ),
    corrected_payload_json: str = typer.Option(
        ..., "--corrected-payload-json", help="JSON object: dotted field path -> corrected value."
    ),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    reason: str | None = typer.Option(None, "--reason"),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a review_override event: a broader reinterpretation than a field-level correction."""
    original_payload = _parse_payload_json(original_payload_json, "--original-payload-json")
    corrected_payload = _parse_payload_json(corrected_payload_json, "--corrected-payload-json")
    _run_review_write(
        package_path,
        "review_override",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        confidence=certainty,
        reviewed_at=reviewed_at,
        override_kind=override_kind,
        review_state=state,
        original_payload=original_payload,
        corrected_payload=corrected_payload,
    )


@review_app.command(name="status")
def review_status_write(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="Id of the source event being triaged/flagged."),
    state: str = typer.Option(
        ...,
        "--state",
        help="review_state to assert (unreviewed/needs_review/uncertain/reviewed/approved/corrected/rejected).",
    ),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    reason: str | None = typer.Option(None, "--reason"),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a review_status event: triage, flagging, or acknowledgment for event_id."""
    _run_review_write(
        package_path,
        "review_status",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        reason=reason,
        confidence=certainty,
        reviewed_at=reviewed_at,
        review_state=state,
    )


@review_app.command(name="note")
def review_note(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    note: str = typer.Option(..., "--note", help="Freestanding commentary text."),
    event_id: str | None = typer.Option(
        None, "--event-id", help="Optional source event id this note is about (omit for a general note)."
    ),
    reviewer_id: str = typer.Option(
        "local", "--reviewer-id", help="Reviewer identity; producer.name becomes human:<reviewer-id>."
    ),
    reviewer_label: str | None = typer.Option(None, "--reviewer-label"),
    certainty: float | None = typer.Option(None, "--certainty", help="Optional confidence (0..1)."),
    reviewed_at: str | None = typer.Option(
        None, "--reviewed-at", help="ISO-8601 timestamp; defaults to now (UTC)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Append a human_note event: freestanding commentary, not a judgment."""
    _run_review_write(
        package_path,
        "human_note",
        force_stale_lock=force_stale_lock,
        target_event_id=event_id,
        reviewer_id=reviewer_id,
        reviewer_label=reviewer_label,
        note_text=note,
        confidence=certainty,
        reviewed_at=reviewed_at,
    )


# --- clulatent analysis ... (V1) --------------------------------------
#
# This group writes already-produced analysis-lane events (V1
# schema validation + V1 writer/receipts). It runs no FFmpeg
# tracker, no OCR engine, no object detector, and no ML model — it is
# a manual/scripted door for evidence a caller already produced
# elsewhere, not an adapter runtime. See
# docs/PHASE_2_8_ANALYSIS_LANE_CLI_COMMANDS.md.


def _load_candidate_events_file(
    path: Path, *, limits: Limits = DEFAULT_LIMITS
) -> list[dict[str, Any]]:
    """Load a batch of candidate analysis event dicts from `path`.

    Supports two formats, auto-detected by the first non-whitespace
    character: a JSON array of event objects (`[...]`), or JSONL (one
    event object per line, the format every tracks/*.jsonl file
    already uses). Raises `ValueError` with a clean, user-facing
    message on any problem (missing/empty file, invalid JSON, a
    non-object item/line, an empty batch) — callers convert this to
    `_fail(...)` rather than letting a traceback escape.
    """
    try:
        raw = read_bytes_bounded(path, max_bytes=limits.max_manifest_bytes, field_name=str(path))
    except JsonlLimitError as exc:
        raise ValueError(str(exc)) from exc

    stripped = raw.decode("utf-8").strip()
    if not stripped:
        raise ValueError(f"{path}: file is empty")

    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError(f"{path}: top-level JSON value must be an array of event objects")
        events: list[dict[str, Any]] = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"{path}: event[{index}] must be a JSON object")
            events.append(item)
        if not events:
            raise ValueError(f"{path}: event array must not be empty")
        return events

    events = []
    for record in iter_jsonl_bounded(path, limits=limits):
        if record.error is not None:
            raise ValueError(f"{path}:{record.lineno}: {record.error}")
        events.append(record.data)
    if not events:
        raise ValueError(f"{path}: file contains no events")
    return events


def _run_analysis_append(
    package_path: Path,
    lane: str,
    events: list[dict[str, Any]],
    *,
    adapter_name: str,
    tool_name: str,
    tool_version: str,
    model_name: str | None,
    model_version: str | None,
    parameters: dict[str, Any] | None,
    input_sources: list[str],
    write_receipt: bool,
    force_stale_lock: bool,
) -> None:
    try:
        result = analysis_writer_mod.append_analysis_events(
            package_path,
            lane,
            events,
            adapter_name=adapter_name,
            tool_name=tool_name,
            tool_version=tool_version,
            model_name=model_name,
            model_version=model_version,
            parameters=parameters,
            input_sources=input_sources or None,
            write_receipt=write_receipt,
            force_stale_lock=force_stale_lock,
        )
    except analysis_writer_mod.AnalysisWriteError as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Analysis events written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  lane:           {safe_console_text(result.lane)}")
    console.print(f"  events written: {result.events_written}")
    for event_id in result.event_ids:
        console.print(f"    - {safe_console_text(event_id)}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


_ANALYSIS_RECEIPT_OPTION_HELP = (
    "Recorded in receipts/analyze.jsonl (V1); does not affect validation."
)


@analysis_app.command(name="lanes")
def analysis_lanes_cmd() -> None:
    """Print the supported analysis lane names (V1 catalog)."""
    for name in analysis_lanes_mod.ANALYSIS_LANE_NAMES:
        console.print(safe_console_text(name))


@analysis_app.command(name="append")
def analysis_append(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    lane: str = typer.Argument(..., help="Analysis lane name (see `clulatent analysis lanes`)."),
    event_json: str = typer.Option(
        ..., "--event-json", help="A single analysis event as a JSON object."
    ),
    adapter_name: str = typer.Option(
        _DEFAULT_ANALYSIS_ADAPTER_NAME, "--adapter-name", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help=_ANALYSIS_RECEIPT_OPTION_HELP),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    model_name: str | None = typer.Option(None, "--model-name", help=_ANALYSIS_RECEIPT_OPTION_HELP),
    model_version: str | None = typer.Option(
        None, "--model-version", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    parameter_json: str | None = typer.Option(
        None, "--parameter-json", help="Adapter run parameters as a JSON object. " + _ANALYSIS_RECEIPT_OPTION_HELP
    ),
    input_source: list[str] = typer.Option(
        [], "--input-source", help="Repeatable. " + _ANALYSIS_RECEIPT_OPTION_HELP
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/analyze.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Validate and append one analysis-lane event from --event-json.

    Writes evidence only — the event is validated against the Phase
    2.6 schema and appended via the V1 writer, exactly as if a
    future adapter had called `append_analysis_event` directly. Refuses
    a locked package, an unsupported lane, or an invalid event; nothing
    is written on any refusal.
    """
    event = _parse_payload_json(event_json, "--event-json")
    parameters = _parse_payload_json(parameter_json, "--parameter-json") if parameter_json else None
    _run_analysis_append(
        package_path,
        lane,
        [event],
        adapter_name=adapter_name,
        tool_name=tool_name,
        tool_version=tool_version,
        model_name=model_name,
        model_version=model_version,
        parameters=parameters,
        input_sources=input_source,
        write_receipt=receipt,
        force_stale_lock=force_stale_lock,
    )


@analysis_app.command(name="append-file")
def analysis_append_file(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    lane: str = typer.Argument(..., help="Analysis lane name (see `clulatent analysis lanes`)."),
    events_file: Path = typer.Argument(
        ..., help="Path to a JSONL file (one event object per line) or a JSON array of event objects."
    ),
    adapter_name: str = typer.Option(
        _DEFAULT_ANALYSIS_ADAPTER_NAME, "--adapter-name", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help=_ANALYSIS_RECEIPT_OPTION_HELP),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    model_name: str | None = typer.Option(None, "--model-name", help=_ANALYSIS_RECEIPT_OPTION_HELP),
    model_version: str | None = typer.Option(
        None, "--model-version", help=_ANALYSIS_RECEIPT_OPTION_HELP
    ),
    parameter_json: str | None = typer.Option(
        None, "--parameter-json", help="Adapter run parameters as a JSON object. " + _ANALYSIS_RECEIPT_OPTION_HELP
    ),
    input_source: list[str] = typer.Option(
        [], "--input-source", help="Repeatable. " + _ANALYSIS_RECEIPT_OPTION_HELP
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/analyze.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Validate and append every analysis-lane event in `events_file`.

    All events in the batch are validated together before anything is
    written — a single invalid event refuses the whole batch, so a
    partial write into tracks/<lane>.jsonl or manifest.json never
    happens. `events_file` may be JSONL or a JSON array; an empty file
    or empty batch is refused cleanly.
    """
    if not events_file.exists() or not events_file.is_file():
        _fail(f"events file not found: {events_file}")
        return
    try:
        events = _load_candidate_events_file(events_file)
    except ValueError as exc:
        _fail(str(exc))
        return

    parameters = _parse_payload_json(parameter_json, "--parameter-json") if parameter_json else None
    _run_analysis_append(
        package_path,
        lane,
        events,
        adapter_name=adapter_name,
        tool_name=tool_name,
        tool_version=tool_version,
        model_name=model_name,
        model_version=model_version,
        parameters=parameters,
        input_sources=input_source,
        write_receipt=receipt,
        force_stale_lock=force_stale_lock,
    )


@analysis_app.command(name="validate-file")
def analysis_validate_file(
    lane: str = typer.Argument(..., help="Analysis lane name (see `clulatent analysis lanes`)."),
    events_file: Path = typer.Argument(
        ..., help="Path to a JSONL file (one event object per line) or a JSON array of event objects."
    ),
    package_path: Path | None = typer.Option(
        None,
        "--package",
        help=(
            "Optional package to check any path-like payload fields against "
            "(full filesystem containment + symlink-escape check). Without "
            "this, path-like fields still get the lexical relative-POSIX "
            "check. No package is required — this command never writes."
        ),
    ),
) -> None:
    """Validate a batch of candidate analysis events for `lane` without writing anything.

    Read-only, and never touches a package even when `--package` is
    given: no track, manifest, or receipt is written. Exits 0 if every
    event is shape-valid, 1 otherwise.
    """
    if not events_file.exists() or not events_file.is_file():
        _fail(f"events file not found: {events_file}")
        return
    try:
        events = _load_candidate_events_file(events_file)
    except ValueError as exc:
        _fail(str(exc))
        return

    try:
        normalized_lane = analysis_lanes_mod.normalize_analysis_lane_name(lane)
    except analysis_lanes_mod.AnalysisEventError as exc:
        _fail(str(exc))
        return

    package_root: Path | None = None
    if package_path is not None:
        if not package_path.exists() or not package_path.is_dir():
            _fail(f"Package not found: {package_path}")
            return
        package_root = package_path

    errors, warnings = analysis_lanes_mod.validate_analysis_track(
        events, lane=normalized_lane, package_root=package_root
    )

    console.print(
        f"Validating {len(events)} event(s) against lane [bold]{safe_console_text(normalized_lane)}[/bold]"
    )
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    if errors:
        console.print(f"[bold red]FAIL[/bold red] — {len(errors)} error(s):")
        for idx, error in enumerate(errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        raise typer.Exit(code=1)

    console.print("[bold green]PASS[/bold green] — all events are shape-valid")


_DRY_RUN_PACKAGE_STATUS_LABELS = {
    "skipped": "[dim]skipped (no --package given)[/dim]",
    "not_found": "[bold red]package not found[/bold red]",
    "not_a_directory": "[bold red]package path is not a directory[/bold red]",
    "locked": "[yellow]locked — write would be refused[/yellow]",
    "lock_invalid": "[yellow]lock invalid — write would be refused[/yellow]",
    "lock_partial": "[yellow]lock partial — write would be refused[/yellow]",
    "writable": "[green]unlocked — write would be eligible[/green]",
}


def _print_dry_run_report(result_file: Path, report: DryRunReport) -> None:
    """Print a V1 dry-run report -- shared by `dry-run-adapter` and `import-adapter-result --dry-run`."""
    console.print(f"Dry-run [bold]{safe_console_text(str(result_file))}[/bold]")
    console.print(f"  adapter_name:    {safe_console_text(report.adapter_name)}")
    console.print(f"  adapter_version: {safe_console_text(report.adapter_version)}")
    console.print(f"  tool_name:       {safe_console_text(report.tool_name)}")
    console.print(f"  tool_version:    {safe_console_text(report.tool_version)}")
    console.print(f"  model_name:      {safe_console_text(report.model_name)}")
    console.print(f"  model_version:   {safe_console_text(report.model_version)}")

    if report.lanes:
        lanes_table = Table(title="Lanes")
        lanes_table.add_column("Lane")
        lanes_table.add_column("Events", justify="right")
        for lane in report.lanes:
            lanes_table.add_row(safe_console_text(lane), str(report.event_counts.get(lane, 0)))
        console.print(lanes_table)
    else:
        console.print("  lanes:           (none)")
    console.print(f"  total events:    {report.total_event_count}")

    if report.warnings:
        console.print(f"[yellow]{len(report.warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(report.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    package_label = _DRY_RUN_PACKAGE_STATUS_LABELS.get(
        report.package_status, safe_console_text(report.package_status)
    )
    console.print(f"  package status:  {package_label}")
    console.print(f"  would write:     {report.would_write}")

    if report.errors:
        console.print(f"[bold red]FAIL[/bold red] — {len(report.errors)} error(s):")
        for idx, error in enumerate(report.errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        return

    if dry_run_is_hard_package_error(report):
        console.print(f"[bold red]FAIL[/bold red] — package path error: {package_label}")
        return

    console.print("[bold green]PASS[/bold green] — adapter result is structurally valid (nothing written)")


@analysis_app.command(name="dry-run-adapter")
def analysis_dry_run_adapter(
    result_file: Path = typer.Argument(
        ..., help="Path to an adapter result JSON file (V1 AdapterResult shape)."
    ),
    package_path: Path | None = typer.Option(
        None,
        "--package",
        help=(
            "Optional package to check write eligibility against (existence, "
            "directory, lock status) and to fully path-safety-check any "
            "path-like fields against. Never written to, even if writable."
        ),
    ),
) -> None:
    """Dry-run a candidate adapter result JSON file: validate, never write.

    Loads `result_file` as a V1 `AdapterResult`, validates it
    with the same `analysis_adapters.validate_adapter_result` a real
    write would use, and reports lanes/event counts/warnings/errors
    plus (if `--package` is given) whether that package would currently
    accept the write. Writes nothing — no track, manifest, receipt, or
    lock file is ever touched, regardless of the report's outcome.

    Exits 0 if the adapter result is structurally valid, whether or not
    `--package` is given, and whether or not the package (if given) is
    currently locked — a locked package is reported as a write-eligibility
    warning, not a validation failure. Exits 1 if the result fails
    validation, if `result_file` is missing/malformed/non-object JSON, or
    if `--package` was given but does not exist / is not a directory.
    """
    if not result_file.exists() or not result_file.is_file():
        _fail(f"adapter result file not found: {result_file}")
        return
    try:
        result = load_adapter_result_json(result_file)
    except AdapterResultLoadError as exc:
        _fail(str(exc))
        return

    report = dry_run_adapter_result(result, package_path=package_path)
    _print_dry_run_report(result_file, report)

    if report.errors or dry_run_is_hard_package_error(report):
        raise typer.Exit(code=1)


@analysis_app.command(name="import-adapter-result")
def analysis_import_adapter_result(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    result_file: Path = typer.Argument(
        ..., help="Path to an adapter result JSON file (V1 AdapterResult shape)."
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/analyze.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "Validate and report only, exactly like `analysis dry-run-adapter "
            "--package`; writes nothing."
        ),
    ),
) -> None:
    """Commit an already-produced adapter result JSON file into `package_path`.

    Loads `result_file` as a V1 `AdapterResult`, validates it
    (V1's `validate_adapter_result`, which validates every
    non-empty lane through V1's `validate_analysis_track`), then
    writes every non-empty lane through `analysis_adapters.write_adapter_result`
    -- the same V1 writer bridge that only ever calls V1's
    `append_analysis_events`. This command adds no new write path: it
    does not touch `manifest.json`, a `tracks/*.jsonl` file, or
    `receipts/*.jsonl` directly, and adds no new atomicity beyond what
    the writer already provides per lane.

    Each lane is written with its own independent writer call. If an
    earlier lane's write succeeds and a later lane's write then fails,
    the earlier lane's write is **not** rolled back -- this command is
    per-lane atomic, not whole-result atomic, exactly like the Phase
    2.10 writer bridge it calls. Nothing is written for *any* lane if
    validation fails before the first write, if the package does not
    exist or is not a directory, or if the package is currently locked
    (checked before the first lane's write, and unchanged for the
    duration of a single command invocation).

    With `--dry-run`, behaves exactly like `analysis dry-run-adapter
    --package package_path result_file` and writes nothing.

    Exits 0 only if every non-empty lane was written. Exits 1 for
    malformed/non-object JSON, a missing result file, a failed
    validation, a missing/non-directory/locked package, or any other
    writer refusal -- with a clean error message, never a traceback.
    """
    if not result_file.exists() or not result_file.is_file():
        _fail(f"adapter result file not found: {result_file}")
        return
    try:
        result = load_adapter_result_json(result_file)
    except AdapterResultLoadError as exc:
        _fail(str(exc))
        return

    if dry_run:
        report = dry_run_adapter_result(result, package_path=package_path)
        _print_dry_run_report(result_file, report)
        if report.errors or dry_run_is_hard_package_error(report):
            raise typer.Exit(code=1)
        return

    # Validate before ever attempting to write anything -- reuses the
    # exact same V1 validation the writer bridge itself runs
    # immediately before writing (below), so nothing here is a second,
    # divergent copy of that logic.
    report = dry_run_adapter_result(result, package_path=package_path)
    if report.errors:
        console.print(f"[bold red]FAIL[/bold red] — {len(report.errors)} error(s):")
        for idx, error in enumerate(report.errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        raise typer.Exit(code=1)
    if dry_run_is_hard_package_error(report):
        package_label = _DRY_RUN_PACKAGE_STATUS_LABELS.get(
            report.package_status, safe_console_text(report.package_status)
        )
        console.print(f"[bold red]FAIL[/bold red] — package path error: {package_label}")
        raise typer.Exit(code=1)

    try:
        write_results = write_adapter_result(
            package_path,
            result,
            write_receipt=receipt,
            force_stale_lock=force_stale_lock,
        )
    except AnalysisAdapterError as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Adapter result imported[/bold green] -> {safe_console_text(str(package_path))}"
    )
    console.print(f"  adapter_name: {safe_console_text(report.adapter_name)}")

    lanes_table = Table(title="Lanes written")
    lanes_table.add_column("Lane")
    lanes_table.add_column("Events", justify="right")
    total_events = 0
    receipt_path: Path | None = None
    for write_result in write_results:
        lanes_table.add_row(safe_console_text(write_result.lane), str(write_result.events_written))
        total_events += write_result.events_written
        if write_result.receipt_path is not None:
            receipt_path = write_result.receipt_path
    console.print(lanes_table)
    console.print(f"  total events written: {total_events}")

    if receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(receipt_path))}")
    else:
        console.print("  receipt: skipped (--no-receipt)")

    if report.warnings:
        console.print(f"[yellow]{len(report.warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(report.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")


@analysis_app.command(name="generate-fixture-adapter-result")
def analysis_generate_fixture_adapter_result(
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the fixture adapter result JSON to this file instead of printing to stdout.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite --output if it already exists."),
) -> None:
    """Print (or write) a deterministic, non-ML fixture adapter result JSON document.

    Generates the V1 fixture `AdapterResult` (safe, synthetic
    example events across `scene_events`, `visual_change_events`,
    `audio_transient_events`, and `cross_lane_link_events`) and prints
    it as JSON, or writes it to `--output` if given. This command reads
    no video/audio, runs no subprocess, and writes no package file --
    it only ever produces one self-contained JSON document, either on
    stdout or at the given `--output` path.

    The generated JSON is compatible with `analysis dry-run-adapter`
    and `analysis import-adapter-result`: pipe or pass its output
    straight to either command to see the fixture pipeline work
    end-to-end.
    """
    text = json.dumps(fixture_adapter_result_to_dict(), indent=2, sort_keys=True)

    if output_file is None:
        console.print(text, markup=False, soft_wrap=True)
        return

    if output_file.exists() and not force:
        _fail(f"output file already exists (use --force to overwrite): {output_file}")
        return
    if not output_file.parent.exists() or not output_file.parent.is_dir():
        _fail(f"output directory does not exist: {output_file.parent}")
        return

    output_file.write_text(text + "\n", encoding="utf-8")
    console.print(
        f"[bold green]Fixture adapter result written[/bold green] -> {safe_console_text(str(output_file))}"
    )


@analysis_app.command(name="generate-ffmpeg-visual-change-adapter-result")
def analysis_generate_ffmpeg_visual_change_adapter_result(
    media_path: Path = typer.Argument(
        ..., help="Path to a real media file to scan for ffmpeg-detected visual-change candidates."
    ),
    output_file: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the adapter result JSON to this file instead of printing to stdout.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite --output if it already exists."),
    threshold: float = typer.Option(
        VISUAL_CHANGE_DEFAULT_THRESHOLD,
        "--threshold",
        help=(
            "ffmpeg scdet scene-change threshold, 0-100 (higher = fewer, stronger "
            "candidates). Default matches ffmpeg's own scdet default (10)."
        ),
    ),
    max_events: int = typer.Option(
        VISUAL_CHANGE_DEFAULT_MAX_EVENTS,
        "--max-events",
        help="Maximum number of visual-change candidate events to keep (bounded 1-2000).",
    ),
) -> None:
    """Run ffmpeg's `scdet` filter over `media_path` and print/write a candidate adapter result.

    Step 1 of the polished real-adapter workflow (V1): generate
    -> `analysis dry-run-adapter` -> `analysis import-adapter-result`
    -> `validate`. Produces a V1 `AdapterResult` JSON document
    containing only `visual_change_events` -- one per ffmpeg-detected
    scene-score candidate above `--threshold`, described with hedged
    evidence language ("visual-change candidate", "ffmpeg scene-score
    candidate"), never as a confirmed cut or semantic scene. This
    command reads `media_path` via ffmpeg only -- it never touches,
    requires, or creates a `.clulatent` package, runs no ML model, and
    performs no semantic scene analysis. Remember: this output is
    candidate evidence, not confirmed truth -- it only becomes
    canonical once dry-run and import (unchanged, V1) have
    validated and written it.

    With no `--output`, the JSON and only the JSON is printed to
    stdout (safe to pipe straight into `analysis dry-run-adapter` or
    `analysis import-adapter-result`). With `--output`, a human-
    readable summary (adapter name, lanes generated, per-lane and
    total event counts, and the evidence reminder above) is printed
    instead, and the JSON is written to `--output`.

    The generated JSON is compatible with `analysis dry-run-adapter`
    and `analysis import-adapter-result`, exactly like `analysis
    generate-fixture-adapter-result`'s output.

    Exits 0 on success (including zero candidates found, which is a
    valid result). Exits 1 with a clean error message -- never a
    traceback -- if ffmpeg is missing, `media_path` does not exist or
    is not readable as media, `--threshold`/`--max-events` are out of
    bounds, or `--output` cannot be written (already exists without
    `--force`, its parent directory does not exist, or it names an
    existing directory rather than a file).
    """
    try:
        result = build_visual_change_adapter_result(
            media_path, threshold=threshold, max_events=max_events
        )
    except VisualChangeAdapterError as exc:
        _fail(str(exc))
        return

    text = json.dumps(visual_change_adapter_result_to_dict(result), indent=2, sort_keys=True)

    if output_file is None:
        console.print(text, markup=False, soft_wrap=True)
        return

    if output_file.exists() and not force:
        _fail(f"output file already exists (use --force to overwrite): {output_file}")
        return
    if output_file.exists() and output_file.is_dir():
        _fail(f"output path is a directory, not a file: {output_file}")
        return
    if not output_file.parent.exists() or not output_file.parent.is_dir():
        _fail(f"output directory does not exist: {output_file.parent}")
        return

    output_file.write_text(text + "\n", encoding="utf-8")

    console.print(
        f"[bold green]Visual-change adapter result written[/bold green] -> "
        f"{safe_console_text(str(output_file))}"
    )
    console.print(f"  adapter_name: {safe_console_text(VISUAL_CHANGE_ADAPTER_NAME)}")

    lanes_table = Table(title="Lanes generated")
    lanes_table.add_column("Lane")
    lanes_table.add_column("Events", justify="right")
    total_events = 0
    for lane, events in result.events_by_lane.items():
        lanes_table.add_row(safe_console_text(lane), str(len(events)))
        total_events += len(events)
    console.print(lanes_table)
    console.print(f"  total events: {total_events}")
    console.print(
        "[dim]Reminder: this is candidate evidence, not confirmed truth -- run "
        "`analysis dry-run-adapter`, then `analysis import-adapter-result`, to "
        "make it canonical.[/dim]"
    )


# --- clulatent audio-digest ... (V1) ----------------------------------
#
# This group validates and writes already-produced audio digest
# records (V1 schema validation + V1 writer/receipts).
# It runs no FFmpeg, librosa, Essentia, aubio, Basic Pitch, Demucs,
# YAMNet, PANNs, or OpenL3 -- no audio adapter of any kind. It is a
# manual/scripted door for evidence a caller already produced
# elsewhere, exactly like `clulatent analysis ...`. See
# docs/PHASE_3_6_AUDIO_DIGEST_CLI_COMMANDS.md.


_AUDIO_DIGEST_RECEIPT_OPTION_HELP = (
    "Recorded in receipts/audio_digest.jsonl (V1); does not affect validation."
)


def _run_audio_digest_append(
    package_path: Path,
    events: list[dict[str, Any]],
    *,
    tool_name: str,
    tool_version: str,
    linked_evidence: dict[str, Any] | None,
    write_receipt: bool,
    force_stale_lock: bool,
) -> None:
    try:
        result = audio_digest_writer_mod.append_audio_digest_events(
            package_path,
            events,
            tool_name=tool_name,
            tool_version=tool_version,
            linked_evidence=linked_evidence,
            write_receipt=write_receipt,
            force_stale_lock=force_stale_lock,
        )
    except audio_digest_writer_mod.AudioDigestWriteError as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Audio digest events written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  events written: {result.events_written}")
    for event_id in result.event_ids:
        console.print(f"    - {safe_console_text(event_id)}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


@audio_digest_app.command(name="types")
def audio_digest_types_cmd() -> None:
    """Print the supported audio digest record type names (V1 catalog)."""
    for name in audio_digest_mod.AUDIO_DIGEST_RECORD_TYPES:
        console.print(safe_console_text(name))


@audio_digest_app.command(name="validate-file")
def audio_digest_validate_file(
    events_file: Path = typer.Argument(
        ..., help="Path to a JSONL file (one record object per line) or a JSON array of record objects."
    ),
    package_path: Path | None = typer.Option(
        None,
        "--package",
        help=(
            "Optional package to check any path-like payload fields "
            "(e.g. audio_feature_series.payload.data_path) against (full "
            "filesystem containment + symlink-escape check). Without "
            "this, path-like fields still get the lexical relative-POSIX "
            "check. No package is required -- this command never writes."
        ),
    ),
) -> None:
    """Validate a batch of candidate audio digest records without writing anything.

    Read-only, and never touches a package even when `--package` is
    given: no track, manifest, or receipt is written. Exits 0 if every
    record is shape-valid, 1 otherwise.
    """
    if not events_file.exists() or not events_file.is_file():
        _fail(f"events file not found: {events_file}")
        return
    try:
        events = _load_candidate_events_file(events_file)
    except ValueError as exc:
        _fail(str(exc))
        return

    package_root: Path | None = None
    if package_path is not None:
        if not package_path.exists() or not package_path.is_dir():
            _fail(f"Package not found: {package_path}")
            return
        package_root = package_path

    errors, warnings = audio_digest_mod.validate_audio_digest_track(
        events, package_root=package_root
    )

    console.print(f"Validating {len(events)} audio digest record(s)")
    if warnings:
        console.print(f"[yellow]{len(warnings)} warning(s):[/yellow]")
        for idx, warning in enumerate(warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    if errors:
        console.print(f"[bold red]FAIL[/bold red] — {len(errors)} error(s):")
        for idx, error in enumerate(errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        raise typer.Exit(code=1)

    console.print("[bold green]PASS[/bold green] — all records are shape-valid")


@audio_digest_app.command(name="append")
def audio_digest_append(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_json: str = typer.Option(
        ..., "--event-json", help="A single audio digest record as a JSON object."
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help=_AUDIO_DIGEST_RECEIPT_OPTION_HELP),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help=_AUDIO_DIGEST_RECEIPT_OPTION_HELP
    ),
    linked_evidence_json: str | None = typer.Option(
        None,
        "--linked-evidence-json",
        help="Linked-evidence metadata as a JSON object. " + _AUDIO_DIGEST_RECEIPT_OPTION_HELP,
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/audio_digest.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Validate and append one audio digest record from --event-json.

    Writes evidence only -- the record is validated against the Phase
    3.4 schema and appended via the V1 writer. Refuses a locked
    package, an unsupported type, or an invalid record; nothing is
    written on any refusal.
    """
    event = _parse_payload_json(event_json, "--event-json")
    linked_evidence = (
        _parse_payload_json(linked_evidence_json, "--linked-evidence-json")
        if linked_evidence_json
        else None
    )
    _run_audio_digest_append(
        package_path,
        [event],
        tool_name=tool_name,
        tool_version=tool_version,
        linked_evidence=linked_evidence,
        write_receipt=receipt,
        force_stale_lock=force_stale_lock,
    )


@audio_digest_app.command(name="append-file")
def audio_digest_append_file(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    events_file: Path = typer.Argument(
        ..., help="Path to a JSONL file (one record object per line) or a JSON array of record objects."
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help=_AUDIO_DIGEST_RECEIPT_OPTION_HELP),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help=_AUDIO_DIGEST_RECEIPT_OPTION_HELP
    ),
    linked_evidence_json: str | None = typer.Option(
        None,
        "--linked-evidence-json",
        help="Linked-evidence metadata as a JSON object. " + _AUDIO_DIGEST_RECEIPT_OPTION_HELP,
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/audio_digest.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Validate and append every audio digest record in `events_file`.

    All records in the batch are validated together before anything is
    written -- a single invalid record refuses the whole batch, so a
    partial write into tracks/audio_digest_events.jsonl or
    manifest.json never happens. `events_file` may be JSONL or a JSON
    array; an empty file or empty batch is refused cleanly.
    """
    if not events_file.exists() or not events_file.is_file():
        _fail(f"events file not found: {events_file}")
        return
    try:
        events = _load_candidate_events_file(events_file)
    except ValueError as exc:
        _fail(str(exc))
        return

    linked_evidence = (
        _parse_payload_json(linked_evidence_json, "--linked-evidence-json")
        if linked_evidence_json
        else None
    )
    _run_audio_digest_append(
        package_path,
        events,
        tool_name=tool_name,
        tool_version=tool_version,
        linked_evidence=linked_evidence,
        write_receipt=receipt,
        force_stale_lock=force_stale_lock,
    )


# --- clulatent audio-digest ... retrieval commands (V1) --------------
#
# Read-only retrieval over an already-validated audio digest track
# (V1 primitives). No audio adapter, no mutation, no receipt,
# no index rebuild. See docs/PHASE_3_9_AUDIO_DIGEST_RETRIEVAL_CLI.md.

_AUDIO_DIGEST_DETAIL_PRIORITY_KEYS = ("label", "summary", "feature")


def _audio_digest_detail_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in _AUDIO_DIGEST_DETAIL_PRIORITY_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(payload, sort_keys=True)
    return json.dumps(payload, sort_keys=True)


def _print_audio_digest_record(event: dict[str, Any]) -> None:
    event_id = event.get("id")
    record_type = event.get("type")
    console.print(
        f"[bold]{safe_console_text(str(event_id))}[/bold] "
        f"({safe_console_text(str(record_type))})"
    )
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))}"
        )
    confidence = event.get("confidence")
    if confidence is not None:
        console.print(f"  confidence: {safe_console_text(str(confidence))}")
    producer = event.get("producer")
    if isinstance(producer, dict):
        producer_name = producer.get("name")
        producer_version = producer.get("version")
        console.print(
            f"  producer: {safe_console_text(str(producer_name))} "
            f"{safe_console_text(str(producer_version))}"
        )
    payload = event.get("payload")
    console.print("  payload:")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


def _print_audio_digest_events_table(events: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("Type")
    table.add_column("ID")
    table.add_column("Detail")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("type"))),
            safe_console_text(str(event.get("id"))),
            safe_console_text(_audio_digest_detail_text(event.get("payload"))),
        )
    console.print(table)


@audio_digest_app.command(name="get")
def audio_digest_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="The audio digest record id to retrieve."),
) -> None:
    """Print one already-validated audio digest record by id.

    Read-only: never mutates the package, never writes a receipt, never
    touches the index. Prints "no result" cleanly (exit 0) if the id is
    not found; fails cleanly (exit 1) if the package or its audio
    digest track cannot be safely read.
    """
    try:
        event = audio_digest_retrieval_mod.get_audio_digest_event_by_id(package_path, event_id)
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(f"[yellow]No audio digest record found with id '{safe_console_text(event_id)}'.[/yellow]")
        return

    _print_audio_digest_record(event)


@audio_digest_app.command(name="query-time")
def audio_digest_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Range end, in milliseconds."),
) -> None:
    """List already-validated audio digest records overlapping a time range.

    Read-only. An empty result (no overlapping records, or no audio
    digest track) is printed cleanly (exit 0), not an error.
    """
    try:
        events = audio_digest_retrieval_mod.query_audio_digest_by_time_range(
            package_path, start_ms, end_ms
        )
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No audio digest records overlap that time range.[/yellow]")
        return

    _print_audio_digest_events_table(events, title="Audio digest records (time range)")


@audio_digest_app.command(name="query-type")
def audio_digest_query_type(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    record_type: str = typer.Argument(..., help="Audio digest record type to match."),
) -> None:
    """List already-validated audio digest records of one record type.

    Read-only. An empty result (no matching records, or no audio digest
    track) is printed cleanly (exit 0), not an error.
    """
    if not audio_digest_mod.is_supported_audio_digest_type(record_type):
        _fail(
            f"Unsupported audio digest record type: {record_type!r} "
            f"(must be one of {sorted(audio_digest_mod.SUPPORTED_AUDIO_DIGEST_TYPES)})"
        )
        return

    try:
        events = audio_digest_retrieval_mod.query_audio_digest_by_type(package_path, record_type)
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print(f"[yellow]No audio digest records of type '{safe_console_text(record_type)}'.[/yellow]")
        return

    _print_audio_digest_events_table(events, title=f"Audio digest records (type={record_type})")


@audio_digest_app.command(name="query-linked")
def audio_digest_query_linked(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    evidence_id: str = typer.Argument(..., help="Evidence id to find linked audio digest records for."),
) -> None:
    """List already-validated audio digest records linked to an evidence id.

    Matches a record whose own id equals `evidence_id`, or whose
    payload lists it as a linked event/feature-series/digest-segment
    id. Read-only. An empty result is printed cleanly (exit 0), not an
    error.
    """
    try:
        events = audio_digest_retrieval_mod.query_audio_digest_by_linked_evidence_id(
            package_path, evidence_id
        )
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print(
            f"[yellow]No audio digest records linked to evidence id "
            f"'{safe_console_text(evidence_id)}'.[/yellow]"
        )
        return

    _print_audio_digest_events_table(events, title="Audio digest records (linked evidence)")


@audio_digest_app.command(name="query-salience")
def audio_digest_query_salience(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    min_salience: float = typer.Option(..., "--min-salience", help="Minimum payload.salience (0.0 - 1.0)."),
) -> None:
    """List already-validated audio_digest_segment records at or above a salience threshold.

    Only `audio_digest_segment` records carry `payload.salience`, so no
    other record type can ever match. Read-only. An empty result is
    printed cleanly (exit 0), not an error.
    """
    if not (0.0 <= min_salience <= 1.0):
        _fail(f"--min-salience must be between 0.0 and 1.0, got {min_salience}")
        return

    try:
        events = audio_digest_retrieval_mod.query_audio_digest_by_salience(
            package_path, min_salience=min_salience
        )
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print(f"[yellow]No audio digest segments with salience >= {min_salience}.[/yellow]")
        return

    _print_audio_digest_events_table(events, title="Audio digest records (salience)")


@audio_digest_app.command(name="llm-context")
def audio_digest_llm_context(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int | None = typer.Option(None, "--start-ms", help="Optional range start, in milliseconds."),
    end_ms: int | None = typer.Option(None, "--end-ms", help="Optional range end, in milliseconds."),
    max_events: int = typer.Option(
        audio_digest_retrieval_mod.DEFAULT_MAX_CONTEXT_EVENTS,
        "--max-events",
        help="Maximum number of records to return.",
    ),
) -> None:
    """Print a small, bounded set of audio digest records safe for LLM context.

    Store deep, show shallow: prefers already-bounded, already-caveated
    `audio_llm_context_packet` records, and only falls back to
    salience-sorted `audio_digest_segment` records when no packet
    exists. Never prints raw `audio_feature_series` data. Read-only. An
    empty result is printed cleanly (exit 0), not an error.
    """
    try:
        events = audio_digest_retrieval_mod.select_audio_digest_llm_context(
            package_path, start_ms=start_ms, end_ms=end_ms, max_events=max_events
        )
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No audio digest context available for that range.[/yellow]")
        return

    for event in events:
        _print_audio_digest_record(event)
        console.print("")


@audio_digest_app.command(name="retrieval-summary")
def audio_digest_retrieval_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's audio digest track.

    Record-type counts, record ids, and the min/max time range covered
    -- no prose, no inference. Read-only. A package with no audio
    digest track prints a zeroed-out summary cleanly (exit 0), not an
    error.
    """
    try:
        events = audio_digest_retrieval_mod.load_audio_digest_events(package_path)
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        _fail(str(exc))
        return

    summary = audio_digest_retrieval_mod.summarize_audio_digest_retrieval_result(events)

    console.print(f"event_count: {summary['event_count']}")
    console.print("record_type_counts:")
    for record_type, count in sorted(summary["record_type_counts"].items()):
        console.print(f"  {safe_console_text(record_type)}: {count}")
    console.print(f"t_start_ms: {summary['t_start_ms']}")
    console.print(f"t_end_ms: {summary['t_end_ms']}")
    console.print("event_ids:")
    for event_id in summary["event_ids"]:
        console.print(f"  - {safe_console_text(event_id)}")


def _print_visual_change_record(event: dict[str, Any]) -> None:
    event_id = event.get("id")
    console.print(f"[bold]{safe_console_text(str(event_id))}[/bold] (visual_change_candidate)")
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))}"
        )
    payload = event.get("payload")
    console.print("  payload:")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


def _print_visual_change_events_table(events: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("ID")
    table.add_column("Strength")
    table.add_column("Normalized Delta")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("id"))),
            safe_console_text(str(payload.get("strength"))),
            safe_console_text(str(metrics.get("normalized_delta"))),
        )
    console.print(table)


@visual_change_app.command(name="analyze")
def visual_change_analyze(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing visual change track. Without this, `analyze` refuses if one already exists.",
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help="Recorded on the receipt as the producing tool."),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help="Recorded on the receipt as the producing tool's version."
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/visual_change.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Compute bounded, non-semantic visual change candidates from stored keyframes.

    Computes one `visual_change_candidate` record per adjacent pair of
    stored keyframes and writes/replaces `tracks/visual_change_candidates
    .jsonl`. Requires Pillow (optional 'visual' extra) and at least 2
    stored keyframes. Refuses to overwrite an existing visual change
    track unless `--force` is passed; refuses a locked package outright.
    """
    if not visual_change_mod.is_available():
        _fail(
            "Pillow is not installed. Install the optional 'visual' extra "
            "(pip install \"clu-latent[visual]\") to run `clulatent visual-change analyze`."
        )
        return

    try:
        result = visual_change_writer_mod.analyze_visual_change(
            package_path,
            tool_name=tool_name,
            tool_version=tool_version,
            force=force,
            write_receipt=receipt,
            force_stale_lock=force_stale_lock,
        )
    except (
        visual_change_writer_mod.VisualChangeWriteError,
        visual_change_mod.VisualChangeComputeError,
        visual_change_mod.VisualChangeUnavailableError,
        keyframe_retrieval_mod.KeyframeRetrievalError,
    ) as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Visual change candidates written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  events written: {result.events_written}")
    for strength, count in sorted(result.strength_counts.items()):
        console.print(f"  strength ({safe_console_text(strength)}): {count}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


@visual_change_app.command(name="summary")
def visual_change_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's visual change track.

    Event count, first/last event, and strength-bucket counts -- no
    prose, no inference. Read-only. A package with no visual change
    track prints a zeroed-out summary cleanly (exit 0), not an error.
    """
    try:
        summary = visual_change_retrieval_mod.summarize_visual_change(package_path)
    except visual_change_retrieval_mod.VisualChangeRetrievalError as exc:
        _fail(str(exc))
        return

    console.print(f"event_count: {summary['event_count']}")
    console.print(f"first_event_id: {safe_console_text(str(summary['first_event_id']))}")
    console.print(f"first_timestamp_ms: {summary['first_timestamp_ms']}")
    console.print(f"last_event_id: {safe_console_text(str(summary['last_event_id']))}")
    console.print(f"last_timestamp_ms: {summary['last_timestamp_ms']}")
    console.print("strength_counts:")
    for strength, count in sorted(summary["strength_counts"].items()):
        console.print(f"  {safe_console_text(strength)}: {count}")


@visual_change_app.command(name="get")
def visual_change_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    event_id: str = typer.Argument(..., help="The visual change record id to retrieve."),
) -> None:
    """Print one already-computed visual change record by id.

    Read-only: never mutates the package, never writes a receipt, never
    runs Pillow. Prints "no result" cleanly (exit 0) if the id is not
    found; fails cleanly (exit 1) if the package or its visual change
    track cannot be safely read.
    """
    try:
        event = visual_change_retrieval_mod.get_visual_change_event_by_id(package_path, event_id)
    except visual_change_retrieval_mod.VisualChangeRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(
            f"[yellow]No visual change record found with id '{safe_console_text(event_id)}'.[/yellow]"
        )
        return

    _print_visual_change_record(event)


@visual_change_app.command(name="query-time")
def visual_change_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Range end, in milliseconds."),
) -> None:
    """List already-computed visual change records overlapping a time range.

    Read-only. An empty result (no overlapping records, or no visual
    change track) is printed cleanly (exit 0), not an error.
    """
    try:
        events = visual_change_retrieval_mod.query_visual_change_by_time_range(
            package_path, start_ms, end_ms
        )
    except visual_change_retrieval_mod.VisualChangeRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No visual change records overlap that time range.[/yellow]")
        return

    _print_visual_change_events_table(events, title="Visual change records (time range)")


def _print_changed_region_record(event: dict[str, Any]) -> None:
    event_id = event.get("id")
    console.print(f"[bold]{safe_console_text(str(event_id))}[/bold] (changed_region_candidate)")
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))}"
        )
    payload = event.get("payload")
    console.print("  payload:")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


def _print_changed_region_events_table(events: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("ID")
    table.add_column("Visual Change ID")
    table.add_column("Strength")
    table.add_column("Scope")
    table.add_column("Region Delta")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("id"))),
            safe_console_text(str(payload.get("visual_change_id"))),
            safe_console_text(str(payload.get("strength"))),
            safe_console_text(str(payload.get("change_scope"))),
            safe_console_text(str(metrics.get("region_normalized_delta"))),
        )
    console.print(table)


@changed_regions_app.command(name="analyze")
def changed_regions_analyze(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing changed-region track. Without this, `analyze` refuses if one already exists.",
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help="Recorded on the receipt as the producing tool."),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help="Recorded on the receipt as the producing tool's version."
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/changed_region.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Compute bounded, non-semantic changed-region candidates from visual change evidence.

    Computes one `changed_region_candidate` record per already-computed
    `visual_change_candidate` (V1), localizing the strongest
    grid-cell region of difference between its linked source/target
    keyframe images, and writes/replaces `tracks/changed_region_candidates
    .jsonl`. Requires Pillow (optional 'visual' extra) and an existing,
    non-empty visual change track -- run `clulatent visual-change
    analyze` first. Refuses to overwrite an existing changed-region
    track unless `--force` is passed; refuses a locked package outright.
    """
    if not changed_region_mod.is_available():
        _fail(
            "Pillow is not installed. Install the optional 'visual' extra "
            "(pip install \"clu-latent[visual]\") to run `clulatent changed-regions analyze`."
        )
        return

    try:
        result = changed_region_writer_mod.analyze_changed_regions(
            package_path,
            tool_name=tool_name,
            tool_version=tool_version,
            force=force,
            write_receipt=receipt,
            force_stale_lock=force_stale_lock,
        )
    except (
        changed_region_writer_mod.ChangedRegionWriteError,
        changed_region_mod.ChangedRegionComputeError,
        changed_region_mod.ChangedRegionUnavailableError,
        visual_change_retrieval_mod.VisualChangeRetrievalError,
    ) as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Changed-region candidates written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  events written: {result.events_written}")
    for strength, count in sorted(result.strength_counts.items()):
        console.print(f"  strength ({safe_console_text(strength)}): {count}")
    for scope, count in sorted(result.change_scope_counts.items()):
        console.print(f"  scope ({safe_console_text(scope)}): {count}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


@changed_regions_app.command(name="summary")
def changed_regions_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's changed-region track.

    Event count, first/last event, strength-bucket counts, and
    change_scope-bucket counts -- no prose, no inference. Read-only. A
    package with no changed-region track prints a zeroed-out summary
    cleanly (exit 0), not an error.
    """
    try:
        summary = changed_region_retrieval_mod.summarize_changed_regions(package_path)
    except changed_region_retrieval_mod.ChangedRegionRetrievalError as exc:
        _fail(str(exc))
        return

    console.print(f"event_count: {summary['event_count']}")
    console.print(f"first_event_id: {safe_console_text(str(summary['first_event_id']))}")
    console.print(f"first_timestamp_ms: {summary['first_timestamp_ms']}")
    console.print(f"last_event_id: {safe_console_text(str(summary['last_event_id']))}")
    console.print(f"last_timestamp_ms: {summary['last_timestamp_ms']}")
    console.print("strength_counts:")
    for strength, count in sorted(summary["strength_counts"].items()):
        console.print(f"  {safe_console_text(strength)}: {count}")
    console.print("change_scope_counts:")
    for scope, count in sorted(summary["change_scope_counts"].items()):
        console.print(f"  {safe_console_text(scope)}: {count}")


@changed_regions_app.command(name="get")
def changed_regions_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    region_id: str = typer.Argument(..., help="The changed-region record id to retrieve."),
) -> None:
    """Print one already-computed changed-region record by id.

    Read-only: never mutates the package, never writes a receipt, never
    runs Pillow. Prints "no result" cleanly (exit 0) if the id is not
    found; fails cleanly (exit 1) if the package or its changed-region
    track cannot be safely read.
    """
    try:
        event = changed_region_retrieval_mod.get_changed_region_event_by_id(package_path, region_id)
    except changed_region_retrieval_mod.ChangedRegionRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(
            f"[yellow]No changed-region record found with id '{safe_console_text(region_id)}'.[/yellow]"
        )
        return

    _print_changed_region_record(event)


@changed_regions_app.command(name="query-time")
def changed_regions_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Range end, in milliseconds."),
) -> None:
    """List already-computed changed-region records overlapping a time range.

    Read-only. An empty result (no overlapping records, or no
    changed-region track) is printed cleanly (exit 0), not an error.
    """
    try:
        events = changed_region_retrieval_mod.query_changed_regions_by_time_range(
            package_path, start_ms, end_ms
        )
    except changed_region_retrieval_mod.ChangedRegionRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No changed-region records overlap that time range.[/yellow]")
        return

    _print_changed_region_events_table(events, title="Changed-region records (time range)")


@changed_regions_app.command(name="query-visual-change")
def changed_regions_query_visual_change(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    visual_change_id: str = typer.Argument(..., help="The visual change candidate id to look up linked regions for."),
) -> None:
    """List already-computed changed-region records linked to one visual change candidate.

    Read-only. An empty result (no changed-region record links to this
    visual change candidate id, or no changed-region track) is printed
    cleanly (exit 0), not an error.
    """
    try:
        events = changed_region_retrieval_mod.query_changed_regions_by_visual_change_id(
            package_path, visual_change_id
        )
    except changed_region_retrieval_mod.ChangedRegionRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print(
            "[yellow]No changed-region records are linked to visual change candidate "
            f"'{safe_console_text(visual_change_id)}'.[/yellow]"
        )
        return

    _print_changed_region_events_table(events, title="Changed-region records (linked to visual change)")


def _print_evidence_bundle_record(event: dict[str, Any]) -> None:
    event_id = event.get("id")
    console.print(f"[bold]{safe_console_text(str(event_id))}[/bold] (evidence_bundle)")
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))}"
        )
    payload = event.get("payload")
    console.print("  payload:")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


def _print_evidence_bundle_events_table(events: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("ID")
    table.add_column("Evidence Counts")
    table.add_column("Missing Evidence")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        evidence_counts = payload.get("evidence_counts") if isinstance(payload.get("evidence_counts"), dict) else {}
        missing_evidence = payload.get("missing_evidence") if isinstance(payload.get("missing_evidence"), list) else []
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("id"))),
            safe_console_text(", ".join(f"{k}={v}" for k, v in sorted(evidence_counts.items()))),
            safe_console_text(", ".join(str(item) for item in missing_evidence)),
        )
    console.print(table)


@evidence_bundles_app.command(name="build")
def evidence_bundles_build(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Bundle time range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Bundle time range end, in milliseconds."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing bundle with the same deterministic id. Without this, `build` refuses.",
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help="Recorded on the receipt as the producing tool."),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help="Recorded on the receipt as the producing tool's version."
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/evidence_bundle.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Collect already-existing package evidence for `[start_ms, end_ms]` into one bundle record.

    Gathers from keyframes, visual change candidates, changed-region
    candidates, audio/speech events, audio digest events, review
    events, analysis lane events, receipts, and validation status --
    all read-only. Never decodes an image, never runs Pillow/FFmpeg/any
    ML model, never infers what the collected evidence means. Writes
    one record to `tracks/evidence_bundles.jsonl`. Refuses to overwrite
    an existing bundle with the same deterministic id unless `--force`
    is passed; refuses a locked package outright.
    """
    try:
        result = evidence_bundle_writer_mod.build_evidence_bundle(
            package_path,
            start_ms=start_ms,
            end_ms=end_ms,
            tool_name=tool_name,
            tool_version=tool_version,
            force=force,
            write_receipt=receipt,
            force_stale_lock=force_stale_lock,
        )
    except evidence_bundle_writer_mod.EvidenceBundleWriteError as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Evidence bundle written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  bundle id: {safe_console_text(result.bundle_id)}")
    for category, count in sorted(result.evidence_counts.items()):
        console.print(f"  evidence ({safe_console_text(category)}): {count}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


@evidence_bundles_app.command(name="preview")
def evidence_bundles_preview(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Bundle time range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Bundle time range end, in milliseconds."),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help="Recorded in the previewed payload as the producing tool."),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help="Recorded in the previewed payload as the producing tool's version."
    ),
) -> None:
    """Print what `evidence-bundles build` would write, without writing anything.

    Read-only: no track write, no manifest update, no receipt. Useful
    for checking evidence coverage for a time range before committing
    a bundle record.
    """
    if end_ms < start_ms:
        _fail(f"end_ms ({end_ms}) must be >= start_ms ({start_ms})")
        return

    manifest = _load_manifest(package_path)
    try:
        payload = evidence_bundle_writer_mod.gather_evidence_bundle_payload(
            package_path,
            manifest,
            start_ms=start_ms,
            end_ms=end_ms,
            tool_name=tool_name,
            tool_version=tool_version,
        )
    except evidence_bundle_writer_mod.EvidenceBundleWriteError as exc:
        _fail(str(exc))
        return

    bundle_id = evidence_bundle_writer_mod.build_evidence_bundle_id(start_ms, end_ms)
    console.print(f"[bold]would build[/bold] {safe_console_text(bundle_id)} (not written)")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


@evidence_bundles_app.command(name="summary")
def evidence_bundles_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's evidence bundle track.

    Bundle count, first/last bundle, per-coverage-category true-count,
    and per-category missing-evidence count -- no prose, no inference.
    Read-only. A package with no evidence bundle track prints a
    zeroed-out summary cleanly (exit 0), not an error.
    """
    try:
        summary = evidence_bundle_retrieval_mod.summarize_evidence_bundles(package_path)
    except evidence_bundle_retrieval_mod.EvidenceBundleRetrievalError as exc:
        _fail(str(exc))
        return

    console.print(f"bundle_count: {summary['bundle_count']}")
    console.print(f"first_bundle_id: {safe_console_text(str(summary['first_bundle_id']))}")
    console.print(f"first_timestamp_ms: {summary['first_timestamp_ms']}")
    console.print(f"last_bundle_id: {safe_console_text(str(summary['last_bundle_id']))}")
    console.print(f"last_timestamp_ms: {summary['last_timestamp_ms']}")
    console.print("coverage_counts:")
    for key, count in sorted(summary["coverage_counts"].items()):
        console.print(f"  {safe_console_text(key)}: {count}")
    console.print("missing_evidence_counts:")
    for category, count in sorted(summary["missing_evidence_counts"].items()):
        console.print(f"  {safe_console_text(category)}: {count}")


@evidence_bundles_app.command(name="get")
def evidence_bundles_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    bundle_id: str = typer.Argument(..., help="The evidence bundle record id to retrieve."),
) -> None:
    """Look up one evidence bundle record by id.

    Read-only. Prints a clean "no result" message (exit 0) if the id
    does not exist.
    """
    try:
        event = evidence_bundle_retrieval_mod.get_evidence_bundle_by_id(package_path, bundle_id)
    except evidence_bundle_retrieval_mod.EvidenceBundleRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(f"[yellow]No evidence bundle found with id '{safe_console_text(bundle_id)}'.[/yellow]")
        return

    _print_evidence_bundle_record(event)


@evidence_bundles_app.command(name="query-time")
def evidence_bundles_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Query time range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Query time range end, in milliseconds."),
) -> None:
    """List evidence bundle records overlapping `[start_ms, end_ms]`.

    Read-only. An empty result (no overlap, or no evidence bundle
    track) is printed cleanly (exit 0), not an error.
    """
    try:
        events = evidence_bundle_retrieval_mod.query_evidence_bundles_by_time_range(package_path, start_ms, end_ms)
    except evidence_bundle_retrieval_mod.EvidenceBundleRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No evidence bundle records overlap this time range.[/yellow]")
        return

    _print_evidence_bundle_events_table(events, title="Evidence bundle records (time overlap)")


def _print_agent_review_record(event: dict[str, Any]) -> None:
    event_id = event.get("id")
    console.print(f"[bold]{safe_console_text(str(event_id))}[/bold] (agent_review_event)")
    t_start_ms = event.get("t_start_ms")
    t_end_ms = event.get("t_end_ms")
    if isinstance(t_start_ms, int) and isinstance(t_end_ms, int):
        console.print(
            f"  time: {safe_console_text(ms_to_timecode(t_start_ms))} - "
            f"{safe_console_text(ms_to_timecode(t_end_ms))}"
        )
    payload = event.get("payload")
    console.print("  payload:")
    console.print(safe_console_text(json.dumps(payload, indent=2, sort_keys=True)))


def _print_agent_review_events_table(events: list[dict[str, Any]], *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Timecode")
    table.add_column("ID")
    table.add_column("Evidence Bundle ID")
    table.add_column("Status")
    table.add_column("Next Step")
    table.add_column("Confidence")
    for event in events:
        t_start_ms = event.get("t_start_ms")
        timecode = ms_to_timecode(t_start_ms) if isinstance(t_start_ms, int) else "?"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        table.add_row(
            safe_console_text(timecode),
            safe_console_text(str(event.get("id"))),
            safe_console_text(str(payload.get("evidence_bundle_id"))),
            safe_console_text(str(payload.get("review_status"))),
            safe_console_text(str(payload.get("recommended_next_step"))),
            safe_console_text(str(payload.get("confidence"))),
        )
    console.print(table)


@agent_review_app.command(name="run")
def agent_review_run(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    bundle_id: str = typer.Argument(..., help="The evidence bundle id to review."),
    claim_file: str | None = typer.Option(
        None,
        "--claim-file",
        help=(
            "Path (package-relative or absolute local file) to a bounded JSON "
            "claim file ({\"claims\": [...]}) to check for support against the "
            "bundle's evidence. Optional."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing review for this bundle. Without this, `run` refuses if one already exists.",
    ),
    tool_name: str = typer.Option(TOOL_NAME, "--tool-name", help="Recorded on the receipt as the producing tool."),
    tool_version: str = typer.Option(
        TOOL_VERSION, "--tool-version", help="Recorded on the receipt as the producing tool's version."
    ),
    receipt: bool = typer.Option(
        True, "--receipt/--no-receipt", help="Write an entry to receipts/agent_review.jsonl (default: yes)."
    ),
    force_stale_lock: bool = typer.Option(False, "--force-stale-lock", help=_FORCE_STALE_LOCK_HELP),
) -> None:
    """Review one evidence bundle and write one bounded, rule-based `agent_review_event` record.

    Never calls an external model, never adds an external agent
    runtime, never adds model adapters -- the review content comes
    entirely from a pure, deterministic read of the bundle's own
    already-computed evidence counts/coverage/missing-evidence/
    validation fields (plus, optionally, a bounded, sanitized claim
    file checked for support against that same evidence). Agent review
    is review of evidence, not invention of truth. Refuses to
    overwrite an existing review for this bundle unless `--force` is
    passed; refuses a locked package outright.
    """
    try:
        result = agent_review_writer_mod.run_agent_review(
            package_path,
            bundle_id,
            tool_name=tool_name,
            tool_version=tool_version,
            claim_file=claim_file,
            force=force,
            write_receipt=receipt,
            force_stale_lock=force_stale_lock,
        )
    except agent_review_writer_mod.AgentReviewWriteError as exc:
        _fail(str(exc))
        return

    console.print(
        f"[bold green]Agent review written[/bold green] -> {safe_console_text(result.track_file)}"
    )
    console.print(f"  review id: {safe_console_text(result.review_id)}")
    console.print(f"  evidence bundle id: {safe_console_text(result.evidence_bundle_id)}")
    console.print(f"  review status: {safe_console_text(result.review_status)}")
    if result.track_created:
        console.print(f"  created {safe_console_text(result.track_file)}")
    if result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(result.receipt_path))}")


@agent_review_app.command(name="preview")
def agent_review_preview(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    bundle_id: str = typer.Argument(..., help="The evidence bundle id to review."),
    claim_file: str | None = typer.Option(
        None,
        "--claim-file",
        help="Path to a bounded JSON claim file ({\"claims\": [...]}) to preview claim-support checks. Optional.",
    ),
) -> None:
    """Print what `agent-review run` would write, without writing anything.

    Read-only: no track write, no manifest update, no receipt. Never
    calls an external model.
    """
    bundle = evidence_bundle_retrieval_mod.get_evidence_bundle_by_id(package_path, bundle_id)
    if bundle is None:
        _fail(f"no evidence bundle with id {bundle_id!r} found in this package")
        return

    claims: list[dict[str, Any]] | None = None
    if claim_file is not None:
        try:
            claims = agent_review_writer_mod.load_and_sanitize_claims(claim_file)
        except agent_review_writer_mod.AgentReviewWriteError as exc:
            _fail(str(exc))
            return

    findings = agent_review_mod.compute_agent_review_findings(bundle, claims=claims)
    review_id = agent_review_writer_mod.build_agent_review_id(bundle["id"])
    console.print(f"[bold]would write[/bold] {safe_console_text(review_id)} (not written)")
    console.print(safe_console_text(json.dumps(findings, indent=2, sort_keys=True)))


@agent_review_app.command(name="summary")
def agent_review_summary(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print a shallow, non-generative index of a package's agent review track.

    Review count, per-status count, escalation count, unsupported-claim
    count, and missing-evidence count -- no prose, no inference.
    Read-only. A package with no agent review track prints a
    zeroed-out summary cleanly (exit 0), not an error.
    """
    try:
        summary = agent_review_retrieval_mod.summarize_agent_reviews(package_path)
    except agent_review_retrieval_mod.AgentReviewRetrievalError as exc:
        _fail(str(exc))
        return

    console.print(f"review_count: {summary['review_count']}")
    console.print("status_counts:")
    for status, count in sorted(summary["status_counts"].items()):
        console.print(f"  {safe_console_text(status)}: {count}")
    console.print(f"escalation_count: {summary['escalation_count']}")
    console.print(f"unsupported_claim_count: {summary['unsupported_claim_count']}")
    console.print(f"missing_evidence_count: {summary['missing_evidence_count']}")


@agent_review_app.command(name="get")
def agent_review_get(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    review_id: str = typer.Argument(..., help="The agent review record id to retrieve."),
) -> None:
    """Look up one agent review record by id.

    Read-only. Prints a clean "no result" message (exit 0) if the id
    does not exist.
    """
    try:
        event = agent_review_retrieval_mod.get_agent_review_by_id(package_path, review_id)
    except agent_review_retrieval_mod.AgentReviewRetrievalError as exc:
        _fail(str(exc))
        return

    if event is None:
        console.print(f"[yellow]No agent review found with id '{safe_console_text(review_id)}'.[/yellow]")
        return

    _print_agent_review_record(event)


@agent_review_app.command(name="query-bundle")
def agent_review_query_bundle(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    bundle_id: str = typer.Argument(..., help="The evidence bundle id to look up linked reviews for."),
) -> None:
    """List already-written agent review records linked to one evidence bundle.

    Read-only. An empty result (no review links to this bundle id, or
    no agent review track) is printed cleanly (exit 0), not an error.
    """
    try:
        events = agent_review_retrieval_mod.query_agent_reviews_by_bundle(package_path, bundle_id)
    except agent_review_retrieval_mod.AgentReviewRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print(
            f"[yellow]No agent review records are linked to evidence bundle "
            f"'{safe_console_text(bundle_id)}'.[/yellow]"
        )
        return

    _print_agent_review_events_table(events, title="Agent review records (linked to evidence bundle)")


@agent_review_app.command(name="query-time")
def agent_review_query_time(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    start_ms: int = typer.Option(..., "--start-ms", help="Query time range start, in milliseconds."),
    end_ms: int = typer.Option(..., "--end-ms", help="Query time range end, in milliseconds."),
) -> None:
    """List agent review records overlapping `[start_ms, end_ms]`.

    Read-only. An empty result (no overlap, or no agent review track)
    is printed cleanly (exit 0), not an error.
    """
    try:
        events = agent_review_retrieval_mod.query_agent_reviews_by_time_range(package_path, start_ms, end_ms)
    except agent_review_retrieval_mod.AgentReviewRetrievalError as exc:
        _fail(str(exc))
        return

    if not events:
        console.print("[yellow]No agent review records overlap this time range.[/yellow]")
        return

    _print_agent_review_events_table(events, title="Agent review records (time overlap)")


@app.command()
def reindex(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force_stale_lock: bool = typer.Option(
        False,
        "--force-stale-lock",
        help="Clear a stale operation lock (pid gone or lock older than the safe threshold) before proceeding.",
    ),
) -> None:
    """Rebuild index/search.sqlite from the canonical tracks/*.jsonl files.

    Never modifies manifest.json, tracks/*.jsonl, or sources/ — only the
    derived, disposable search index.
    """
    try:
        with operation_lock(package_path, operation="reindex", force_stale=force_stale_lock):
            result = reindex_package(package_path)
    except OperationLockError as exc:
        _fail(str(exc))
        return
    except ReindexError as exc:
        _fail(str(exc))
        return

    if result.warnings:
        console.print(
            f"[yellow]{len(result.warnings)} warning(s) — malformed line(s) skipped:[/yellow]"
        )
        for idx, warning in enumerate(result.warnings, start=1):
            console.print(f"  {idx}. {safe_console_text(warning)}")

    console.print(f"[bold green]Reindex complete[/bold green] -> {safe_console_text(str(result.sqlite_path))}")
    console.print(f"  records indexed: {result.indexed_count}")
    for name, count in result.tracks_indexed.items():
        console.print(f"  track: {safe_console_text(name)} ({count} records)")


@app.command()
def lock(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing integrity lock. Without this, `lock` refuses if the package is already locked.",
    ),
    include_index: bool = typer.Option(
        False,
        "--include-index/--exclude-index",
        help="Include index/search.sqlite in the lock (excluded by default: it is derived/rebuildable).",
    ),
    strict_extra_files: bool = typer.Option(
        False,
        "--strict-extra-files/--no-strict-extra-files",
        help="Record the default --strict policy for later `verify-lock` calls (does not itself scan for extras).",
    ),
    chmod_readonly: bool = typer.Option(
        False,
        "--chmod-readonly",
        help="Best-effort: strip write permission bits from locked files. NOT a security boundary.",
    ),
    force_stale_lock: bool = typer.Option(
        False,
        "--force-stale-lock",
        help="Clear a stale operation lock (pid gone or lock older than the safe threshold) before proceeding.",
    ),
) -> None:
    """Create lock/package.lock.json + lock/package.lock.sha256 for a package.

    Records a SHA-256 digest of every canonical file (manifest.json,
    source media, tracks/*.jsonl, receipts/*.jsonl) so a later
    `verify-lock` can detect whether any of them changed.
    """
    try:
        with operation_lock(package_path, operation="lock", force_stale=force_stale_lock):
            result = lock_mod.create_lock(
                package_path,
                force=force,
                include_index=include_index,
                strict_extra_files_default=strict_extra_files,
                chmod_readonly=chmod_readonly,
            )
    except OperationLockError as exc:
        _fail(str(exc))
        return
    except lock_mod.LockError as exc:
        _fail(str(exc))
        return

    console.print(f"[bold green]Lock created[/bold green] -> {safe_console_text(str(result.lock_json_path))}")
    console.print(f"  files locked: {result.file_count}")
    if result.chmod_readonly_applied:
        console.print(f"  chmod --readonly applied to {len(result.chmod_readonly_applied)} file(s)")


@app.command(name="verify-lock")
def verify_lock_cmd(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
    strict: bool = typer.Option(
        False, "--strict", help="Also fail if untracked files exist alongside the locked ones."
    ),
) -> None:
    """Verify a package's canonical files still match lock/package.lock.json.

    Read-only. Exits 0 if valid, 1 otherwise.
    """
    try:
        report = lock_mod.verify_lock(package_path, strict=strict)
    except lock_mod.LockError as exc:
        _fail(str(exc))
        return

    console.print(f"Verifying [bold]{safe_console_text(str(report.package_path))}[/bold]")
    console.print(f"  checked: {len(report.checked)}")
    if report.changed:
        console.print(f"[bold red]changed ({len(report.changed)}):[/bold red]")
        for path in report.changed:
            console.print(f"  {safe_console_text(path)}")
    if report.missing:
        console.print(f"[bold red]missing ({len(report.missing)}):[/bold red]")
        for path in report.missing:
            console.print(f"  {safe_console_text(path)}")
    if report.extra:
        console.print(f"[yellow]extra ({len(report.extra)}):[/yellow]")
        for path in report.extra:
            console.print(f"  {safe_console_text(path)}")
    if report.errors:
        console.print(f"[bold red]errors ({len(report.errors)}):[/bold red]")
        for error in report.errors:
            console.print(f"  {safe_console_text(error)}")
    if not report.lock_hash_valid:
        console.print("[bold red]lock.sha256 does not match lock.json[/bold red]")

    if report.valid:
        console.print("[bold green]VALID[/bold green] — package matches its lock")
        return

    console.print("[bold red]INVALID[/bold red] — package does not match its lock")
    raise typer.Exit(code=1)


@app.command(name="lock-status")
def lock_status_cmd(
    package_path: Path = typer.Argument(..., help="Path to a .clulatent package folder."),
) -> None:
    """Print whether a package is unlocked, locked, lock-invalid, or lock-partial.

    Read-only.
    """
    try:
        status, report = lock_mod.lock_status(package_path)
    except lock_mod.LockError as exc:
        _fail(str(exc))
        return

    color = {
        "unlocked": "yellow",
        "locked": "bold green",
        "lock-invalid": "bold red",
        "lock-partial": "bold red",
    }[status]
    console.print(f"[{color}]{status}[/{color}]")
    if report is not None and not report.valid:
        for path in report.changed:
            console.print(f"  changed: {safe_console_text(path)}")
        for path in report.missing:
            console.print(f"  missing: {safe_console_text(path)}")
        for error in report.errors:
            console.print(f"  error: {safe_console_text(error)}")


if __name__ == "__main__":
    app()
