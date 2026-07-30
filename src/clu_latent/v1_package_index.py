"""V1: the V1 self-describing package index.

Phases 3.18-3.21 made a `.clulatent` package readable, buildable,
explainable, and askable — but the agent-readable artifacts (the agent
context JSON/Markdown and the safe ask prompt) all lived *outside* the
package, exported on demand as sidecar files. This module puts them
*inside* the package, under `index/v1/`, so a package carries its own
ready-to-use V1 agent context. When CLU, Codex, or another agent opens a
V1-built package it finds, without any separate export step:

  - `index/v1/agent_context.json`  — the V1 agent context (the
                                     machine contract)
  - `index/v1/agent_context.md`    — the same context as a readable brief
  - `index/v1/ask_prompt.md`       — the V1 safe ask prompt
  - `index/v1/index_manifest.json` — a small descriptor of the above:
                                     schema id/version, package id,
                                     generation time and tool, per-artifact
                                     relative paths + content hashes, the
                                     package's validation status at
                                     generation time, track counts, evidence
                                     bundle / agent review ids, and caveats.

Boundaries (inherited from the layers this builds on):

- Everything is derived from `agent_context.build_agent_context`, which
  opens the package through the V1 read-only reader. This module
  never walks the folder tree to *read* content, never assumes a track
  filename, and adds **no** new evidence lane.
- It performs **no** semantic interpretation: no object, person, face,
  action, scene, speech, or intent claim. It only re-packages
  already-existing candidate-evidence facts.
- It runs **no** FFmpeg/Pillow/ML model/network call.
- The only mutation it performs is writing the four `index/v1/` artifacts.
  It writes **no** receipt and generates **no** evidence track. It honors
  the package's integrity lock exactly like the evidence writers: it
  refuses to write against a package that carries a valid integrity lock,
  and it guards its write with the standard operation lock.

The index manifest is deliberately **not** a lock and **not** a
certificate: it records what the built-in index files are and what they
hashed to at generation time, so an agent can tell whether the built-in
index is stale relative to the package's tracks. It makes no claim that
the package's evidence is semantically true.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import agent_context as agent_context_mod
from . import package_reader as package_reader_mod
from . import v1_open_ask as v1_open_ask_mod
from .agent_context import (
    FORBIDDEN_CONTEXT_PHRASES,
    build_agent_context,
    context_to_json,
    render_markdown,
)
from .agent_review_retrieval import AGENT_REVIEW_TRACK_NAME
from .constants import TOOL_NAME, TOOL_VERSION
from .evidence_bundle_retrieval import EVIDENCE_BUNDLE_TRACK_NAME
from .lock import lock_status
from .security.limits import DEFAULT_LIMITS, Limits
from .security.operation_lock import OperationLockError, operation_lock
from .security.paths import PathSecurityError, resolve_in_package

__all__ = [
    "V1PackageIndexError",
    "V1_PACKAGE_INDEX_SCHEMA_ID",
    "V1_PACKAGE_INDEX_SCHEMA_VERSION",
    "INDEX_V1_DIR",
    "INDEX_ARTIFACT_FILENAMES",
    "INDEX_MANIFEST_FILENAME",
    "INDEX_CAVEATS",
    "IndexArtifact",
    "V1PackageIndexResult",
    "build_v1_package_index",
    "write_v1_package_index",
    "refresh_v1_package_index",
    "load_v1_index_manifest",
    "v1_index_exists",
    "assert_index_no_forbidden_language",
    "IndexArtifactStatus",
    "IndexVerificationBlock",
    "IndexVerificationResult",
    "VERIFY_CAVEATS",
    "verify_v1_package_index",
    "render_v1_package_index_verification",
]

V1_PACKAGE_INDEX_SCHEMA_ID = "clulatent.v1_package_index.v0"
V1_PACKAGE_INDEX_SCHEMA_VERSION = "0.1.0"

# The package-relative home of every built-in V1 index artifact. POSIX,
# relative, never absolute — it is written into the index manifest.
INDEX_V1_DIR = "index/v1"

# The three content artifacts (in stable write order) and the manifest
# that describes them. The manifest is written last, after the content
# artifacts have been written and hashed.
AGENT_CONTEXT_JSON_FILENAME = "agent_context.json"
AGENT_CONTEXT_MD_FILENAME = "agent_context.md"
ASK_PROMPT_MD_FILENAME = "ask_prompt.md"
INDEX_MANIFEST_FILENAME = "index_manifest.json"

INDEX_ARTIFACT_FILENAMES: tuple[str, ...] = (
    AGENT_CONTEXT_JSON_FILENAME,
    AGENT_CONTEXT_MD_FILENAME,
    ASK_PROMPT_MD_FILENAME,
)

_MEDIA_TYPES: dict[str, str] = {
    AGENT_CONTEXT_JSON_FILENAME: "application/json",
    AGENT_CONTEXT_MD_FILENAME: "text/markdown",
    ASK_PROMPT_MD_FILENAME: "text/markdown",
    INDEX_MANIFEST_FILENAME: "application/json",
}

# The load-bearing authored prose of the index manifest. Worded to avoid
# every banned semantic phrase; the guard runs over exactly these strings.
# The final caveat states plainly that the index is not a lock and makes
# no truth claim.
INDEX_CAVEATS: tuple[str, ...] = (
    "This built-in index re-packages candidate evidence only; it carries no semantic interpretation.",
    "No object, person, face, action, scene, speech, or purpose claim is made or implied here.",
    "The agent context and ask prompt describe what evidence exists, never what the evidence means.",
    "This index manifest is not a lock and not a certificate; it records artifact hashes at generation time only.",
    "The recorded hashes let an agent detect that the built-in index is stale relative to the package tracks.",
    "Everything referenced here needs human review before it is treated as canonical.",
)

# A downstream agent must never see this module emit a semantic claim. We
# reuse the agent-context banned phrases; the guard scans only the strings
# this module *authors* (the caveats and the manifest note), never opaque
# user-controlled data (a package id, a source filename, a track name).
INDEX_FORBIDDEN_PHRASES: tuple[str, ...] = FORBIDDEN_CONTEXT_PHRASES


class V1PackageIndexError(RuntimeError):
    """Raised when the V1 package index cannot be built, written, or read."""


@dataclass
class IndexArtifact:
    """One built-in index file, as recorded in the index manifest.

    `path` is package-relative and POSIX. `sha256` is the hex digest of
    the file's exact bytes, or `None` for the manifest's own entry (a file
    cannot record its own hash).
    """

    name: str
    path: str
    media_type: str
    size_bytes: int
    sha256: str | None


@dataclass
class V1PackageIndexResult:
    """Structured outcome of writing (or previewing) the V1 package index."""

    package_path: Path
    package_id: str
    generated_at: str
    schema_id: str
    artifacts: list[IndexArtifact]
    validation_valid: bool
    wrote: bool
    index_dir: str = INDEX_V1_DIR
    written_paths: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Build (read-only): render the four artifacts and the manifest in memory
# ---------------------------------------------------------------------------


def assert_index_no_forbidden_language(authored: list[str]) -> None:
    """Raise `V1PackageIndexError` if authored prose contains a banned phrase.

    Scans only the strings this module authors (the index caveats and the
    manifest note) — never opaque, user-controlled data like a source
    filename, a package id, or a track name — so legitimate input never
    trips the guard, while a future edit that smuggles a semantic claim
    into the authored prose fails loudly.
    """
    for text in authored:
        if not isinstance(text, str):
            continue
        lowered = text.lower()
        for phrase in INDEX_FORBIDDEN_PHRASES:
            if phrase in lowered:
                raise V1PackageIndexError(
                    "authored package-index prose contains forbidden "
                    f"semantic phrase {phrase!r}: {text!r}"
                )


def _evidence_ids(context: dict[str, Any]) -> tuple[list[str], list[str]]:
    evidence = context.get("evidence", {})
    bundle_ids = [b["id"] for b in evidence.get("evidence_bundles", []) if "id" in b]
    review_ids = [r["id"] for r in evidence.get("agent_reviews", []) if "id" in r]
    return bundle_ids, review_ids


def build_v1_package_index(
    package_path: Path | str,
    *,
    strict: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Render the four V1 index artifacts in memory, without writing.

    Opens `package_path` once through the read-only agent-context path and
    returns a dict with the artifact text (`agent_context_json`,
    `agent_context_md`, `ask_prompt_md`) and the fully-built
    `index_manifest` dict (which already carries the content artifacts'
    hashes). Read-only: mutates nothing and writes no file. Raises
    `V1PackageIndexError` if the package cannot be opened or the authored
    prose fails the guard.
    """
    try:
        context = build_agent_context(
            package_path,
            strict=strict,
            limits=limits,
            tool_name=tool_name,
            tool_version=tool_version,
        )
    except agent_context_mod.AgentContextError as exc:
        raise V1PackageIndexError(f"could not build agent context: {exc}") from exc

    agent_context_json = context_to_json(context)
    agent_context_md = render_markdown(context)
    ask_prompt_md = v1_open_ask_mod.render_ask_prompt(context)

    contents: dict[str, str] = {
        AGENT_CONTEXT_JSON_FILENAME: agent_context_json,
        AGENT_CONTEXT_MD_FILENAME: agent_context_md,
        ASK_PROMPT_MD_FILENAME: ask_prompt_md,
    }

    pkg = context["package"]
    validation = context["validation"]
    bundle_ids, review_ids = _evidence_ids(context)
    when = generated_at or datetime.now(timezone.utc).isoformat()

    # Content artifacts, hashed. The manifest's own entry is added below
    # with a null hash (a file cannot record its own digest).
    artifacts: list[dict[str, Any]] = []
    for filename in INDEX_ARTIFACT_FILENAMES:
        data = contents[filename].encode("utf-8")
        artifacts.append(
            {
                "name": filename,
                "path": f"{INDEX_V1_DIR}/{filename}",
                "media_type": _MEDIA_TYPES[filename],
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    artifacts.append(
        {
            "name": INDEX_MANIFEST_FILENAME,
            "path": f"{INDEX_V1_DIR}/{INDEX_MANIFEST_FILENAME}",
            "media_type": _MEDIA_TYPES[INDEX_MANIFEST_FILENAME],
            "size_bytes": None,
            "sha256": None,
            "self": True,
        }
    )

    index_manifest: dict[str, Any] = {
        "schema_id": V1_PACKAGE_INDEX_SCHEMA_ID,
        "schema_version": V1_PACKAGE_INDEX_SCHEMA_VERSION,
        "package_id": pkg["package_id"],
        "generated_at": when,
        "generated_by": {"tool": tool_name, "version": tool_version},
        "source_agent_context_schema": {
            "schema_id": context.get("schema_id"),
            "schema_version": context.get("schema_version"),
        },
        "source_validation": {
            "valid": bool(validation["valid"]),
            "error_count": validation["error_count"],
            "warning_count": validation["warning_count"],
        },
        "package": {
            "duration_ms": pkg.get("duration_ms"),
            "status": pkg.get("status"),
            "clulatent_version": pkg.get("clulatent_version"),
            "track_counts": dict(context.get("event_counts", {})),
        },
        "evidence": {
            "evidence_bundle_ids": bundle_ids,
            "agent_review_ids": review_ids,
        },
        "artifacts": artifacts,
        "caveats": list(INDEX_CAVEATS),
        "note": (
            "This is a package index artifact, not a lock and not a "
            "certificate. It does not certify what the package's evidence "
            "means; it only points an agent at the package's "
            "candidate-evidence context and records artifact hashes at "
            "generation time."
        ),
    }

    # Guard only the strings this module authors.
    assert_index_no_forbidden_language(list(INDEX_CAVEATS) + [index_manifest["note"]])

    index_manifest_json = json.dumps(index_manifest, indent=2, sort_keys=False) + "\n"

    return {
        "package_id": pkg["package_id"],
        "generated_at": when,
        "validation_valid": bool(validation["valid"]),
        "contents": {
            AGENT_CONTEXT_JSON_FILENAME: agent_context_json,
            AGENT_CONTEXT_MD_FILENAME: agent_context_md,
            ASK_PROMPT_MD_FILENAME: ask_prompt_md,
            INDEX_MANIFEST_FILENAME: index_manifest_json,
        },
        "index_manifest": index_manifest,
    }


# ---------------------------------------------------------------------------
# Write (mutating): persist the four artifacts under index/v1/
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (temp file + fsync + os.replace)."""
    data = text.encode("utf-8")
    tmp_path = path.parent / f".{path.name}.tmp-{os.getpid()}"
    fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _ensure_index_v1_dir(package_path: Path) -> Path:
    """Create `index/v1/` under the package, refusing symlinked segments.

    `index/` and `index/v1/` are fixed tool-owned paths (not
    manifest-declared), but a malicious package could pre-plant either as a
    symlink, so we refuse a symlinked segment before creating anything.
    """
    root = Path(package_path).resolve(strict=True)
    index_dir = root / "index"
    if index_dir.is_symlink():
        raise V1PackageIndexError(f"refusing to use a symlinked index directory: {index_dir}")
    if index_dir.exists() and not index_dir.is_dir():
        raise V1PackageIndexError(f"index path exists and is not a directory: {index_dir}")
    if not index_dir.exists():
        index_dir.mkdir(parents=False)

    v1_dir = index_dir / "v1"
    if v1_dir.is_symlink():
        raise V1PackageIndexError(f"refusing to use a symlinked index/v1 directory: {v1_dir}")
    if v1_dir.exists() and not v1_dir.is_dir():
        raise V1PackageIndexError(f"index/v1 path exists and is not a directory: {v1_dir}")
    if not v1_dir.exists():
        v1_dir.mkdir(parents=False)
    return v1_dir


def write_v1_package_index(
    package_path: Path | str,
    *,
    force_stale_lock: bool = False,
    strict: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
) -> V1PackageIndexResult:
    """Build and write the four `index/v1/` artifacts into the package.

    Refuses (raises `V1PackageIndexError`, writes nothing) if the package
    does not exist, carries a valid integrity lock, or the operation lock
    cannot be acquired. Writes no receipt and generates no evidence track;
    the only mutation is the four index artifacts (overwritten atomically
    if already present). Honors the integrity lock exactly like the
    evidence writers.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise V1PackageIndexError(f"Package not found: {package_path}")

    status, _report = lock_status(package_path, limits=limits)
    if status == "locked":
        raise V1PackageIndexError(
            "package has a valid integrity lock (lock/package.lock.json); "
            "the built-in V1 index refuses to write against a locked package"
        )

    # Build the artifacts before acquiring the write lock — the build is
    # read-only and can fail (e.g. an invalid package) without needing the
    # lock at all.
    built = build_v1_package_index(
        package_path,
        strict=strict,
        limits=limits,
        tool_name=tool_name,
        tool_version=tool_version,
    )
    contents: dict[str, str] = built["contents"]

    try:
        with operation_lock(
            package_path,
            operation="v1_package_index",
            force_stale=force_stale_lock,
            limits=limits,
        ):
            _ensure_index_v1_dir(package_path)
            written: list[str] = []
            # Content artifacts first, then the manifest that describes them.
            write_order = list(INDEX_ARTIFACT_FILENAMES) + [INDEX_MANIFEST_FILENAME]
            for filename in write_order:
                relative = f"{INDEX_V1_DIR}/{filename}"
                try:
                    target = resolve_in_package(
                        package_path, relative, field_name=relative, for_write=True
                    )
                except PathSecurityError as exc:
                    raise V1PackageIndexError(str(exc)) from exc
                _atomic_write_text(target, contents[filename])
                written.append(relative)
    except OperationLockError as exc:
        raise V1PackageIndexError(str(exc)) from exc

    artifacts = [IndexArtifact(**_artifact_kwargs(a)) for a in built["index_manifest"]["artifacts"]]
    return V1PackageIndexResult(
        package_path=package_path,
        package_id=built["package_id"],
        generated_at=built["generated_at"],
        schema_id=V1_PACKAGE_INDEX_SCHEMA_ID,
        artifacts=artifacts,
        validation_valid=built["validation_valid"],
        wrote=True,
        written_paths=written,
    )


def _artifact_kwargs(entry: dict[str, Any]) -> dict[str, Any]:
    """Project an artifact manifest entry onto `IndexArtifact` fields."""
    return {
        "name": entry["name"],
        "path": entry["path"],
        "media_type": entry["media_type"],
        "size_bytes": entry.get("size_bytes") or 0,
        "sha256": entry.get("sha256"),
    }


def refresh_v1_package_index(
    package_path: Path | str,
    *,
    force_stale_lock: bool = False,
    strict: bool = True,
    limits: Limits = DEFAULT_LIMITS,
    tool_name: str = TOOL_NAME,
    tool_version: str = TOOL_VERSION,
) -> V1PackageIndexResult:
    """Regenerate the built-in V1 index after the package's tracks changed.

    A thin, explicitly-named wrapper over `write_v1_package_index`: it
    re-renders the agent context from the *current* package state and
    overwrites the four `index/v1/` artifacts. It generates no new
    evidence (no visual-change/changed-region/evidence-bundle/agent-review
    write) — it only refreshes the index.
    """
    return write_v1_package_index(
        package_path,
        force_stale_lock=force_stale_lock,
        strict=strict,
        limits=limits,
        tool_name=tool_name,
        tool_version=tool_version,
    )


# ---------------------------------------------------------------------------
# Read (read-only): inspect an existing built-in index
# ---------------------------------------------------------------------------


def v1_index_exists(package_path: Path | str) -> bool:
    """True if the package carries a built-in V1 index manifest."""
    manifest_path = Path(package_path) / INDEX_V1_DIR / INDEX_MANIFEST_FILENAME
    return manifest_path.is_file() and not manifest_path.is_symlink()


def load_v1_index_manifest(
    package_path: Path | str, *, limits: Limits = DEFAULT_LIMITS
) -> dict[str, Any]:
    """Read and parse `index/v1/index_manifest.json` (read-only).

    Raises `V1PackageIndexError` if the manifest is missing, is a symlink,
    exceeds the manifest size bound, or is not valid JSON. Mutates
    nothing and writes no receipt.
    """
    package_path = Path(package_path)
    relative = f"{INDEX_V1_DIR}/{INDEX_MANIFEST_FILENAME}"
    try:
        manifest_path = resolve_in_package(package_path, relative, field_name=relative)
    except PathSecurityError as exc:
        raise V1PackageIndexError(str(exc)) from exc
    if not manifest_path.is_file():
        raise V1PackageIndexError(
            f"no built-in V1 index found: {relative} does not exist in {package_path}"
        )
    from .security.jsonl import JsonlLimitError, read_bytes_bounded

    try:
        raw = read_bytes_bounded(
            manifest_path, max_bytes=limits.max_manifest_bytes, field_name=relative
        )
    except JsonlLimitError as exc:
        raise V1PackageIndexError(f"{relative} exceeds size limits: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise V1PackageIndexError(f"{relative} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise V1PackageIndexError(f"{relative} is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# Verify (read-only, V1): does the built-in index still describe
# the package it lives in?
# ---------------------------------------------------------------------------

# "Is the package's V1 self-description present, internally consistent, and
# fresh relative to the evidence package it describes?" This is a
# verification, not a repair: it never writes the package index, the
# agent-read index, a receipt, a track, the manifest, or the lock. It only
# opens the package (read-only) and the two independent index manifests
# already on disk, and reports what it finds.
VERIFY_CAVEATS: tuple[str, ...] = (
    "This verification reports presence, internal consistency, and freshness "
    "of the built-in V1 index; it makes no claim about what the package's "
    "evidence means.",
)

_STATUS_SEVERITY: dict[str, int] = {
    "PASS": 0,
    "FRESH": 0,
    "UNKNOWN": 1,
    "STALE": 2,
    "INVALID": 3,
    "MISSING": 4,
}


def _elevate_status(current: str, candidate: str) -> str:
    """Return whichever of `current`/`candidate` is the more severe status."""
    return candidate if _STATUS_SEVERITY[candidate] > _STATUS_SEVERITY[current] else current


@dataclass
class IndexArtifactStatus:
    """The status of one on-disk index artifact file."""

    name: str
    status: str  # PASS | MISSING | INVALID
    path: str | None = None
    detail: str | None = None


@dataclass
class IndexVerificationBlock:
    """The verification outcome for one index (package-index or agent-read)."""

    label: str
    status: str  # FRESH | STALE | MISSING | INVALID | UNKNOWN
    artifacts: list[IndexArtifactStatus] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


@dataclass
class IndexVerificationResult:
    """Structured outcome of verifying a package's built-in V1 indexes."""

    package_path: Path
    package_id: str
    checked_at: str
    package_index: IndexVerificationBlock
    agent_read_index: IndexVerificationBlock
    overall_status: str
    remediation: list[str] = field(default_factory=list)


def _verify_package_index_block(
    package_path: Path,
    reader: Any,
    current_package_id: str,
    *,
    limits: Limits,
) -> IndexVerificationBlock:
    findings: list[str] = []
    artifacts: list[IndexArtifactStatus] = []
    worst = "PASS"

    if not v1_index_exists(package_path):
        findings.append(f"no built-in V1 index found under {INDEX_V1_DIR}/")
        return IndexVerificationBlock(
            label="package_index", status="MISSING", artifacts=artifacts, findings=findings
        )

    try:
        manifest = load_v1_index_manifest(package_path, limits=limits)
    except V1PackageIndexError as exc:
        findings.append(f"index manifest could not be read: {exc}")
        return IndexVerificationBlock(
            label="package_index", status="INVALID", artifacts=artifacts, findings=findings
        )

    recorded_package_id = manifest.get("package_id")
    if recorded_package_id != current_package_id:
        findings.append(
            f"package id mismatch: index manifest records {recorded_package_id!r}, "
            f"package is currently {current_package_id!r}"
        )
        worst = _elevate_status(worst, "INVALID")

    for entry in manifest.get("artifacts", []):
        name = entry.get("name")
        if name == INDEX_MANIFEST_FILENAME:
            continue  # the manifest cannot record its own hash
        relative = entry.get("path", f"{INDEX_V1_DIR}/{name}")
        try:
            target = resolve_in_package(package_path, relative, field_name=relative)
        except PathSecurityError as exc:
            artifacts.append(
                IndexArtifactStatus(name=name, status="INVALID", path=relative, detail=str(exc))
            )
            worst = _elevate_status(worst, "INVALID")
            continue
        if not target.is_file() or target.is_symlink():
            artifacts.append(
                IndexArtifactStatus(
                    name=name, status="MISSING", path=relative, detail="artifact file not found"
                )
            )
            worst = _elevate_status(worst, "INVALID")
            continue
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        expected_sha = entry.get("sha256")
        if expected_sha and actual_sha != expected_sha:
            artifacts.append(
                IndexArtifactStatus(
                    name=name,
                    status="INVALID",
                    path=relative,
                    detail="on-disk content does not match the hash recorded in the index manifest",
                )
            )
            worst = _elevate_status(worst, "INVALID")
        else:
            artifacts.append(IndexArtifactStatus(name=name, status="PASS", path=relative))

    current_context: dict[str, Any] | None = None
    try:
        current_context = build_agent_context(package_path, strict=True, limits=limits)
    except agent_context_mod.AgentContextError as exc:
        findings.append(f"could not recompute current package state to check freshness: {exc}")
        worst = _elevate_status(worst, "UNKNOWN")

    if current_context is not None:
        current_counts = dict(current_context.get("event_counts", {}))
        recorded_counts = dict(manifest.get("package", {}).get("track_counts", {}))
        if current_counts != recorded_counts:
            findings.append(
                "track counts changed since the index was generated: "
                f"recorded {recorded_counts}, current {current_counts}"
            )
            worst = _elevate_status(worst, "STALE")

        current_bundle_ids, current_review_ids = _evidence_ids(current_context)
        recorded_bundle_ids = list(manifest.get("evidence", {}).get("evidence_bundle_ids", []))
        recorded_review_ids = list(manifest.get("evidence", {}).get("agent_review_ids", []))

        for bundle_id in recorded_bundle_ids:
            if reader.get_event(bundle_id, track_names=[EVIDENCE_BUNDLE_TRACK_NAME]) is None:
                findings.append(f"evidence bundle reference no longer resolves: {bundle_id}")
                worst = _elevate_status(worst, "INVALID")
        for review_id in recorded_review_ids:
            if reader.get_event(review_id, track_names=[AGENT_REVIEW_TRACK_NAME]) is None:
                findings.append(f"agent review reference no longer resolves: {review_id}")
                worst = _elevate_status(worst, "INVALID")

        if sorted(current_bundle_ids) != sorted(recorded_bundle_ids):
            findings.append(
                "evidence bundle ids changed since the index was generated: "
                f"recorded {sorted(recorded_bundle_ids)}, current {sorted(current_bundle_ids)}"
            )
            worst = _elevate_status(worst, "STALE")
        if sorted(current_review_ids) != sorted(recorded_review_ids):
            findings.append(
                "agent review ids changed since the index was generated: "
                f"recorded {sorted(recorded_review_ids)}, current {sorted(current_review_ids)}"
            )
            worst = _elevate_status(worst, "STALE")

    if worst == "PASS":
        worst = "FRESH"

    return IndexVerificationBlock(
        label="package_index", status=worst, artifacts=artifacts, findings=findings
    )


def _verify_agent_read_block(
    package_path: Path,
    reader: Any,
    current_package_id: str,
    *,
    limits: Limits,
) -> IndexVerificationBlock:
    from . import v1_agent_read_model as v1_agent_read_model_mod

    findings: list[str] = []
    artifacts: list[IndexArtifactStatus] = []
    worst = "PASS"

    manifest_path = Path(package_path) / v1_agent_read_model_mod.INDEX_DIR / v1_agent_read_model_mod.MANIFEST
    if not manifest_path.is_file() or manifest_path.is_symlink():
        findings.append(f"no agent-read index found under {v1_agent_read_model_mod.INDEX_DIR}/")
        return IndexVerificationBlock(
            label="agent_read_index", status="MISSING", artifacts=artifacts, findings=findings
        )

    try:
        manifest = v1_agent_read_model_mod.load_agent_read_manifest(package_path)
    except v1_agent_read_model_mod.AgentReadModelError as exc:
        findings.append(f"agent-read manifest could not be read: {exc}")
        return IndexVerificationBlock(
            label="agent_read_index", status="INVALID", artifacts=artifacts, findings=findings
        )

    recorded_package_id = manifest.get("package_id")
    if recorded_package_id != current_package_id:
        findings.append(
            f"package id mismatch: agent-read manifest records {recorded_package_id!r}, "
            f"package is currently {current_package_id!r}"
        )
        worst = _elevate_status(worst, "INVALID")

    for entry in manifest.get("artifacts", []):
        name = entry.get("name")
        relative = entry.get("path", f"{v1_agent_read_model_mod.INDEX_DIR}/{name}")
        target = Path(package_path) / relative
        if not target.is_file() or target.is_symlink():
            artifacts.append(
                IndexArtifactStatus(
                    name=name, status="MISSING", path=relative, detail="artifact file not found"
                )
            )
            worst = _elevate_status(worst, "INVALID")
            continue
        actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        expected_sha = entry.get("sha256")
        if expected_sha and actual_sha != expected_sha:
            artifacts.append(
                IndexArtifactStatus(
                    name=name,
                    status="INVALID",
                    path=relative,
                    detail="on-disk content does not match the hash recorded in the agent-read manifest",
                )
            )
            worst = _elevate_status(worst, "INVALID")
        else:
            artifacts.append(IndexArtifactStatus(name=name, status="PASS", path=relative))

    current_model: dict[str, Any] | None = None
    try:
        current_model = v1_agent_read_model_mod.build_agent_read_model(package_path, budget="full")
    except v1_agent_read_model_mod.AgentReadModelError as exc:
        findings.append(f"could not recompute current package state to check freshness: {exc}")
        worst = _elevate_status(worst, "UNKNOWN")

    if current_model is not None:
        current_counts = dict(current_model.get("track_counts", {}))
        recorded_counts = dict(manifest.get("track_counts", {}))
        if current_counts != recorded_counts:
            findings.append(
                "track counts changed since the agent-read index was generated: "
                f"recorded {recorded_counts}, current {current_counts}"
            )
            worst = _elevate_status(worst, "STALE")

        current_bundle_ids = sorted(current_model.get("evidence_bundle_ids", []))
        current_review_ids = sorted(current_model.get("agent_review_ids", []))
        recorded_bundle_ids = sorted(manifest.get("evidence_bundle_ids", []))
        recorded_review_ids = sorted(manifest.get("agent_review_ids", []))

        for bundle_id in recorded_bundle_ids:
            if reader.get_event(bundle_id, track_names=[EVIDENCE_BUNDLE_TRACK_NAME]) is None:
                findings.append(f"evidence bundle reference no longer resolves: {bundle_id}")
                worst = _elevate_status(worst, "INVALID")
        for review_id in recorded_review_ids:
            if reader.get_event(review_id, track_names=[AGENT_REVIEW_TRACK_NAME]) is None:
                findings.append(f"agent review reference no longer resolves: {review_id}")
                worst = _elevate_status(worst, "INVALID")

        if current_bundle_ids != recorded_bundle_ids:
            findings.append(
                "evidence bundle ids changed since the agent-read index was generated: "
                f"recorded {recorded_bundle_ids}, current {current_bundle_ids}"
            )
            worst = _elevate_status(worst, "STALE")
        if current_review_ids != recorded_review_ids:
            findings.append(
                "agent review ids changed since the agent-read index was generated: "
                f"recorded {recorded_review_ids}, current {current_review_ids}"
            )
            worst = _elevate_status(worst, "STALE")

    if worst == "PASS":
        worst = "FRESH"

    return IndexVerificationBlock(
        label="agent_read_index", status=worst, artifacts=artifacts, findings=findings
    )


def verify_v1_package_index(
    package_path: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> IndexVerificationResult:
    """Verify a package's built-in V1 indexes against its current state.

    Read-only: opens the package and the existing index manifests (Phase
    3.22 package-index and V1 agent-read index) and reports
    presence, internal consistency, and freshness for each. Never writes
    the package index, the agent-read index, a receipt, a track, the
    manifest, or the lock. Raises `V1PackageIndexError` only if the package
    itself cannot be found or opened at all; every other condition (a
    missing, stale, or invalid index) is reported in the returned result,
    not raised.
    """
    package_path = Path(package_path)
    if not package_path.exists() or not package_path.is_dir():
        raise V1PackageIndexError(f"Package not found: {package_path}")

    try:
        reader = package_reader_mod.open_package(package_path, limits=limits)
    except package_reader_mod.PackageReaderError as exc:
        raise V1PackageIndexError(f"could not open package: {exc}") from exc

    current_package_id = reader.package_id
    checked_at = datetime.now(timezone.utc).isoformat()

    assert_index_no_forbidden_language(list(VERIFY_CAVEATS))

    package_index_block = _verify_package_index_block(
        package_path, reader, current_package_id, limits=limits
    )
    agent_read_block = _verify_agent_read_block(
        package_path, reader, current_package_id, limits=limits
    )

    remediation: list[str] = []
    if package_index_block.status in ("MISSING", "STALE", "INVALID"):
        remediation.append(f"clulatent package-index refresh {package_path}")
    if agent_read_block.status in ("MISSING", "STALE", "INVALID"):
        remediation.append(f"clulatent agent-read write-index {package_path}")

    return IndexVerificationResult(
        package_path=package_path,
        package_id=current_package_id,
        checked_at=checked_at,
        package_index=package_index_block,
        agent_read_index=agent_read_block,
        overall_status=package_index_block.status,
        remediation=remediation,
    )


def render_v1_package_index_verification(result: IndexVerificationResult) -> str:
    """Render a compact, terminal-friendly summary of a verification result.

    Not guarded by `assert_index_no_forbidden_language`: this text
    interpolates opaque package data (ids, paths, findings that quote
    package-controlled ids) that must never be rewritten or censored for
    containing a substring of a banned phrase. Only the static
    `VERIFY_CAVEATS` text is guarded, at verification time.
    """
    lines: list[str] = []
    lines.append(f"V1 index verification for package {result.package_id}")
    lines.append(f"  checked at: {result.checked_at}")
    lines.append(f"  overall status: {result.overall_status}")
    lines.append(f"  package-index ({INDEX_V1_DIR}/): {result.package_index.status}")
    for artifact in result.package_index.artifacts:
        detail = f" — {artifact.detail}" if artifact.detail else ""
        lines.append(f"    {artifact.name}: {artifact.status}{detail}")
    for finding in result.package_index.findings:
        lines.append(f"    - {finding}")
    lines.append(f"  agent-read-index: {result.agent_read_index.status}")
    for artifact in result.agent_read_index.artifacts:
        detail = f" — {artifact.detail}" if artifact.detail else ""
        lines.append(f"    {artifact.name}: {artifact.status}{detail}")
    for finding in result.agent_read_index.findings:
        lines.append(f"    - {finding}")
    if result.remediation:
        lines.append("  remediation:")
        for command in result.remediation:
            lines.append(f"    {command}")
    else:
        lines.append("  remediation: none needed")
    lines.append("  caveats:")
    for caveat in VERIFY_CAVEATS:
        lines.append(f"    - {caveat}")
    return "\n".join(lines)
