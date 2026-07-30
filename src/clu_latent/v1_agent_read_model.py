"""Token-budgeted, non-semantic agent read model for V1 packages.

The module reads package evidence exclusively through :class:`PackageReader`.
Building and rendering are read-only.  ``write_agent_read_artifacts`` is the
single mutating entry point and writes only derived files below ``index/v1``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA_ID = "clulatent.v1_agent_read_model.v0"
SCHEMA_VERSION = "0"
MANIFEST_SCHEMA_ID = "clulatent.v1_agent_read_manifest.v0"
INDEX_DIR = "index/v1"
ARTIFACTS = (
    "agent_read_micro.md",
    "agent_read_model.json",
    "agent_read_model.md",
    "agent_read_windows.jsonl",
)
MANIFEST = "agent_read_manifest.json"
BUDGETS = ("micro", "summary", "standard", "full")
_WINDOW_LIMITS = {"micro": 3, "summary": 8, "standard": 64, "full": 256}
_TRACK_KEYS = {
    "keyframes": "keyframe_ids",
    "visual_change_candidates": "visual_change_candidate_ids",
    "changed_region_candidates": "changed_region_candidate_ids",
    "audio_events": "audio_event_ids",
    "speech_events": "speech_event_ids",
    "evidence_bundles": "evidence_bundle_ids",
    "agent_review_events": "agent_review_ids",
}

SAFE_ANSWER_POLICY = [
    "Cite timestamps and event IDs.",
    "Describe records as candidate evidence or available evidence.",
    "Do not identify objects, people, faces, actions, intent, emotion, identity, or scene meaning.",
    "Do not turn visual-change candidates into recognition claims.",
    "Do not turn changed-region candidates into object localization claims.",
    "Do not turn agent review into semantic confirmation.",
    "Request human review when available evidence is insufficient.",
    "Retrieve focused windows instead of reading the entire package when possible.",
]
CAVEATS = [
    "This read model is a bounded evidence map, not semantic certification.",
    "Candidate evidence alone does not support recognition or scene-meaning claims.",
    "Missing evidence means unavailable in the package read surface, not that an event did not occur.",
]


class AgentReadModelError(RuntimeError):
    """Raised when an agent read model cannot be built or written safely."""


@dataclass(frozen=True)
class AgentReadWriteResult:
    package_path: Path
    package_id: str
    written_paths: list[str]
    manifest: dict[str, Any]


def _check_budget(budget: str) -> None:
    if budget not in BUDGETS:
        raise AgentReadModelError(f"unsupported budget {budget!r}; choose from {', '.join(BUDGETS)}")


def _event_path(payload: dict[str, Any]) -> str | None:
    for key in ("path", "image_path", "file", "relative_path", "keyframe_path"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _strength(payload: dict[str, Any]) -> float:
    for key in ("score", "change_score", "difference_score", "changed_fraction", "strength_score"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, float(value))
    label = str(payload.get("strength", "")).lower()
    return {"low": 0.25, "medium": 0.6, "high": 1.0}.get(label, 0.0)


def _overlaps(event: Any, start: int, end: int) -> bool:
    return event.t_start_ms < end and event.t_end_ms >= start


def _review_bundle_id(event: Any) -> str | None:
    value = event.payload.get("evidence_bundle_id")
    return str(value) if value is not None else None


def _make_window(
    package_path: Path, reader: Any, all_events: dict[str, list[Any]],
    index: int, start: int, end: int,
) -> dict[str, Any]:
    from .timecode import ms_to_timecode

    refs: dict[str, list[Any]] = {
        name: [event for event in events if _overlaps(event, start, end)]
        for name, events in all_events.items()
    }
    bundle_ids = {e.id for e in refs.get("evidence_bundles", [])}
    reviews = [e for e in all_events.get("agent_review_events", []) if _review_bundle_id(e) in bundle_ids or _overlaps(e, start, end)]
    refs["agent_review_events"] = reviews
    keyframe_paths = [p for p in (_event_path(e.payload) for e in refs.get("keyframes", [])) if p]
    vc_score = max((_strength(e.payload) for e in refs.get("visual_change_candidates", [])), default=0.0)
    cr_score = max((_strength(e.payload) for e in refs.get("changed_region_candidates", [])), default=0.0)
    counts = sum(len(refs.get(name, [])) for name in _TRACK_KEYS)
    density_score = round(min(1.0, counts / 8.0 + vc_score * 0.2 + cr_score * 0.2), 4)
    density_label = "high" if density_score >= .7 else "medium" if density_score >= .3 else "low"
    missing = [name for name in _TRACK_KEYS if not reader.has_track(name)]
    window_id = f"ew_{index:06d}"
    ids = {key: [e.id for e in refs.get(track, [])] for track, key in _TRACK_KEYS.items()}
    tracks = [name for name in _TRACK_KEYS if ids[_TRACK_KEYS[name]]]
    summary_parts = [f"{density_label.capitalize()} evidence density in this bounded window."]
    if ids["visual_change_candidate_ids"]:
        summary_parts.append("Visual-change candidate evidence is available.")
    if ids["changed_region_candidate_ids"]:
        summary_parts.append("Changed-region candidate evidence is available.")
    if ids["audio_event_ids"]:
        summary_parts.append("Audio event coverage is available.")
    summary_parts.append("No recognition, action, identity, intent, emotion, or scene-meaning claim is supported by this window alone.")
    return {
        "window_id": window_id,
        "t_start_ms": start,
        "t_end_ms": end,
        "timecode_start": ms_to_timecode(start),
        "timecode_end": ms_to_timecode(end),
        "evidence_density": {"score": density_score, "label": density_label, "event_ref_count": counts},
        **ids,
        "keyframe_paths": keyframe_paths,
        "safe_summary": " ".join(summary_parts),
        "missing_or_unavailable_evidence": missing,
        "caveats": ["Candidate evidence requires timestamped inspection and may require human review."],
        "retrieval_hints": {
            "event_ids": sorted({event.id for events in refs.values() for event in events}),
            "track_names": tracks,
            "package_relative_keyframe_paths": keyframe_paths,
            "viewer_anchor": f"index/v1/viewer.html#t={start}",
            "suggested_command": f"clulatent agent-read window PACKAGE --time {start / 1000:g}s",
            "evidence_bundle_ids": ids["evidence_bundle_ids"],
            "agent_review_ids": ids["agent_review_ids"],
        },
        "_ranking": {"visual": vc_score, "region": cr_score},
    }


def _rankings(windows: list[dict[str, Any]]) -> dict[str, list[str]]:
    def top(key, reverse=True):
        return [w["window_id"] for w in sorted(windows, key=key, reverse=reverse)]
    dense = top(lambda w: (w["evidence_density"]["score"], -w["t_start_ms"]))
    return {
        "most_evidence_dense_windows": dense[:10],
        "highest_visual_change_windows": [i for i in top(lambda w: (w["_ranking"]["visual"], -w["t_start_ms"])) if next(x for x in windows if x["window_id"] == i)["visual_change_candidate_ids"]][:10],
        "strongest_changed_region_windows": [i for i in top(lambda w: (w["_ranking"]["region"], -w["t_start_ms"])) if next(x for x in windows if x["window_id"] == i)["changed_region_candidate_ids"]][:10],
        "windows_with_audio_events": [w["window_id"] for w in windows if w["audio_event_ids"]][:20],
        "windows_with_bundle_review_coverage": [w["window_id"] for w in windows if w["evidence_bundle_ids"] and w["agent_review_ids"]][:20],
        "lowest_evidence_windows": top(lambda w: (w["evidence_density"]["score"], w["t_start_ms"]), reverse=False)[:10],
        "windows_with_missing_evidence": [w["window_id"] for w in windows if w["missing_or_unavailable_evidence"]][:20],
        "recommended_inspection_order": dense[:20],
    }


def build_agent_read_model(package_path: Path | str, *, budget: str = "standard", window_ms: int = 1000,
                           max_windows: int | None = None) -> dict[str, Any]:
    from . import agent_context as agent_context_mod
    from . import package_reader as package_reader_mod

    _check_budget(budget)
    if window_ms <= 0:
        raise AgentReadModelError("window_ms must be greater than zero")
    try:
        reader = package_reader_mod.open_package(package_path)
        context = agent_context_mod.build_agent_context(package_path)
    except (package_reader_mod.PackageReaderError, agent_context_mod.AgentContextError) as exc:
        raise AgentReadModelError(str(exc)) from exc
    all_events = {name: reader.load_track(name) for name in reader.track_names()}
    total = max(1, (reader.duration_ms + window_ms - 1) // window_ms)
    cap = _WINDOW_LIMITS[budget]
    if max_windows is not None:
        if max_windows <= 0:
            raise AgentReadModelError("max_windows must be greater than zero")
        cap = min(cap, max_windows)
    # Preserve windows that actually carry event starts, then fill remaining
    # capacity with deterministic coverage samples across long packages.
    event_indices = {
        min(total - 1, max(0, event.t_start_ms // window_ms))
        for events in all_events.values() for event in events
    }
    selected = sorted(event_indices)[:cap]
    if len(selected) < cap:
        sample_count = min(total, cap)
        sampled = ({0} if sample_count == 1 else {
            round(i * (total - 1) / (sample_count - 1)) for i in range(sample_count)
        })
        for index in sorted(sampled):
            if index not in selected:
                selected.append(index)
                if len(selected) == cap:
                    break
        selected.sort()
    windows = [_make_window(Path(package_path), reader, all_events, i, i * window_ms,
                            min(reader.duration_ms, (i + 1) * window_ms)) for i in selected]
    rankings = _rankings(windows)
    for window in windows:
        window.pop("_ranking", None)
    evidence = context.get("evidence", {})
    model = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "budget": budget,
        "package": context["package"], "validation": context["validation"], "lock": context["lock"],
        "tracks": context["tracks"], "track_counts": context["event_counts"],
        "evidence_coverage": context["time_coverage"], "windows": windows,
        "windows_truncated": total > len(windows), "total_window_count": total,
        "rankings": rankings, "safe_answer_policy": list(SAFE_ANSWER_POLICY),
        "caveats": list(CAVEATS), "next_actions": [
            "Inspect the recommended evidence windows first.",
            "Retrieve a focused window and cite its timestamp and event IDs.",
            "Request human review when candidate evidence is insufficient.",
        ],
        "evidence_bundle_ids": [b.get("id") for b in evidence.get("evidence_bundles", []) if b.get("id")],
        "agent_review_ids": [r.get("id") for r in evidence.get("agent_reviews", []) if r.get("id")],
    }
    return model


def render_agent_read_summary(model: dict[str, Any]) -> str:
    package, validation = model["package"], model["validation"]
    ranked = model.get("rankings", {}).get("recommended_inspection_order", [])[:3]
    return (f"CLULatent {package['package_id']}: {package['duration_ms']} ms; validation "
            f"{'valid' if validation['valid'] else 'invalid'}; {len(model.get('tracks', []))} tracks; "
            f"{sum(model.get('track_counts', {}).values())} events. Inspection candidates: "
            f"{', '.join(ranked) or 'none'}. Candidate evidence only; cite timestamps and event IDs; "
            "no recognition or scene-meaning claim is supported without further review.\n")


def render_agent_read_markdown(model: dict[str, Any], *, budget: str = "standard") -> str:
    _check_budget(budget)
    if budget == "micro":
        return render_agent_read_summary(model)
    p = model["package"]
    lines = ["# CLULatent agent read model", "", f"- package_id: `{p['package_id']}`",
             f"- duration_ms: {p['duration_ms']}", f"- validation_valid: {model['validation']['valid']}",
             f"- budget: {budget}", "", "## Tracks", ""]
    lines.extend(f"- `{name}`: {count}" for name, count in model["track_counts"].items())
    lines += ["", "## Evidence windows", ""]
    window_cap = {"summary": 8, "standard": 32, "full": 128}.get(budget, 3)
    for w in model["windows"][:window_cap]:
        lines += [f"### {w['window_id']} — {w['timecode_start']} to {w['timecode_end']}", "",
                  w["safe_summary"], "", f"- event IDs: {', '.join(w['retrieval_hints']['event_ids']) or 'none'}",
                  f"- retrieve: `{w['retrieval_hints']['suggested_command']}`", ""]
    lines += ["## Safe answer policy", ""] + [f"- {rule}" for rule in model["safe_answer_policy"]]
    if budget == "full":
        lines += ["", "## Safe rankings", ""]
        for name, ids in model.get("rankings", {}).items():
            lines.append(f"- `{name}`: {', '.join(ids) or 'none'}")
    lines += ["", "## Caveats", ""] + [f"- {c}" for c in model["caveats"]]
    return "\n".join(lines) + "\n"


def get_agent_read_window(package_path: Path | str, *, time_ms: int | None = None, start_ms: int | None = None,
                          end_ms: int | None = None, budget: str = "standard") -> dict[str, Any]:
    from . import package_reader as package_reader_mod

    _check_budget(budget)
    if time_ms is not None:
        start_ms = max(0, (time_ms // 1000) * 1000)
        end_ms = start_ms + 1000
    if start_ms is None:
        raise AgentReadModelError("provide time_ms or start_ms")
    reader = package_reader_mod.open_package(package_path)
    start = max(0, start_ms)
    end = min(reader.duration_ms, end_ms if end_ms is not None else start + 1000)
    if end <= start:
        raise AgentReadModelError("window end must be greater than start")
    all_events = {name: reader.load_track(name) for name in reader.track_names()}
    window = _make_window(Path(package_path), reader, all_events, start // 1000, start, end)
    window.pop("_ranking", None)
    return {"schema_id": SCHEMA_ID + ".window", "budget": budget, "package_id": reader.package_id,
            "window": window, "safe_answer_policy": list(SAFE_ANSWER_POLICY), "caveats": list(CAVEATS)}


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_agent_read_artifacts(package_path: Path | str, *, force_stale_lock: bool = False) -> AgentReadWriteResult:
    from datetime import datetime, timezone

    from .constants import TOOL_NAME, TOOL_VERSION
    from .lock import lock_status
    from .security.operation_lock import OperationLockError, operation_lock
    from .security.paths import PathSecurityError, resolve_in_package

    package_path = Path(package_path)
    if not package_path.is_dir():
        raise AgentReadModelError(f"Package not found: {package_path}")
    status, _ = lock_status(package_path)
    if status == "locked":
        raise AgentReadModelError("package has a valid integrity lock; agent-read write-index refuses mutation")
    model = build_agent_read_model(package_path, budget="full")
    micro = render_agent_read_summary({**model, "budget": "micro"})
    model_json = json.dumps(model, indent=2) + "\n"
    model_md = render_agent_read_markdown(model, budget="full")
    windows_jsonl = "".join(json.dumps(w, sort_keys=True) + "\n" for w in model["windows"])
    contents = dict(zip(ARTIFACTS, (micro, model_json, model_md, windows_jsonl)))
    artifacts = [{"name": name, "path": f"{INDEX_DIR}/{name}", "size_bytes": len(text.encode()),
                  "sha256": hashlib.sha256(text.encode()).hexdigest()} for name, text in contents.items()]
    manifest = {"schema_id": MANIFEST_SCHEMA_ID, "schema_version": SCHEMA_VERSION,
                "package_id": model["package"]["package_id"], "generated_at": datetime.now(timezone.utc).isoformat(),
                "generator": {"tool": TOOL_NAME, "version": TOOL_VERSION}, "default_window_ms": 1000,
                "budgets_available": list(BUDGETS), "artifacts": artifacts,
                "package_validation_status": model["validation"], "track_counts": model["track_counts"],
                "evidence_bundle_ids": model["evidence_bundle_ids"], "agent_review_ids": model["agent_review_ids"],
                "caveats": list(CAVEATS), "note": "This agent read manifest is not semantic certification."}
    contents[MANIFEST] = json.dumps(manifest, indent=2) + "\n"
    try:
        with operation_lock(package_path, operation="v1_agent_read", force_stale=force_stale_lock):
            root = package_path.resolve(strict=True)
            out = root / "index" / "v1"
            for part in (root / "index", out):
                if part.is_symlink() or (part.exists() and not part.is_dir()):
                    raise AgentReadModelError(f"unsafe index path: {part}")
                part.mkdir(exist_ok=True)
            written = []
            for name in (*ARTIFACTS, MANIFEST):
                relative = f"{INDEX_DIR}/{name}"
                try:
                    target = resolve_in_package(package_path, relative, field_name=relative, for_write=True)
                except PathSecurityError as exc:
                    raise AgentReadModelError(str(exc)) from exc
                _atomic_write(target, contents[name]); written.append(relative)
    except OperationLockError as exc:
        raise AgentReadModelError(str(exc)) from exc
    return AgentReadWriteResult(package_path, model["package"]["package_id"], written, manifest)


def load_agent_read_manifest(package_path: Path | str) -> dict[str, Any]:
    from .security.paths import resolve_in_package

    path = resolve_in_package(Path(package_path), f"{INDEX_DIR}/{MANIFEST}", field_name=MANIFEST)
    if not path.is_file():
        raise AgentReadModelError(f"agent read manifest not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentReadModelError(f"could not load agent read manifest: {exc}") from exc
    if data.get("schema_id") != MANIFEST_SCHEMA_ID:
        raise AgentReadModelError("unsupported agent read manifest schema")
    return data
