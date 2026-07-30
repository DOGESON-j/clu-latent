"""The CLULatent V1 compatibility profile and format contract.

The V1 `.clulatent` format includes a manifest, tracks, media, receipts,
lock state, and two independent built-in indexes. This module states, as
one explicit and machine-readable artifact, what a package must satisfy
to truthfully claim compatibility with "CLULatent V1".

It answers exactly one question: *does this package satisfy the CLULatent
V1 compatibility profile?* It does **not** answer, and must never be read
as answering, "is this package's evidence semantically true?" A package
can be a fully V1-compatible, well-formed container of candidate evidence
while making no claim whatsoever about what a person, object, action, or
scene in the source media is. Profile compatibility is a structural and
self-description contract, not a semantic certification.

The contract is derived from the *frozen* V1 implementation, not invented:

- REQUIRED items are things the existing format already always demands
  (a schema-valid manifest, a resolvable package identity, a supported
  format version, safe package-relative paths, `PackageReader` and
  `validate.validate_package` compatibility, the fixed top-level package
  structure, and a fresh built-in V1 package index — see
  `v1_package_index.verify_v1_package_index`, reused here rather than
  reimplemented).
- CONDITIONAL items only apply when something optional is present: if the
  manifest declares a track, that track's schema identity must be sound;
  if the optional agent-read index is present, it must be
  internally valid, even though its absence is allowed.
- OPTIONAL capabilities (keyframes, audio/speech/semantic events, visual
  change/changed-region candidates, evidence bundles, agent review events,
  the agent-read index) may be entirely absent without affecting profile
  compatibility. Their absence is reported as a missing capability, never
  as a profile failure.

Boundaries:

- Read-only. `verify_v1_profile` never writes, refreshes an index,
  regenerates agent-read artifacts, writes a receipt, touches the lock, or
  runs FFmpeg/Pillow/a model/a network call.
- No semantic interpretation: no object, person, face, action, scene,
  speech, or intent claim. Only structural, format-level facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import package_reader as package_reader_mod
from . import v1_package_index as v1_package_index_mod
from .agent_context import FORBIDDEN_CONTEXT_PHRASES
from .constants import (
    CLULATENT_VERSION,
    EVENT_ENVELOPE_SCHEMA_ID,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    INDEX_DIR,
    RECEIPTS_DIR,
    SOURCES_DIR,
    TRACKS_DIR,
)
from .manifest import Manifest
from .security.limits import DEFAULT_LIMITS, Limits
from .security.paths import PathSecurityError, resolve_in_package

__all__ = [
    "V1ProfileError",
    "PROFILE_ID",
    "PROFILE_CONTRACT_VERSION",
    "PROFILE_CAVEATS",
    "SUPPORTED_CLULATENT_VERSIONS",
    "V1ProfileRequirement",
    "V1ProfileCapability",
    "V1ProfileContract",
    "V1ProfileCheckResult",
    "V1ProfileCapabilityResult",
    "V1ProfileVerificationResult",
    "get_v1_profile_contract",
    "assert_profile_no_forbidden_language",
    "verify_v1_profile",
    "render_v1_profile_contract",
    "render_v1_profile_verification",
]

# A stable, machine-readable identifier for this contract, plus its own
# version — deliberately distinct from `CLULATENT_VERSION` (the package
# format's own version): a future V1.1/V2 contract could tighten or relax
# what "compatible" means without the underlying package format changing,
# and vice versa.
PROFILE_ID = "clulatent.profile.v1"
PROFILE_CONTRACT_VERSION = "1.0"

# The set of `manifest.clulatent_version` values this contract currently
# recognizes as a supported V1 format version. V0 of this contract only
# claims compatibility with the one frozen format version that exists.
SUPPORTED_CLULATENT_VERSIONS: tuple[str, ...] = (CLULATENT_VERSION,)

# The load-bearing authored prose of the contract itself. Worded to avoid
# every banned semantic phrase; the guard runs over exactly these strings
# (plus each requirement/capability description), never over verification
# findings, which may interpolate opaque package data.
PROFILE_CAVEATS: tuple[str, ...] = (
    "CLULatent V1 compatibility certifies package/profile compatibility, "
    "not semantic truth about media content.",
    "No object, person, face, action, scene, speech, or purpose claim is "
    "made or implied by a COMPATIBLE result.",
    "A missing optional capability never makes a package incompatible; it "
    "is reported as an absent capability only.",
    "Everything referenced here needs human review before it is treated "
    "as canonical.",
)

PROFILE_FORBIDDEN_PHRASES: tuple[str, ...] = FORBIDDEN_CONTEXT_PHRASES


class V1ProfileError(RuntimeError):
    """Raised only when `package_path` itself cannot be opened as a package.

    Every other condition a package might be in — missing manifest,
    malformed manifest, unsafe declared path, failed validation, stale
    required index — is reported as an `INCOMPATIBLE` (or `UNKNOWN`)
    verification result, never raised.
    """


# ---------------------------------------------------------------------------
# The contract itself (structured, not only prose)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class V1ProfileRequirement:
    """One REQUIRED or CONDITIONAL item of the V1 compatibility contract."""

    key: str
    label: str
    category: str  # "REQUIRED" | "CONDITIONAL"
    description: str


@dataclass(frozen=True)
class V1ProfileCapability:
    """One OPTIONAL capability the contract describes but never requires."""

    key: str
    label: str
    description: str


@dataclass(frozen=True)
class V1ProfileContract:
    """The full, structured CLULatent V1 compatibility profile."""

    profile_id: str
    contract_version: str
    requirements: list[V1ProfileRequirement]
    capabilities: list[V1ProfileCapability]
    caveats: list[str]


def _required_items() -> list[V1ProfileRequirement]:
    return [
        V1ProfileRequirement(
            key="manifest",
            label="manifest",
            category="REQUIRED",
            description=(
                "manifest.json exists, is not a symlink, is within the manifest "
                "size bound, and schema-validates against the V1 Manifest model."
            ),
        ),
        V1ProfileRequirement(
            key="package_identity",
            label="package identity",
            category="REQUIRED",
            description="manifest.json declares a non-empty package_id.",
        ),
        V1ProfileRequirement(
            key="format_version",
            label="supported format version",
            category="REQUIRED",
            description=(
                "manifest.clulatent_version is one of the format versions this "
                "contract version recognizes as V1."
            ),
        ),
        V1ProfileRequirement(
            key="reader_compatibility",
            label="reader compatibility",
            category="REQUIRED",
            description="The package opens cleanly through PackageReader.open.",
        ),
        V1ProfileRequirement(
            key="safe_paths",
            label="safe package-relative paths",
            category="REQUIRED",
            description=(
                "Every manifest-declared path (source, each declared track, the "
                "keyframes directory, the index file, the receipts file) resolves "
                "to a location inside the package with no symlink escape."
            ),
        ),
        V1ProfileRequirement(
            key="validator_compatibility",
            label="validator compatibility",
            category="REQUIRED",
            description=(
                "validate.validate_package reports the package valid: schema, "
                "path, source-hash, track, and receipt checks all pass."
            ),
        ),
        V1ProfileRequirement(
            key="package_structure",
            label="required package structure",
            category="REQUIRED",
            description=(
                f"The fixed top-level layout is present: manifest.json, "
                f"{SOURCES_DIR}/, {TRACKS_DIR}/, {RECEIPTS_DIR}/, and {INDEX_DIR}/."
            ),
        ),
        V1ProfileRequirement(
            key="v1_package_index",
            label="V1 package index",
            category="REQUIRED",
            description=(
                "The built-in V1 package index under index/v1/ is present and "
                "fresh relative to the package's current tracks and evidence "
                "references."
            ),
        ),
        V1ProfileRequirement(
            key="declared_tracks",
            label="declared track structural contract",
            category="CONDITIONAL",
            description=(
                "Not applicable when the manifest declares no tracks. When one "
                "or more tracks are declared, each must carry the shared event "
                "envelope schema id/version this format uses."
            ),
        ),
        V1ProfileRequirement(
            key="agent_read_index",
            label="agent-read index (if present)",
            category="CONDITIONAL",
            description=(
                "Not applicable when the optional agent-read index "
                "is absent. When present, it must be internally valid and fresh "
                "relative to the package's current state."
            ),
        ),
    ]


_OPTIONAL_TRACK_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("keyframes", "keyframes"),
    ("audio_events", "audio events"),
    ("speech_events", "speech events"),
    ("semantic_events", "semantic events"),
    ("visual_change_candidates", "visual change candidates"),
    ("changed_region_candidates", "changed region candidates"),
    ("evidence_bundles", "evidence bundles"),
    ("agent_review_events", "agent review events"),
)


def _capability_items() -> list[V1ProfileCapability]:
    items = [
        V1ProfileCapability(
            key=key,
            label=label,
            description=f"The optional '{key}' track is declared in manifest.json.",
        )
        for key, label in _OPTIONAL_TRACK_CAPABILITIES
    ]
    items.append(
        V1ProfileCapability(
            key="agent_read_index",
            label="agent-read index",
            description=(
                "The optional token-budgeted agent-read index is present "
                "under index/."
            ),
        )
    )
    return items


def get_v1_profile_contract() -> V1ProfileContract:
    """Return the structured CLULatent V1 compatibility profile.

    Read-only, no package involved: this describes the contract itself,
    independent of any specific `.clulatent` package.
    """
    return V1ProfileContract(
        profile_id=PROFILE_ID,
        contract_version=PROFILE_CONTRACT_VERSION,
        requirements=_required_items(),
        capabilities=_capability_items(),
        caveats=list(PROFILE_CAVEATS),
    )


def assert_profile_no_forbidden_language(authored: list[str]) -> None:
    """Raise `V1ProfileError` if authored contract prose contains a banned phrase.

    Scans only the strings this module authors (requirement/capability
    descriptions and the profile caveats) — never a verification result's
    findings or details, which interpolate opaque, package-controlled data
    (ids, paths, validator error text) that must never be rewritten or
    censored for containing a substring of a banned phrase.
    """
    for text in authored:
        if not isinstance(text, str):
            continue
        lowered = text.lower()
        for phrase in PROFILE_FORBIDDEN_PHRASES:
            if phrase in lowered:
                raise V1ProfileError(
                    "authored V1 profile prose contains forbidden semantic "
                    f"phrase {phrase!r}: {text!r}"
                )


# ---------------------------------------------------------------------------
# Verification (read-only): does a specific package satisfy the contract?
# ---------------------------------------------------------------------------


@dataclass
class V1ProfileCheckResult:
    """The outcome of one REQUIRED or CONDITIONAL contract item."""

    key: str
    label: str
    category: str  # "REQUIRED" | "CONDITIONAL"
    status: str  # PASS | FAIL | MISSING | UNKNOWN | NOT_APPLICABLE
    detail: str | None = None


@dataclass
class V1ProfileCapabilityResult:
    """The presence/absence of one OPTIONAL capability."""

    key: str
    label: str
    status: str  # AVAILABLE | MISSING


@dataclass
class V1ProfileVerificationResult:
    """Structured outcome of checking a package against the V1 profile."""

    package_path: Path
    package_id: str
    checked_at: str
    compatibility: str  # COMPATIBLE | INCOMPATIBLE | UNKNOWN
    checks: list[V1ProfileCheckResult] = field(default_factory=list)
    capabilities: list[V1ProfileCapabilityResult] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


_INCOMPATIBLE_STATUSES = frozenset({"FAIL", "MISSING"})
_UNKNOWN_STATUSES = frozenset({"UNKNOWN"})


def _check_safe_paths(package_path: Path, manifest: Manifest) -> tuple[str, str | None]:
    findings: list[str] = []
    candidates: list[tuple[str, str]] = [
        ("source.stored_path", manifest.source.stored_path),
        ("sources/source.sha256", "sources/source.sha256"),
        ("media.keyframes.dir", manifest.media.keyframes.dir),
        ("index.file", manifest.index.file),
        ("receipts.file", manifest.receipts.file),
    ]
    for track in manifest.tracks:
        candidates.append((f"tracks[{track.name}].file", track.file))

    for field_name, relative in candidates:
        try:
            resolve_in_package(package_path, relative, field_name=field_name)
        except PathSecurityError as exc:
            findings.append(f"{field_name}: {exc}")

    if findings:
        return "FAIL", "; ".join(findings)
    return "PASS", None


def _check_package_structure(
    package_path: Path, manifest: Manifest | None
) -> tuple[str, str | None]:
    missing: list[str] = []
    if not (package_path / "manifest.json").is_file():
        missing.append("manifest.json")
    required_directories = [SOURCES_DIR, RECEIPTS_DIR, INDEX_DIR]
    if manifest is not None and manifest.tracks:
        required_directories.append(TRACKS_DIR)
    for name in required_directories:
        candidate = package_path / name
        if not candidate.is_dir() or candidate.is_symlink():
            missing.append(f"{name}/")
    if missing:
        return "FAIL", f"missing required package structure: {', '.join(missing)}"
    return "PASS", None


def _check_declared_tracks(manifest: Manifest) -> tuple[str, str | None]:
    findings: list[str] = []
    for track in manifest.tracks:
        if track.schema_id != EVENT_ENVELOPE_SCHEMA_ID or track.schema_version != EVENT_ENVELOPE_SCHEMA_VERSION:
            findings.append(
                f"track {track.name!r} declares schema {track.schema_id!r} "
                f"v{track.schema_version!r}, expected {EVENT_ENVELOPE_SCHEMA_ID!r} "
                f"v{EVENT_ENVELOPE_SCHEMA_VERSION!r}"
            )
    if findings:
        return "FAIL", "; ".join(findings)
    return "PASS", None


_INDEX_STATUS_TO_CHECK: dict[str, str] = {
    "FRESH": "PASS",
    "STALE": "FAIL",
    "INVALID": "FAIL",
    "MISSING": "MISSING",
    "UNKNOWN": "UNKNOWN",
}


def verify_v1_profile(
    package_path: Path | str,
    *,
    limits: Limits = DEFAULT_LIMITS,
) -> V1ProfileVerificationResult:
    """Check whether a package satisfies the CLULatent V1 compatibility profile.

    Read-only: never writes the package index, the agent-read index, a
    receipt, a track, the manifest, or the lock, and runs no evidence
    generation, media analysis, or model/network call. Raises
    `V1ProfileError` only if `package_path` does not exist or is not a
    directory; every other condition — a missing or malformed manifest, an
    unsafe declared path, a failed validation, a stale required index — is
    reported in the returned result as `INCOMPATIBLE`, never raised.
    """
    package_path = Path(package_path)
    if package_path.is_file():
        from .archive import ArchiveError, resolved_package

        try:
            with resolved_package(package_path) as extracted:
                result = verify_v1_profile(extracted, limits=limits)
        except (ArchiveError, OSError) as exc:
            raise V1ProfileError(f"portable archive could not be opened: {exc}") from exc
        result.package_path = package_path
        return result
    if not package_path.exists() or not package_path.is_dir():
        raise V1ProfileError(f"Package not found: {package_path}")

    checked_at = datetime.now(timezone.utc).isoformat()
    checks: list[V1ProfileCheckResult] = []
    capabilities: list[V1ProfileCapabilityResult] = []
    package_id = "unknown"

    # -- manifest -----------------------------------------------------
    manifest_path = package_path / "manifest.json"
    manifest: Manifest | None = None
    if not manifest_path.is_file() or manifest_path.is_symlink():
        checks.append(
            V1ProfileCheckResult(
                key="manifest",
                label="manifest",
                category="REQUIRED",
                status="MISSING",
                detail="manifest.json not found in package",
            )
        )
    else:
        try:
            manifest = Manifest.from_json_file(manifest_path, limits=limits)
            checks.append(
                V1ProfileCheckResult(
                    key="manifest", label="manifest", category="REQUIRED", status="PASS"
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported, not re-raised
            checks.append(
                V1ProfileCheckResult(
                    key="manifest",
                    label="manifest",
                    category="REQUIRED",
                    status="FAIL",
                    detail=f"manifest.json could not be read/validated: {exc}",
                )
            )

    # -- package identity / format version -----------------------------
    if manifest is not None:
        package_id = manifest.package_id or "unknown"
        checks.append(
            V1ProfileCheckResult(
                key="package_identity",
                label="package identity",
                category="REQUIRED",
                status="PASS" if manifest.package_id else "FAIL",
                detail=None if manifest.package_id else "manifest.package_id is empty",
            )
        )
        supported = manifest.clulatent_version in SUPPORTED_CLULATENT_VERSIONS
        checks.append(
            V1ProfileCheckResult(
                key="format_version",
                label="supported format version",
                category="REQUIRED",
                status="PASS" if supported else "FAIL",
                detail=(
                    None
                    if supported
                    else (
                        f"manifest.clulatent_version {manifest.clulatent_version!r} is not "
                        f"in the supported set {list(SUPPORTED_CLULATENT_VERSIONS)}"
                    )
                ),
            )
        )
    else:
        for key, label in (
            ("package_identity", "package identity"),
            ("format_version", "supported format version"),
        ):
            checks.append(
                V1ProfileCheckResult(
                    key=key,
                    label=label,
                    category="REQUIRED",
                    status="NOT_APPLICABLE",
                    detail="manifest unavailable",
                )
            )

    # -- reader compatibility -------------------------------------------
    try:
        reader = package_reader_mod.open_package(package_path, limits=limits)
        checks.append(
            V1ProfileCheckResult(
                key="reader_compatibility",
                label="reader compatibility",
                category="REQUIRED",
                status="PASS",
            )
        )
        package_id = reader.package_id
    except package_reader_mod.PackageReaderError as exc:
        checks.append(
            V1ProfileCheckResult(
                key="reader_compatibility",
                label="reader compatibility",
                category="REQUIRED",
                status="FAIL",
                detail=str(exc),
            )
        )

    # -- safe declared paths ---------------------------------------------
    if manifest is not None:
        status, detail = _check_safe_paths(package_path, manifest)
        checks.append(
            V1ProfileCheckResult(
                key="safe_paths",
                label="safe package-relative paths",
                category="REQUIRED",
                status=status,
                detail=detail,
            )
        )
    else:
        checks.append(
            V1ProfileCheckResult(
                key="safe_paths",
                label="safe package-relative paths",
                category="REQUIRED",
                status="NOT_APPLICABLE",
                detail="manifest unavailable",
            )
        )

    # -- validator compatibility (delegates to validate.validate_package) --
    from .validate import validate_package

    try:
        report = validate_package(package_path, limits=limits)
        checks.append(
            V1ProfileCheckResult(
                key="validator_compatibility",
                label="validator compatibility",
                category="REQUIRED",
                status="PASS" if report.valid else "FAIL",
                detail=None if report.valid else "; ".join(report.errors[:5]),
            )
        )
    except Exception as exc:  # noqa: BLE001 - reported, not re-raised
        checks.append(
            V1ProfileCheckResult(
                key="validator_compatibility",
                label="validator compatibility",
                category="REQUIRED",
                status="UNKNOWN",
                detail=f"validator raised unexpectedly: {exc}",
            )
        )

    # -- required package structure --------------------------------------
    status, detail = _check_package_structure(package_path, manifest)
    checks.append(
        V1ProfileCheckResult(
            key="package_structure",
            label="required package structure",
            category="REQUIRED",
            status=status,
            detail=detail,
        )
    )

    # -- V1 package index (required) + agent-read index (conditional) ----
    # Reuse the package-index verifier rather than duplicating its
    # freshness and consistency rules.
    index_result = None
    try:
        index_result = v1_package_index_mod.verify_v1_package_index(package_path, limits=limits)
    except v1_package_index_mod.V1PackageIndexError as exc:
        checks.append(
            V1ProfileCheckResult(
                key="v1_package_index",
                label="V1 package index",
                category="REQUIRED",
                status="FAIL",
                detail=str(exc),
            )
        )
    except Exception as exc:  # noqa: BLE001 - UNKNOWN must not become PASS or escape
        checks.append(
            V1ProfileCheckResult(
                key="v1_package_index",
                label="V1 package index",
                category="REQUIRED",
                status="UNKNOWN",
                detail=f"package index verification raised unexpectedly: {exc}",
            )
        )

    if index_result is not None:
        pkg_status = index_result.package_index.status
        mapped = _INDEX_STATUS_TO_CHECK[pkg_status]
        checks.append(
            V1ProfileCheckResult(
                key="v1_package_index",
                label="V1 package index",
                category="REQUIRED",
                status=mapped,
                detail=None if mapped == "PASS" else f"package index status: {pkg_status}",
            )
        )

        ar_status = index_result.agent_read_index.status
        if ar_status == "MISSING":
            checks.append(
                V1ProfileCheckResult(
                    key="agent_read_index",
                    label="agent-read index (if present)",
                    category="CONDITIONAL",
                    status="NOT_APPLICABLE",
                    detail="agent-read index not present (optional)",
                )
            )
        else:
            mapped_ar = _INDEX_STATUS_TO_CHECK[ar_status]
            checks.append(
                V1ProfileCheckResult(
                    key="agent_read_index",
                    label="agent-read index (if present)",
                    category="CONDITIONAL",
                    status=mapped_ar,
                    detail=None if mapped_ar == "PASS" else f"agent-read index status: {ar_status}",
                )
            )
        capabilities.append(
            V1ProfileCapabilityResult(
                key="agent_read_index",
                label="agent-read index",
                status="MISSING" if ar_status == "MISSING" else "AVAILABLE",
            )
        )
    else:
        checks.append(
            V1ProfileCheckResult(
                key="agent_read_index",
                label="agent-read index (if present)",
                category="CONDITIONAL",
                status="UNKNOWN",
                detail="could not determine agent-read index status",
            )
        )
        capabilities.append(
            V1ProfileCapabilityResult(key="agent_read_index", label="agent-read index", status="MISSING")
        )

    # -- declared tracks (conditional) + optional track capabilities -----
    declared_names: set[str] = set()
    if manifest is not None:
        declared_names = {t.name for t in manifest.tracks}
        if not manifest.tracks:
            checks.append(
                V1ProfileCheckResult(
                    key="declared_tracks",
                    label="declared track structural contract",
                    category="CONDITIONAL",
                    status="NOT_APPLICABLE",
                    detail="no tracks declared",
                )
            )
        else:
            status, detail = _check_declared_tracks(manifest)
            checks.append(
                V1ProfileCheckResult(
                    key="declared_tracks",
                    label="declared track structural contract",
                    category="CONDITIONAL",
                    status=status,
                    detail=detail,
                )
            )
    else:
        checks.append(
            V1ProfileCheckResult(
                key="declared_tracks",
                label="declared track structural contract",
                category="CONDITIONAL",
                status="NOT_APPLICABLE",
                detail="manifest unavailable",
            )
        )

    for key, label in _OPTIONAL_TRACK_CAPABILITIES:
        capabilities.append(
            V1ProfileCapabilityResult(
                key=key,
                label=label,
                status="AVAILABLE" if key in declared_names else "MISSING",
            )
        )

    compatibility = _compute_compatibility(checks)

    return V1ProfileVerificationResult(
        package_path=package_path,
        package_id=package_id,
        checked_at=checked_at,
        compatibility=compatibility,
        checks=checks,
        capabilities=capabilities,
        caveats=list(PROFILE_CAVEATS),
    )


def _compute_compatibility(checks: list[V1ProfileCheckResult]) -> str:
    if any(c.status in _INCOMPATIBLE_STATUSES for c in checks):
        return "INCOMPATIBLE"
    if any(c.status in _UNKNOWN_STATUSES for c in checks):
        return "UNKNOWN"
    return "COMPATIBLE"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_v1_profile_contract(contract: V1ProfileContract) -> str:
    """Render the contract itself (not a verification) for `profile inspect v1`."""
    authored = [r.description for r in contract.requirements]
    authored += [c.description for c in contract.capabilities]
    # The directive requires the exact compatibility disclaimer containing
    # "semantic truth"; it is an explicit negation, not a semantic claim.
    authored += [c for c in contract.caveats if c != PROFILE_CAVEATS[0]]
    assert_profile_no_forbidden_language(authored)

    lines: list[str] = []
    lines.append(f"CLULatent V1 Profile Contract ({contract.profile_id}, v{contract.contract_version})")
    lines.append("")
    lines.append("Required contract:")
    for req in contract.requirements:
        if req.category != "REQUIRED":
            continue
        lines.append(f"  {req.label}")
        lines.append(f"    {req.description}")
    lines.append("")
    lines.append("Conditional contract:")
    for req in contract.requirements:
        if req.category != "CONDITIONAL":
            continue
        lines.append(f"  {req.label}")
        lines.append(f"    {req.description}")
    lines.append("")
    lines.append("Optional capabilities:")
    for cap in contract.capabilities:
        lines.append(f"  {cap.label}")
        lines.append(f"    {cap.description}")
    lines.append("")
    lines.append("Caveats:")
    for caveat in contract.caveats:
        lines.append(f"  - {caveat}")
    return "\n".join(lines)


def render_v1_profile_verification(result: V1ProfileVerificationResult) -> str:
    """Render a compact, terminal-friendly summary of a verification result.

    Not guarded by `assert_profile_no_forbidden_language`: this text
    interpolates opaque package data (ids, paths, validator error text,
    check details) that must never be rewritten or censored for
    containing a substring of a banned phrase. Only the static contract
    prose is guarded, at contract-render time.
    """
    lines: list[str] = []
    lines.append("CLULatent V1 Profile")
    lines.append(f"  package:       {result.package_id}")
    lines.append(f"  checked at:    {result.checked_at}")
    lines.append("")
    lines.append(f"Compatibility: {result.compatibility}")
    lines.append("")
    lines.append("Required contract:")
    for check in result.checks:
        if check.category != "REQUIRED":
            continue
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"  {check.label:<28} {check.status}{detail}")
    lines.append("")
    lines.append("Conditional contract:")
    for check in result.checks:
        if check.category != "CONDITIONAL":
            continue
        detail = f" — {check.detail}" if check.detail else ""
        lines.append(f"  {check.label:<28} {check.status}{detail}")
    lines.append("")
    lines.append("Optional capabilities:")
    for cap in result.capabilities:
        lines.append(f"  {cap.label:<28} {cap.status}")
    lines.append("")
    lines.append("Caveats:")
    for caveat in result.caveats:
        lines.append(f"  - {caveat}")
    return "\n".join(lines)
