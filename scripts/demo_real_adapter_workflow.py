#!/usr/bin/env python3
"""Phase 2.18: a small, reproducible demo of the real-adapter workflow.

Runs the full Phase 2.10-2.17 real-adapter path end-to-end against a
tiny, synthetically generated video, entirely inside one output
directory the caller chooses:

  1. Create (ingest) a tiny demo `.clulatent` package.
  2. Generate a real ffmpeg `scdet` visual-change adapter result
     (Phase 2.15).
  3. Dry-run that adapter result against the package (Phase 2.11) --
     writes nothing.
  4. Import the adapter result into the package (Phase 2.12).
  5. Validate the resulting package (Phase 1/2.9).
  6. Print a safe, human-readable summary of what happened.

This script adds no new adapter architecture, no new real adapter, no
ML model dependency, no semantic truth generation, no dynamic plugin
loading, no adapter discovery, no Studio UI, and no CLUBIN. Every step
above calls an existing, unmodified library function
(`ingest.ingest_video`, `analysis_ffmpeg_visual_change_adapter.
build_visual_change_adapter_result`, `analysis_adapter_dry_run.
dry_run_adapter_result`, `analysis_adapters.write_adapter_result`,
`validate.validate_package`) -- it duplicates none of their logic.

Core principle (unchanged): adapters produce **evidence**, not truth.

- Detected does not mean trusted.
- Generated does not mean canonical.
- Canonical means validated, bounded, receipted, reviewable, and
  lockable.

Usage:

    python scripts/demo_real_adapter_workflow.py [--output-dir DIR]
        [--media PATH] [--threshold N] [--print-json]

With no arguments, everything runs inside a fresh `clulatent_demo`
subdirectory of the system temp directory (or `--output-dir` if
given), using a tiny synthetic ffmpeg-generated video (or `--media` if
given). Nothing outside the chosen output directory is created,
modified, or deleted; no network access is performed; no committed
repo file or fixture is touched.
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

from clu_latent.analysis_adapter_dry_run import dry_run_adapter_result
from clu_latent.analysis_adapters import AnalysisAdapterError, write_adapter_result
from clu_latent.analysis_ffmpeg_visual_change_adapter import (
    ADAPTER_NAME,
    DEFAULT_THRESHOLD,
    VisualChangeAdapterError,
    build_visual_change_adapter_result,
    visual_change_adapter_result_to_dict,
)
from clu_latent.ingest import IngestError, ingest_video
from clu_latent.security.console import safe_console_text
from clu_latent.security.limits import DEFAULT_LIMITS
from clu_latent.security.subprocess import ToolNotFoundError, resolve_tool, run_tool
from clu_latent.validate import validate_package

from rich.console import Console
from rich.table import Table


class DemoError(RuntimeError):
    """Raised for any demo failure that should be shown to the user cleanly.

    Every failure path in this script -- missing ffmpeg, an
    unwritable output directory, a failed ingest/dry-run/import/
    validate step -- raises this, never a raw traceback.
    """


def _generate_demo_media(directory: Path) -> Path:
    """Generate a tiny, deterministic, local-only scene-change video.

    Uses the same `security.subprocess.resolve_tool`/`run_tool`
    hardened pattern every other ffmpeg invocation in this codebase
    uses (no `shell=True`, bounded capture, hard timeout) -- never a
    raw `subprocess` call. Two one-second solid-color clips
    (red, then blue) concatenated together give ffmpeg's `scdet`
    filter one unambiguous, deterministic scene change to detect,
    without downloading or embedding any media file.
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
        "color=c=red:size=64x64:duration=1:rate=5",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:size=64x64:duration=1:rate=5",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-pix_fmt",
        "yuv420p",
        str(video_path),
    ]
    result = run_tool(executable, args, timeout_s=DEFAULT_LIMITS.ffmpeg_timeout_s)
    if not result.success:
        raise DemoError(f"failed to generate demo media: {result.stderr_tail.strip()}")
    return video_path


def run_demo(
    *,
    output_dir: Path,
    media_path: Path | None,
    threshold: float,
    print_json: bool,
    console: Console,
) -> int:
    """Run the full demo workflow. Returns a process exit code (0 or 1).

    Every path this function touches is confined to `output_dir`
    (plus reading `media_path` if given, which may live anywhere) --
    it never writes outside `output_dir`, never mutates a repo file or
    committed fixture, and never touches an unrelated package.
    """
    if output_dir.exists() and output_dir.is_file():
        raise DemoError(f"output-dir path is a file, not a directory: {output_dir}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DemoError(f"could not create output-dir {output_dir}: {exc}") from exc

    console.print(f"[bold]Step 1/5[/bold]: preparing demo media and package in {safe_console_text(str(output_dir))}")

    owns_media = media_path is None
    if owns_media:
        media_path = _generate_demo_media(output_dir)
    else:
        media_path = Path(media_path)
        if not media_path.exists() or not media_path.is_file():
            raise DemoError(f"--media path does not exist or is not a file: {media_path}")
    console.print(f"  media: {safe_console_text(str(media_path))}")

    package_path = output_dir / "demo.clulatent"
    try:
        ingest_result = ingest_video(media_path, package_path)
    except IngestError as exc:
        raise DemoError(f"failed to create demo package: {exc}") from exc
    console.print(f"  package: {safe_console_text(str(ingest_result.package_path))}")

    console.print("[bold]Step 2/5[/bold]: generating real ffmpeg visual-change adapter result")
    try:
        adapter_result = build_visual_change_adapter_result(media_path, threshold=threshold)
    except VisualChangeAdapterError as exc:
        raise DemoError(f"failed to generate adapter result: {exc}") from exc

    result_dict = visual_change_adapter_result_to_dict(adapter_result)
    result_path = output_dir / "visual_change_result.json"
    result_path.write_text(json.dumps(result_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    console.print(f"  adapter_name: {safe_console_text(ADAPTER_NAME)}")
    console.print(f"  adapter result: {safe_console_text(str(result_path))}")

    console.print("[bold]Step 3/5[/bold]: dry-running the adapter result (writes nothing)")
    dry_run_report = dry_run_adapter_result(adapter_result, package_path=package_path)
    if dry_run_report.errors:
        console.print("[bold red]FAIL[/bold red] -- dry run reported error(s):")
        for idx, error in enumerate(dry_run_report.errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        return 1
    console.print("  [bold green]PASS[/bold green] -- dry run reports this result is eligible to import")

    console.print("[bold]Step 4/5[/bold]: importing the adapter result into the package")
    try:
        write_results = write_adapter_result(package_path, adapter_result)
    except AnalysisAdapterError as exc:
        raise DemoError(f"failed to import adapter result: {exc}") from exc

    lanes_table = Table(title="Lanes written")
    lanes_table.add_column("Lane")
    lanes_table.add_column("Events", justify="right")
    total_events = 0
    receipt_path = None
    for write_result in write_results:
        lanes_table.add_row(safe_console_text(write_result.lane), str(write_result.events_written))
        total_events += write_result.events_written
        if write_result.receipt_path is not None:
            receipt_path = write_result.receipt_path
    console.print(lanes_table)
    console.print(f"  total events written: {total_events}")
    if receipt_path is not None:
        console.print(f"  analyze receipt: {safe_console_text(str(receipt_path))}")

    console.print("[bold]Step 5/5[/bold]: validating the package")
    validation_report = validate_package(package_path)
    if not validation_report.valid:
        console.print("[bold red]FAIL[/bold red] -- package validation reported error(s):")
        for idx, error in enumerate(validation_report.errors, start=1):
            console.print(f"  {idx}. {safe_console_text(error)}")
        return 1
    console.print("  [bold green]PASS[/bold green] -- package is valid")

    console.print()
    console.print("[bold]Summary[/bold]")
    console.print(f"  package: {safe_console_text(str(package_path))}")
    console.print(f"  adapter result: {safe_console_text(str(result_path))}")
    console.print(f"  dry-run: PASS")
    console.print(f"  import: PASS ({total_events} event(s) written)")
    console.print(f"  validate: PASS")
    console.print(
        "[dim]Reminder: this is candidate evidence, not confirmed truth -- importing "
        "made it canonical (validated, bounded, receipted, reviewable, lockable), "
        "never confirmed true.[/dim]"
    )

    if print_json:
        console.print()
        console.print("[bold]Generated adapter result JSON[/bold]")
        console.print(json.dumps(result_dict, indent=2, sort_keys=True), markup=False, soft_wrap=True)

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_real_adapter_workflow.py",
        description=(
            "Run the polished real-adapter workflow (Phase 2.10-2.17) end-to-end: "
            "create a demo package, generate a real ffmpeg visual-change adapter "
            "result, dry-run it, import it, and validate the package. Produces "
            "candidate evidence, never confirmed truth."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory to create/reuse for all demo output (default: a "
            "'clulatent_demo' directory under the system temp directory). "
            "Nothing is ever written outside this directory."
        ),
    )
    parser.add_argument(
        "--media",
        type=Path,
        default=None,
        help=(
            "Path to a real media file to use instead of generating a tiny "
            "synthetic demo video."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"ffmpeg scdet scene-change threshold, 0-100 (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Also print the full generated adapter-result JSON document.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir()) / "clulatent_demo"

    console = Console()
    try:
        return run_demo(
            output_dir=output_dir,
            media_path=args.media,
            threshold=args.threshold,
            print_json=args.print_json,
            console=console,
        )
    except DemoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
