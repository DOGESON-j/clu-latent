#!/usr/bin/env python3
"""Phase 3.11: a read-only debugging/audit harness that reports, honestly,
what CLULatent can and cannot determine about a real clip *from the
package alone*.

The one question this tool answers:

    "Given only this package, can CLULatent determine what happens in
    the clip?"

For every package this codebase can produce today, the honest answer is
almost always **NO -- insufficient evidence**. This script exists to
make that gap explicit and auditable instead of letting a downstream
caller quietly assume a package that merely *validates* also *explains*
its clip.

What it may report (all directly present in the package, never inferred):

  - package validity (via the existing `validate.validate_package`)
  - duration, resolution (from `manifest.source`)
  - keyframe count (from `manifest.media.keyframes`)
  - audio presence (from `manifest.source.has_audio` / an `audio_events`
    track)
  - track inventory (the manifest's declared tracks)
  - audio digest presence, and whether those records are synthetic/demo
    evidence rather than real audio analysis
  - whether speech / semantic / real-audio-feature / visual
    interpretation lanes are present

What it must never do:

  - infer a visual story, scene meaning, intent, or emotion
  - interpret real audio from synthetic digest records
  - claim to know "what happens in the clip" unless an actual package
    evidence lane supports it
  - run an LLM, a model, an audio adapter, or ffmpeg
  - mutate the package (no receipt, no track edit, no manifest edit, no
    index rebuild)

This is a diagnostic tool, not a feature generator. It adds no new
adapter, validation rule, retrieval function, or dependency. It is
read-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    import clu_latent  # noqa: F401
except ImportError:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from clu_latent import audio_digest_retrieval as audio_digest_retrieval_mod
from clu_latent.audio_digest_writer import AUDIO_DIGEST_TRACK_NAME
from clu_latent.constants import AUDIO_DIGEST_RECEIPTS_FILE, AUDIO_DIGEST_TRACK_FILE
from clu_latent.manifest import Manifest
from clu_latent.security.console import safe_console_text
from clu_latent.validate import validate_package

from rich.console import Console

# --- Evidence-lane catalogs -------------------------------------------------
#
# These are deliberately fixed, documented lists -- not heuristics. A lane
# only counts as "present" if it is declared in the manifest AND carries at
# least one record. A declared-but-empty lane is treated exactly like an
# absent one: it explains nothing.

# Lanes that would let CLULatent say something about *what is visually
# happening* in the clip. None of these are produced by any adapter that
# ships today, so this set is currently always empty for real packages.
VISUAL_INTERPRETATION_LANES: tuple[str, ...] = (
    "scene_events",
    "object_proposal_events",
    "object_tracking_events",
    "ocr_events",
    "motion_events",
    "semantic_events",
)

# Lanes that would carry *real analyzed audio* (energy/transient/texture/
# rhythm/etc.). An `audio_events` track is NOT in this set: audio presence
# is not audio understanding. Audio digest records are NOT in this set
# either: today they are synthetic/demo evidence, never real analysis.
REAL_AUDIO_FEATURE_LANES: tuple[str, ...] = (
    "audio_energy_events",
    "audio_transient_events",
    "audio_texture_events",
    "audio_signature_events",
    "rhythm_events",
    "stereo_events",
    "music_events",
)

SPEECH_LANE = "speech_events"
SEMANTIC_LANE = "semantic_events"
BASIC_AUDIO_LANE = "audio_events"

# The honest roadmap of what would actually be needed to start closing
# these gaps -- printed as "recommended next work", never as a promise.
RECOMMENDED_NEXT_WORK: tuple[str, ...] = (
    "Contact sheet / keyframe preview report surface",
    "Real audio energy/transient adapter",
    "Scene-change lane",
    "OCR lane",
    "Motion/object candidate lane",
)


class AuditError(RuntimeError):
    """Raised only when the target package cannot be audited at all.

    Reserved for the cases where there is nothing to report on -- a
    missing/non-directory package path, or a `manifest.json` that
    cannot be read/parsed. A package that merely *validates poorly*, or
    an audio digest track that is corrupt, is NOT an `AuditError`: those
    are exactly the gaps this tool is meant to report, so they produce a
    normal audit (with FAIL/invalid noted) rather than aborting.
    """


@dataclass
class TrackFinding:
    name: str
    record_count: int

    @property
    def present(self) -> bool:
        return self.record_count > 0


@dataclass
class AudioDigestFinding:
    # One of: "absent", "synthetic_demo", "invalid", "manifest_mismatch".
    status: str = "absent"
    record_count: int = 0
    producer_names: list[str] = field(default_factory=list)
    receipt_present: bool = False
    detail: str | None = None


@dataclass
class Audit:
    package_path: Path
    package_id: str
    validation_pass: bool
    validation_error_count: int
    duration_ms: int | None
    width: int | None
    height: int | None
    has_audio: bool
    keyframe_count: int
    tracks: list[TrackFinding]
    audio_digest: AudioDigestFinding
    gaps: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)

    # --- derived lane presence ---------------------------------------------

    def _present_names(self) -> set[str]:
        return {t.name for t in self.tracks if t.present}

    @property
    def has_visual_interpretation_lane(self) -> bool:
        return bool(self._present_names() & set(VISUAL_INTERPRETATION_LANES))

    @property
    def has_real_audio_feature_lane(self) -> bool:
        return bool(self._present_names() & set(REAL_AUDIO_FEATURE_LANES))

    @property
    def has_speech_evidence(self) -> bool:
        return SPEECH_LANE in self._present_names()

    @property
    def has_semantic_evidence(self) -> bool:
        return SEMANTIC_LANE in self._present_names()

    @property
    def has_basic_audio(self) -> bool:
        return self.has_audio or BASIC_AUDIO_LANE in self._present_names()

    # --- the three honest answers ------------------------------------------

    @property
    def can_describe_visual_events(self) -> bool:
        return self.has_visual_interpretation_lane

    @property
    def can_describe_audio_meaning(self) -> bool:
        # Real analyzed-audio lanes only. Basic audio presence and
        # synthetic/demo digest records deliberately do NOT count.
        return self.has_real_audio_feature_lane

    @property
    def can_answer_what_happens(self) -> bool:
        return self.can_describe_visual_events or self.can_describe_audio_meaning


def _load_manifest(package_path: Path) -> Manifest:
    if not package_path.exists() or not package_path.is_dir():
        raise AuditError(f"Package not found or not a directory: {package_path}")
    manifest_path = package_path / "manifest.json"
    if not manifest_path.exists():
        raise AuditError(f"manifest.json not found in package: {package_path}")
    try:
        return Manifest.from_json_file(manifest_path)
    except Exception as exc:  # noqa: BLE001 - re-raised as a clean audit error
        raise AuditError(f"manifest.json could not be read: {exc}") from exc


def _audit_audio_digest(package_path: Path, manifest: Manifest) -> AudioDigestFinding:
    """Inspect the audio digest track, read-only, without ever trusting it.

    Uses the Phase 3.8 `load_audio_digest_events`, which re-runs Phase
    3.4 validation before returning anything -- so a corrupt, duplicate-
    id, path-traversal, or dense-array digest surfaces here as
    `status="invalid"` (caught, never crashing), and its records are
    never partially trusted.
    """
    descriptor = next(
        (t for t in manifest.tracks if t.name == AUDIO_DIGEST_TRACK_NAME), None
    )
    track_file_on_disk = (package_path / AUDIO_DIGEST_TRACK_FILE).is_file()
    receipt_present = (package_path / AUDIO_DIGEST_RECEIPTS_FILE).is_file()

    if descriptor is None:
        if track_file_on_disk:
            return AudioDigestFinding(
                status="manifest_mismatch",
                receipt_present=receipt_present,
                detail=(
                    "an audio digest track file exists on disk but is not "
                    "declared in the manifest"
                ),
            )
        return AudioDigestFinding(status="absent")

    try:
        events = audio_digest_retrieval_mod.load_audio_digest_events(package_path)
    except audio_digest_retrieval_mod.AudioDigestRetrievalError as exc:
        return AudioDigestFinding(
            status="invalid",
            receipt_present=receipt_present,
            detail=str(exc),
        )

    producer_names: list[str] = []
    for event in events:
        producer = event.get("producer")
        if isinstance(producer, dict):
            name = producer.get("name")
            if isinstance(name, str) and name not in producer_names:
                producer_names.append(name)

    return AudioDigestFinding(
        # No real audio adapter exists in this codebase, so any audio
        # digest records present are, by construction, synthetic/demo
        # evidence -- never the output of real audio analysis.
        status="synthetic_demo",
        record_count=len(events),
        producer_names=producer_names,
        receipt_present=receipt_present,
    )


def audit_package(package_path: Path) -> Audit:
    """Build a read-only, structured evidence-gap audit of one package.

    Raises `AuditError` only when the package cannot be opened at all.
    A package that validates as invalid, or whose audio digest is
    corrupt, still produces a full audit (with the failure noted).
    """
    package_path = Path(package_path)
    manifest = _load_manifest(package_path)

    report = validate_package(package_path)

    tracks = [
        TrackFinding(name=t.name, record_count=t.record_count) for t in manifest.tracks
    ]
    audio_digest = _audit_audio_digest(package_path, manifest)

    audit = Audit(
        package_path=package_path,
        package_id=manifest.package_id,
        validation_pass=report.valid,
        validation_error_count=len(report.errors),
        duration_ms=manifest.source.duration_ms,
        width=manifest.source.width,
        height=manifest.source.height,
        has_audio=manifest.source.has_audio,
        keyframe_count=manifest.media.keyframes.count,
        tracks=tracks,
        audio_digest=audio_digest,
    )

    _populate_gaps_and_risks(audit)
    return audit


def _populate_gaps_and_risks(audit: Audit) -> None:
    gaps = audit.gaps
    risks = audit.risks

    if not audit.can_describe_visual_events:
        gaps.append(
            "No visual interpretation lane is present (scene, object, OCR, "
            "motion, frame caption, or semantic evidence); visual events "
            "cannot be described."
        )
    if not audit.can_describe_audio_meaning:
        gaps.append(
            "No real audio feature analysis lane is present; audio meaning "
            "cannot be described (basic audio presence is not audio "
            "understanding)."
        )
    if not audit.has_speech_evidence:
        gaps.append("Speech/transcript evidence is unavailable or empty.")
    if not audit.has_semantic_evidence:
        gaps.append("Semantic evidence is unavailable or empty.")

    if not audit.validation_pass:
        risks.append(
            f"Package validation reported {audit.validation_error_count} "
            "error(s); its evidence should not be trusted until it validates."
        )

    ad = audit.audio_digest
    if ad.status == "synthetic_demo":
        risks.append(
            "Audio digest records are synthetic/demo evidence, not real clip "
            "audio analysis. They must not be read as real audio findings."
        )
        if not ad.receipt_present:
            risks.append(
                "Audio digest track is present without a receipt; it is not "
                "fully receipted canonical evidence and should not be treated "
                "as fully trusted."
            )
    elif ad.status == "invalid":
        risks.append(
            "Audio digest track is present but invalid or unreadable; it must "
            "not be partially trusted."
        )
    elif ad.status == "manifest_mismatch":
        risks.append(
            "An audio digest track file exists on disk but is not declared in "
            "the manifest; treat it as untrusted and do not rely on it."
        )


# --- rendering --------------------------------------------------------------


def _yesno(value: bool) -> str:
    return "YES" if value else "NO"


def _resolution(audit: Audit) -> str:
    if audit.width and audit.height:
        return f"{audit.width}x{audit.height}"
    return "unknown"


def _audio_digest_line(audit: Audit) -> str:
    ad = audit.audio_digest
    if ad.status == "absent":
        return "no audio digest track"
    if ad.status == "synthetic_demo":
        return f"synthetic/demo evidence only ({ad.record_count} record(s))"
    if ad.status == "invalid":
        return "present but invalid or unreadable"
    if ad.status == "manifest_mismatch":
        return "track file present but not declared in manifest"
    return ad.status


def render_human(audit: Audit, console: Console, *, question: str | None = None) -> None:
    console.print("[bold]CLULatent Real Clip Evidence Gap Audit[/bold]")
    console.print()
    console.print(f"Package: {safe_console_text(str(audit.package_path))}")
    console.print(f"Package id: {safe_console_text(audit.package_id)}")
    console.print(f"Validation: {'PASS' if audit.validation_pass else 'FAIL'}")
    if audit.duration_ms is not None:
        console.print(f"Duration: {audit.duration_ms} ms")
    console.print(f"Resolution: {_resolution(audit)}")
    console.print(
        f"Keyframes: {'present' if audit.keyframe_count > 0 else 'none'} "
        f"(count known: {audit.keyframe_count})"
    )
    console.print(f"Audio: {'present' if audit.has_basic_audio else 'none/silent'}")
    console.print(
        f"Speech evidence: {'present' if audit.has_speech_evidence else 'empty'}"
    )
    console.print(
        f"Semantic evidence: {'present' if audit.has_semantic_evidence else 'empty'}"
    )
    console.print(f"Audio digest: {_audio_digest_line(audit)}")

    track_names = ", ".join(safe_console_text(t.name) for t in audit.tracks) or "none"
    console.print(f"Track inventory: {track_names}")
    console.print()

    console.print(f"Can describe visual events: {_yesno(audit.can_describe_visual_events)}")
    console.print(
        "  Reason: keyframes may exist, but no visual interpretation lane "
        "(scene, object, OCR, motion, frame caption, or semantic evidence) "
        "is present, so visual interpretation is unavailable."
    )
    console.print(f"Can describe audio meaning: {_yesno(audit.can_describe_audio_meaning)}")
    console.print(
        "  Reason: only basic audio presence/non-silence evidence exists, or "
        "the audio digest is synthetic/demo evidence; no real audio feature "
        "analysis lane is present."
    )
    console.print(
        f"Can answer \u201cwhat happens in this clip?\u201d: "
        f"{_yesno(audit.can_answer_what_happens)}"
    )
    console.print("  Reason: insufficient evidence in this package to determine what happens.")
    console.print()

    if question is not None:
        console.print(f"Question: {safe_console_text(question)}")
        if audit.can_answer_what_happens:
            console.print(
                "  Answer: some interpretation lanes are present; see the "
                "evidence inventory above. This audit still does not generate "
                "a narrative -- it only reports which evidence lanes exist."
            )
        else:
            console.print(
                "  Answer: insufficient evidence -- cannot determine what "
                "happens from the current package evidence. No story is "
                "generated; only the evidence inventory above is reported."
            )
        console.print()

    if audit.gaps:
        console.print("[bold]Evidence gaps[/bold]")
        for gap in audit.gaps:
            console.print(f"  - {safe_console_text(gap)}")
        console.print()

    if audit.risks:
        console.print("[bold]Risks / trust warnings[/bold]")
        for risk in audit.risks:
            console.print(f"  - {safe_console_text(risk)}")
        console.print()

    console.print("[bold]Recommended next work[/bold]")
    for idx, item in enumerate(RECOMMENDED_NEXT_WORK, start=1):
        console.print(f"  {idx}. {safe_console_text(item)}")
    console.print()

    console.print(
        "[dim]All findings above are evidence, not truth. This audit reports "
        "only what is present in the package; it does not interpret the clip, "
        "and CLULatent does not claim to understand audio or video.[/dim]"
    )


def audit_to_dict(audit: Audit, *, question: str | None = None) -> dict[str, Any]:
    ad = audit.audio_digest
    result: dict[str, Any] = {
        "validation_status": "PASS" if audit.validation_pass else "FAIL",
        "can_describe_visual_events": audit.can_describe_visual_events,
        "can_describe_audio_meaning": audit.can_describe_audio_meaning,
        "can_answer_what_happens": audit.can_answer_what_happens,
        "evidence_inventory": {
            "package_path": str(audit.package_path),
            "package_id": audit.package_id,
            "validation_status": "PASS" if audit.validation_pass else "FAIL",
            "duration_ms": audit.duration_ms,
            "resolution": _resolution(audit),
            "has_audio": audit.has_basic_audio,
            "keyframe_count": audit.keyframe_count,
            "tracks": [
                {"name": t.name, "record_count": t.record_count} for t in audit.tracks
            ],
            "speech_evidence_present": audit.has_speech_evidence,
            "semantic_evidence_present": audit.has_semantic_evidence,
            "audio_digest": {
                "status": ad.status,
                "record_count": ad.record_count,
                "producer_names": ad.producer_names,
                "receipt_present": ad.receipt_present,
                "is_synthetic_demo": ad.status == "synthetic_demo",
            },
        },
        "gaps": list(audit.gaps),
        "risks": list(audit.risks),
        "recommended_next_work": list(RECOMMENDED_NEXT_WORK),
        "note": "All findings are evidence, not truth; this audit does not interpret the clip.",
    }
    if question is not None:
        result["question"] = question
        result["question_answerable"] = audit.can_answer_what_happens
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="debug_real_clip_evidence_gap.py",
        description=(
            "Read-only evidence-gap debugging/audit tool for a .clulatent "
            "package. Reports, honestly, what CLULatent can and cannot "
            "determine about a clip from the package alone -- for current "
            "packages, usually NO: insufficient evidence. It never interprets "
            "the clip, never runs an LLM/model/adapter, and never mutates the "
            "package. Findings are evidence, not truth."
        ),
    )
    parser.add_argument(
        "package",
        type=Path,
        help="Path to the .clulatent package to audit (read-only).",
    )
    parser.add_argument(
        "--question",
        type=str,
        default=None,
        help=(
            "An optional free-text question (e.g. \"what happens in this "
            "clip?\"). The audit answers only whether the package holds enough "
            "evidence to answer it -- it never generates a story."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the audit as a single JSON object instead of human-readable text.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        audit = audit_package(args.package)
    except AuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(audit_to_dict(audit, question=args.question), indent=2, sort_keys=True))
    else:
        render_human(audit, Console(), question=args.question)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
