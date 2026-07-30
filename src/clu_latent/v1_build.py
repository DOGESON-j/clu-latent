"""Phase 3.19: the V1 build pipeline — one command, one video, one package.

Every earlier phase produced a *part* of a `.clulatent` package: ingest,
validate, the visual-change lane, the changed-region lane, evidence
bundles, and agent review. Using them meant running six or seven
commands in the right order and knowing which lane feeds which. This
module orchestrates exactly those existing writers, in that fixed order,
behind a single deterministic entry point (`build_v1_package`) so a user
can go from a raw video to a validated, evidence-carrying package in one
step.

This module implements **no** perception of its own. It never runs
FFmpeg, Pillow, an ML model, an LLM, or a network call directly — it only
calls writers that already exist, each of which carries its own bounds,
locks, and receipts. It never decides what any evidence *means*: it does
not identify objects, people, faces, text, actions, speech, scenes, or
intent. It only sequences already-bounded steps and reports what each one
did.

The pipeline (see docs/PHASE_3_19_V1_BUILD_PIPELINE_AGENT_CONTEXT_V0.md):

  1. ingest the video into a package                (`ingest_video`)
  2. validate the ingested package                  (`validate_package`)
  3. visual-change analysis, if Pillow is available  (`analyze_visual_change`)
  4. changed-region analysis, if step 3 produced a track
                                                     (`analyze_changed_regions`)
  5. one evidence bundle over the full package window
                                                     (`build_evidence_bundle`)
  6. one agent review of that bundle                 (`run_agent_review`)
  7. validate again

Pillow policy: under the default profile the visual lanes are *promised*.
If Pillow is absent, `build_v1_package` fails clearly (it does not
silently produce a weaker package) unless `allow_partial=True`, which
skips the visual lanes, continues, and marks the result `partial=True`
with the skipped lanes named.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import changed_region as changed_region_mod
from . import visual_change as visual_change_mod
from .agent_review_writer import AgentReviewWriteError, run_agent_review
from .changed_region_writer import ChangedRegionWriteError, analyze_changed_regions
from .constants import TOOL_NAME, TOOL_VERSION
from .evidence_bundle_writer import EvidenceBundleWriteError, build_evidence_bundle
from .ingest import IngestError, ingest_video
from .security.limits import DEFAULT_LIMITS, Limits
from .v1_package_index import (
    V1PackageIndexError,
    write_v1_package_index,
)
from .validate import validate_package
from .visual_change_writer import VisualChangeWriteError, analyze_visual_change

__all__ = [
    "V1BuildError",
    "V1_PROFILE",
    "StepResult",
    "ValidationSnapshot",
    "V1BuildResult",
    "build_v1_package",
]

# The one profile this phase ships. Named (not a bare bool) so a future
# profile can be added without changing the CLI's option shape.
V1_PROFILE = "v1"

_VISUAL_LANES = ("visual_change", "changed_regions")

_PILLOW_UNAVAILABLE_MESSAGE = (
    "profile 'v1' includes the visual-change and changed-region lanes, "
    "which require the optional 'visual' extra (Pillow), and Pillow is not "
    "installed. Install it (e.g. `pip install 'clu-latent[visual]'`) and "
    "retry, or pass allow_partial=True / --allow-partial to build a partial "
    "package that skips the visual lanes."
)


class V1BuildError(RuntimeError):
    """Raised when the V1 build pipeline cannot complete a required step."""


@dataclass
class StepResult:
    """One pipeline step's outcome, for the build summary.

    `status` is one of `"ok"`, `"skipped"`, or `"failed"`. `detail` is a
    small, non-semantic dict of what the step produced (ids, counts) —
    never an interpretation of the evidence.
    """

    name: str
    status: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationSnapshot:
    valid: bool
    error_count: int
    warning_count: int

    @classmethod
    def from_report(cls, report: Any) -> "ValidationSnapshot":
        return cls(
            valid=bool(report.valid),
            error_count=len(report.errors),
            warning_count=len(report.warnings),
        )


@dataclass
class V1BuildResult:
    """Structured outcome of a V1 build, ready for a concise CLI summary.

    Carries only structural facts — paths, ids, counts, validation
    status — never any claim about what the video contains.
    """

    package_path: Path
    profile: str
    partial: bool
    duration_ms: int
    track_counts: dict[str, int]
    receipts: list[str]
    validation_before: ValidationSnapshot
    validation_after: ValidationSnapshot
    visual_change_events: int
    changed_region_events: int
    bundle_ids: list[str]
    review_ids: list[str]
    skipped_lanes: list[str]
    index_written: bool
    index_artifacts: list[str]
    steps: list[StepResult]


def _track_counts(manifest: Any) -> dict[str, int]:
    return {track.name: track.record_count for track in manifest.tracks}


def _receipt_files(package_path: Path) -> list[str]:
    receipts_dir = package_path / "receipts"
    if not receipts_dir.is_dir():
        return []
    return sorted(
        f"receipts/{entry.name}"
        for entry in receipts_dir.glob("*.jsonl")
        if entry.is_file() and not entry.is_symlink()
    )


def build_v1_package(
    input_video: Path | str,
    output_path: Path | str,
    *,
    profile: str = V1_PROFILE,
    force: bool = False,
    allow_partial: bool = False,
    force_stale_lock: bool = False,
    write_index: bool = True,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
    limits: Limits = DEFAULT_LIMITS,
) -> V1BuildResult:
    """Run the full deterministic V1 pipeline on `input_video`.

    Returns a `V1BuildResult` describing every step. Raises
    `V1BuildError` if a required step fails (ingest, either validation,
    the evidence bundle, or — under the default profile without
    `allow_partial` — a missing Pillow visual extra). All writes go
    through the existing writers, so integrity/operation locks are
    honored unchanged.
    """
    if profile != V1_PROFILE:
        raise V1BuildError(
            f"unknown profile {profile!r}; the only supported profile is {V1_PROFILE!r}"
        )

    output_path = Path(output_path)
    steps: list[StepResult] = []

    # --- 1. ingest ---------------------------------------------------------
    try:
        ingest_result = ingest_video(
            Path(input_video),
            output_path,
            force=force,
            force_stale_lock=force_stale_lock,
        )
    except IngestError as exc:
        raise V1BuildError(f"ingest failed: {exc}") from exc

    package_path = ingest_result.package_path
    duration_ms = ingest_result.manifest.source.duration_ms
    steps.append(
        StepResult(
            "ingest",
            "ok",
            {"package_path": str(package_path), "duration_ms": duration_ms},
        )
    )

    # --- 2. validate (before analysis) ------------------------------------
    report_before = validate_package(package_path, limits=limits)
    validation_before = ValidationSnapshot.from_report(report_before)
    steps.append(
        StepResult(
            "validate_before",
            "ok" if validation_before.valid else "failed",
            {
                "valid": validation_before.valid,
                "error_count": validation_before.error_count,
            },
        )
    )
    if not validation_before.valid:
        raise V1BuildError(
            "the freshly-ingested package did not validate: "
            f"{report_before.errors[:3]}"
        )

    # --- 3/4. visual lanes (optional, Pillow-gated) -----------------------
    pillow_available = visual_change_mod.is_available()
    partial = False
    skipped_lanes: list[str] = []
    visual_change_events = 0
    changed_region_events = 0

    if pillow_available:
        try:
            vc_result = analyze_visual_change(
                package_path,
                tool_name=tool_name,
                tool_version=tool_version,
                force=force,
                force_stale_lock=force_stale_lock,
                limits=limits,
            )
        except VisualChangeWriteError as exc:
            raise V1BuildError(f"visual-change analysis failed: {exc}") from exc
        visual_change_events = vc_result.events_written
        steps.append(
            StepResult(
                "visual_change",
                "ok",
                {
                    "events_written": vc_result.events_written,
                    "strength_counts": dict(vc_result.strength_counts),
                },
            )
        )

        if changed_region_mod.is_available():
            try:
                cr_result = analyze_changed_regions(
                    package_path,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    force=force,
                    force_stale_lock=force_stale_lock,
                    limits=limits,
                )
            except ChangedRegionWriteError as exc:
                raise V1BuildError(f"changed-region analysis failed: {exc}") from exc
            changed_region_events = cr_result.events_written
            steps.append(
                StepResult(
                    "changed_regions",
                    "ok",
                    {
                        "events_written": cr_result.events_written,
                        "strength_counts": dict(cr_result.strength_counts),
                    },
                )
            )
        else:
            skipped_lanes.append("changed_regions")
            steps.append(StepResult("changed_regions", "skipped", {"reason": "visual extra unavailable"}))
    else:
        # Default profile promises the visual lanes; fail clearly unless
        # the caller explicitly opted into a partial build.
        if not allow_partial:
            raise V1BuildError(_PILLOW_UNAVAILABLE_MESSAGE)
        partial = True
        skipped_lanes.extend(_VISUAL_LANES)
        for lane in _VISUAL_LANES:
            steps.append(StepResult(lane, "skipped", {"reason": "Pillow (visual extra) unavailable"}))

    # --- 5. evidence bundle over the full package window ------------------
    try:
        bundle_result = build_evidence_bundle(
            package_path,
            start_ms=0,
            end_ms=duration_ms,
            tool_name=tool_name,
            tool_version=tool_version,
            force=force,
            force_stale_lock=force_stale_lock,
            limits=limits,
        )
    except EvidenceBundleWriteError as exc:
        raise V1BuildError(f"evidence bundle build failed: {exc}") from exc
    bundle_ids = [bundle_result.bundle_id]
    steps.append(
        StepResult(
            "evidence_bundle",
            "ok",
            {
                "bundle_id": bundle_result.bundle_id,
                "evidence_counts": dict(bundle_result.evidence_counts),
            },
        )
    )

    # --- 6. agent review of the generated bundle --------------------------
    review_ids: list[str] = []
    for bundle_id in bundle_ids:
        try:
            review_result = run_agent_review(
                package_path,
                bundle_id,
                tool_name=tool_name,
                tool_version=tool_version,
                force=force,
                force_stale_lock=force_stale_lock,
                limits=limits,
            )
        except AgentReviewWriteError as exc:
            raise V1BuildError(f"agent review failed for bundle {bundle_id!r}: {exc}") from exc
        review_ids.append(review_result.review_id)
        steps.append(
            StepResult(
                "agent_review",
                "ok",
                {
                    "review_id": review_result.review_id,
                    "review_status": review_result.review_status,
                },
            )
        )

    # --- 7. validate again ------------------------------------------------
    report_after = validate_package(package_path, limits=limits)
    validation_after = ValidationSnapshot.from_report(report_after)
    steps.append(
        StepResult(
            "validate_after",
            "ok" if validation_after.valid else "failed",
            {
                "valid": validation_after.valid,
                "error_count": validation_after.error_count,
            },
        )
    )
    if not validation_after.valid:
        raise V1BuildError(
            "the package did not validate after the build steps: "
            f"{report_after.errors[:3]}"
        )

    # --- 8. built-in V1 index (default on) --------------------------------
    # After the package validates, write its own agent-readable index under
    # index/v1/ so the package is self-describing. This adds no evidence
    # lane and writes no receipt; it only re-packages the already-built
    # agent context. Kept last so it reflects the final track state.
    index_written = False
    index_artifacts: list[str] = []
    if write_index:
        try:
            index_result = write_v1_package_index(
                package_path,
                force_stale_lock=force_stale_lock,
                tool_name=tool_name,
                tool_version=tool_version,
                limits=limits,
            )
        except V1PackageIndexError as exc:
            raise V1BuildError(f"built-in V1 index write failed: {exc}") from exc
        index_written = True
        index_artifacts = list(index_result.written_paths)
        steps.append(
            StepResult(
                "v1_package_index",
                "ok",
                {"artifacts": index_artifacts},
            )
        )
    else:
        steps.append(
            StepResult("v1_package_index", "skipped", {"reason": "write_index=False"})
        )

    # Re-read the manifest for final, authoritative per-track counts.
    from .manifest import Manifest

    final_manifest = Manifest.from_json_file(package_path / "manifest.json", limits=limits)

    return V1BuildResult(
        package_path=package_path,
        profile=profile,
        partial=partial,
        duration_ms=duration_ms,
        track_counts=_track_counts(final_manifest),
        receipts=_receipt_files(package_path),
        validation_before=validation_before,
        validation_after=validation_after,
        visual_change_events=visual_change_events,
        changed_region_events=changed_region_events,
        bundle_ids=bundle_ids,
        review_ids=review_ids,
        skipped_lanes=skipped_lanes,
        index_written=index_written,
        index_artifacts=index_artifacts,
        steps=steps,
    )
