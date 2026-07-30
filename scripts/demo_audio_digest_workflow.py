#!/usr/bin/env python3
"""Phase 3.10: a small, reproducible demo of the audio digest evidence
stack, end-to-end, using entirely synthetic (hand-authored) audio
digest records.

Runs the full Phase 3.4-3.9 audio digest path end-to-end inside one
output directory the caller chooses:

  1. Create (ingest) a tiny demo `.clulatent` package.
  2. Write a synthetic audio digest JSONL file: one
     `audio_feature_series` record, one `audio_digest_segment` record,
     and one `audio_llm_context_packet` record, referencing a
     package-relative `data_path` -- no dense array is ever inlined.
  3. Validate those records (Phase 3.4 schema validation) -- writes
     nothing.
  4. Append the validated records into the package (Phase 3.5 writer):
     writes `tracks/audio_digest_events.jsonl`, a manifest track
     descriptor, and `receipts/audio_digest.jsonl`.
  5. Validate the whole package (Phase 3.7 package-level validation).
  6. Retrieve the evidence back out (Phase 3.8 retrieval primitives):
     by id, by time range, by type, by linked evidence id, and by
     salience.
  7. Retrieve a small, bounded LLM-context selection (Phase 3.8
     `select_audio_digest_llm_context`).
  8. Print a shallow retrieval summary and confirm the receipt and
     manifest updates from step 4 are still on disk.

This script adds no new adapter, no new validation rule, no new
writer, no new retrieval function, no ML dependency, no dense audio
extraction, and no automatically generated LLM summary -- every
synthetic record's `label`/`summary`/`caveats` text is hand-written,
hedged evidence language, fixed in `_build_synthetic_audio_digest_
events()` below. This script runs no librosa, Essentia, aubio, Basic
Pitch, Demucs, YAMNet, PANNs, or OpenL3 code, and adds no such
dependency. ffmpeg is used only to generate the tiny base video this
demo's package is built from (the same role it plays in every other
demo/fixture in this codebase) -- never to derive an audio digest
record.

Core rule (Phase 3.3, restated because it governs every retrieval step
below): **store deep, show shallow, retrieve detail only when
needed.**

Usage:

    python scripts/demo_audio_digest_workflow.py [--output-dir DIR]

With no arguments, everything runs inside a fresh
`clulatent_audio_digest_demo` subdirectory of the system temp
directory (or `--output-dir` if given). Nothing outside the chosen
output directory is created, modified, or deleted; no network access
is performed; no committed repo file or fixture is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    import clu_latent  # noqa: F401
except ImportError:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from clu_latent import audio_digest as audio_digest_mod
from clu_latent import audio_digest_retrieval as audio_digest_retrieval_mod
from clu_latent import audio_digest_writer as audio_digest_writer_mod
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.manifest import Manifest
from clu_latent.security.console import safe_console_text
from clu_latent.security.limits import DEFAULT_LIMITS
from clu_latent.security.subprocess import ToolNotFoundError, resolve_tool, run_tool
from clu_latent.validate import validate_package

from rich.console import Console
from rich.table import Table

TOOL_NAME = "demo-audio-digest-workflow"
TOOL_VERSION = "0.0.1"

_PRODUCER = {"name": TOOL_NAME, "version": TOOL_VERSION}

_FEATURE_SERIES_ID = "demo_series_loudness_rms"
_DIGEST_SEGMENT_ID = "demo_segment_buildup_candidate"
_CONTEXT_PACKET_ID = "demo_packet_context"


class DemoError(RuntimeError):
    """Raised for any demo failure that should be shown to the user cleanly.

    Every failure path in this script -- missing ffmpeg, an
    unwritable output directory, a failed ingest/validate/append/
    retrieval step -- raises this, never a raw traceback.
    """


def _generate_demo_media(directory: Path) -> Path:
    """Generate a tiny, deterministic, local-only demo video with tone.

    Uses the same hardened `security.subprocess.resolve_tool`/
    `run_tool` pattern every other ffmpeg invocation in this codebase
    uses (no `shell=True`, bounded capture, hard timeout). This
    produces the *base* `.clulatent` package this demo appends
    synthetic audio digest evidence into -- it is not itself an audio
    adapter, and no audio digest field anywhere in this script is ever
    derived from this media's actual waveform.
    """
    try:
        executable = resolve_tool("ffmpeg")
    except ToolNotFoundError as exc:
        raise DemoError(str(exc)) from exc

    video_path = directory / "demo_media.mp4"
    args = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=64x64:rate=5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video_path),
    ]
    result = run_tool(executable, args, timeout_s=DEFAULT_LIMITS.ffmpeg_timeout_s)
    if not result.success:
        raise DemoError(f"failed to generate demo media: {result.stderr_tail.strip()}")
    return video_path


def _build_synthetic_audio_digest_events() -> list[dict]:
    """Return three hand-authored, conservative synthetic audio digest records.

    Every label/summary/caveat below is fixed, hedged evidence
    language written for this demo -- never generated, never inferred
    from any real audio signal. The feature series references a
    package-relative `data_path`; no dense array is inlined anywhere
    in any of the three records.
    """
    feature_series = {
        "id": _FEATURE_SERIES_ID,
        "type": "audio_feature_series",
        "t_start_ms": 0,
        "t_end_ms": 2000,
        "producer": dict(_PRODUCER),
        "confidence": 0.9,
        "payload": {
            "feature": "loudness_rms",
            "window_ms": 20,
            "hop_ms": 20,
            "units": "dbfs",
            "data_path": "media/audio_features/demo_loudness_rms.jsonl",
            "summary": {"min": -40.0, "max": -6.0, "mean": -18.5, "count": 100},
        },
    }
    digest_segment = {
        "id": _DIGEST_SEGMENT_ID,
        "type": "audio_digest_segment",
        "t_start_ms": 500,
        "t_end_ms": 1500,
        "producer": dict(_PRODUCER),
        "confidence": 0.8,
        "payload": {
            "label": "tension-like buildup candidate",
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "linked_event_ids": ["demo_energy_evidence_000001"],
            "linked_feature_series_ids": [_FEATURE_SERIES_ID],
            "salience": 0.75,
            "recommended_for_llm_context": True,
            "caveats": ["This is evidence, not truth; it does not establish intent."],
        },
    }
    context_packet = {
        "id": _CONTEXT_PACKET_ID,
        "type": "audio_llm_context_packet",
        "t_start_ms": 0,
        "t_end_ms": 2000,
        "producer": dict(_PRODUCER),
        "confidence": 0.75,
        "payload": {
            "time_range": {"t_start_ms": 0, "t_end_ms": 2000},
            "budget_tokens_estimate": 300,
            "summary": "Linked evidence suggests a tension-like buildup candidate.",
            "top_evidence": [
                f"tension-like buildup candidate in segment {_DIGEST_SEGMENT_ID}"
            ],
            "warnings": ["Some lower-salience detail was omitted to stay within budget."],
            "caveats": [
                "This packet describes evidence, not truth, and does not establish "
                "intent, meaning, or a listener's emotional response."
            ],
            "linked_event_ids": ["demo_energy_evidence_000001"],
            "linked_digest_segment_ids": [_DIGEST_SEGMENT_ID],
            "linked_feature_series_ids": [_FEATURE_SERIES_ID],
            "omitted_detail_reason": "Lower-salience detail omitted to stay within budget.",
            "retrieval_hints": {
                "by_time_range": "retrieve tracks/audio_digest_events.jsonl records overlapping the range",
                "by_evidence_id": "retrieve any linked id by exact match",
            },
        },
    }
    return [feature_series, digest_segment, context_packet]


def run_demo(*, output_dir: Path, console: Console) -> int:
    """Run the full demo workflow. Returns a process exit code (0 or 1).

    Every path this function touches is confined to `output_dir` -- it
    never writes outside `output_dir`, never mutates a repo file or
    committed fixture, and never touches an unrelated package.
    """
    if output_dir.exists() and output_dir.is_file():
        raise DemoError(f"output-dir path is a file, not a directory: {output_dir}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DemoError(f"could not create output-dir {output_dir}: {exc}") from exc

    # --- Step 1/8: a valid base package -----------------------------------
    console.print(f"[bold]Step 1/8[/bold]: preparing demo media and package in {safe_console_text(str(output_dir))}")
    media_path = _generate_demo_media(output_dir)
    console.print(f"  media: {safe_console_text(str(media_path))}")

    package_path = output_dir / "demo.clulatent"
    try:
        ingest_result = ingest_video(media_path, package_path)
    except IngestError as exc:
        raise DemoError(f"failed to create demo package: {exc}") from exc
    console.print(f"  package: {safe_console_text(str(ingest_result.package_path))}")

    # --- Step 2/8: synthetic audio digest JSONL -----------------------------
    console.print("[bold]Step 2/8[/bold]: writing synthetic audio digest records (JSONL)")
    events = _build_synthetic_audio_digest_events()
    events_file = output_dir / "synthetic_audio_digest_events.jsonl"
    events_file.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    console.print(f"  events file: {safe_console_text(str(events_file))}")
    console.print(f"  record count: {len(events)}")

    # --- Step 3/8: validate the records (Phase 3.4), writes nothing --------
    console.print("[bold]Step 3/8[/bold]: validating synthetic records (writes nothing)")
    errors, warnings = audio_digest_mod.validate_audio_digest_track(
        events, package_root=package_path
    )
    if errors:
        console.print("[bold red]FAIL[/bold red] -- synthetic records failed validation:")
        for idx, error in enumerate(errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        return 1
    console.print("  [bold green]PASS[/bold green] -- all synthetic records are shape-valid")
    if warnings:
        for warning in warnings:
            console.print(f"  [yellow]warning:[/yellow] {safe_console_text(warning)}")

    # --- Step 4/8: append into the package (Phase 3.5 writer) --------------
    console.print("[bold]Step 4/8[/bold]: appending records into the package")
    try:
        write_result = audio_digest_writer_mod.append_audio_digest_events(
            package_path,
            events,
            tool_name=TOOL_NAME,
            tool_version=TOOL_VERSION,
            write_receipt=True,
        )
    except audio_digest_writer_mod.AudioDigestWriteError as exc:
        raise DemoError(f"failed to append synthetic records: {exc}") from exc

    records_table = Table(title="Audio digest records written")
    records_table.add_column("ID")
    records_table.add_column("Type")
    for event in events:
        records_table.add_row(safe_console_text(event["id"]), safe_console_text(event["type"]))
    console.print(records_table)
    console.print(f"  events written: {write_result.events_written}")
    if write_result.receipt_path is not None:
        console.print(f"  receipt: {safe_console_text(str(write_result.receipt_path))}")

    # --- Step 5/8: validate the whole package (Phase 3.7) -------------------
    console.print("[bold]Step 5/8[/bold]: validating the package")
    validation_report = validate_package(package_path)
    if not validation_report.valid:
        console.print("[bold red]FAIL[/bold red] -- package validation reported error(s):")
        for idx, error in enumerate(validation_report.errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        return 1
    console.print("  [bold green]PASS[/bold green] -- package is valid")

    # --- Step 6/8: retrieve by id / time / type / link / salience ----------
    console.print("[bold]Step 6/8[/bold]: retrieving evidence (Phase 3.8 primitives)")

    by_id = audio_digest_retrieval_mod.get_audio_digest_event_by_id(
        package_path, _DIGEST_SEGMENT_ID
    )
    console.print(f"  get by id: {'PASS' if by_id is not None else 'FAIL'} ({safe_console_text(_DIGEST_SEGMENT_ID)})")

    by_time = audio_digest_retrieval_mod.query_audio_digest_by_time_range(package_path, 0, 2000)
    console.print(f"  query by time range: {len(by_time)} record(s) overlap [0, 2000] ms")

    by_type = audio_digest_retrieval_mod.query_audio_digest_by_type(
        package_path, "audio_digest_segment"
    )
    console.print(f"  query by type: {len(by_type)} audio_digest_segment record(s)")

    by_link = audio_digest_retrieval_mod.query_audio_digest_by_linked_evidence_id(
        package_path, _FEATURE_SERIES_ID
    )
    console.print(f"  query by linked evidence id: {len(by_link)} record(s) linked to {safe_console_text(_FEATURE_SERIES_ID)}")

    by_salience = audio_digest_retrieval_mod.query_audio_digest_by_salience(
        package_path, min_salience=0.5
    )
    console.print(f"  query by salience (>= 0.5): {len(by_salience)} record(s)")

    if by_id is None or not by_time or not by_type or not by_link or not by_salience:
        console.print("[bold red]FAIL[/bold red] -- one or more retrieval queries returned no results")
        return 1
    console.print("  [bold green]PASS[/bold green] -- all retrieval queries returned the expected evidence")

    # --- Step 7/8: bounded LLM context selection ----------------------------
    console.print("[bold]Step 7/8[/bold]: retrieving bounded LLM context (Phase 3.8)")
    llm_context = audio_digest_retrieval_mod.select_audio_digest_llm_context(
        package_path, max_events=5
    )
    context_types = sorted({event["type"] for event in llm_context})
    console.print(f"  context record(s): {len(llm_context)} ({', '.join(context_types) or 'none'})")
    if "audio_feature_series" in context_types:
        console.print("[bold red]FAIL[/bold red] -- LLM context must never include raw audio_feature_series records")
        return 1
    console.print("  [bold green]PASS[/bold green] -- LLM context is bounded and excludes dense feature series data")

    # --- Step 8/8: confirm receipts/manifest, print shallow summary --------
    console.print("[bold]Step 8/8[/bold]: confirming receipts and manifest updates")
    manifest = Manifest.from_json_file(package_path / "manifest.json")
    descriptor = next(
        (t for t in manifest.tracks if t.name == audio_digest_writer_mod.AUDIO_DIGEST_TRACK_NAME),
        None,
    )
    if descriptor is None:
        console.print("[bold red]FAIL[/bold red] -- manifest is missing the audio digest track descriptor")
        return 1
    console.print(f"  manifest track descriptor: record_count={descriptor.record_count}")

    receipt_path = package_path / "receipts" / "audio_digest.jsonl"
    if not receipt_path.is_file() or receipt_path.stat().st_size == 0:
        console.print("[bold red]FAIL[/bold red] -- receipts/audio_digest.jsonl is missing or empty")
        return 1
    console.print(f"  receipt file: {safe_console_text(str(receipt_path))}")

    all_events = audio_digest_retrieval_mod.load_audio_digest_events(package_path)
    summary = audio_digest_retrieval_mod.summarize_audio_digest_retrieval_result(all_events)
    console.print(f"  retrieval-summary: event_count={summary['event_count']}, record_type_counts={summary['record_type_counts']}")
    console.print("  [bold green]PASS[/bold green] -- receipt and manifest updates confirmed on disk")

    console.print()
    console.print("[bold]Summary[/bold]")
    console.print(f"  package: {safe_console_text(str(package_path))}")
    console.print(f"  synthetic records: {len(events)}")
    console.print(f"  validate records: PASS")
    console.print(f"  append: PASS ({write_result.events_written} event(s) written)")
    console.print(f"  validate package: PASS")
    console.print(f"  retrieval queries: PASS")
    console.print(f"  llm context: PASS ({len(llm_context)} bounded record(s))")
    console.print(f"  receipt + manifest: PASS")
    console.print(
        "[dim]Reminder: this is entirely synthetic evidence, not confirmed truth, and "
        "not derived from any real audio signal -- appending it made it canonical "
        "(validated, bounded, receipted, reviewable, lockable), never confirmed "
        "true, and CLULatent does not claim to understand audio.[/dim]"
    )

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_audio_digest_workflow.py",
        description=(
            "Run the audio digest evidence workflow (Phase 3.4-3.9) end-to-end "
            "using entirely synthetic, hand-authored audio digest records: "
            "create a demo package, write synthetic audio digest JSONL, validate "
            "it, append it, validate the package, retrieve it back out by id/"
            "time/type/link/salience, select bounded LLM context, and confirm "
            "the receipt and manifest updates. Produces synthetic evidence, "
            "never confirmed truth, and never analyzes real audio."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to create/reuse for all demo output (default: a "
            "'clulatent_audio_digest_demo' directory under the system temp "
            "directory). Nothing is ever written outside this directory."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir()) / "clulatent_audio_digest_demo"

    console = Console()
    try:
        return run_demo(output_dir=output_dir, console=console)
    except DemoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
