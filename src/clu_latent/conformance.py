"""Public, deterministic, offline CLULatent V1 conformance runner."""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from . import conformance_fixtures as fixtures_mod
from .archive import (
    ARCHIVE_MARKER,
    ARCHIVE_MEDIA_TYPE,
    ArchiveError,
    pack_package,
    verify_archive,
)
from .v1_package_index import INDEX_V1_DIR, write_v1_package_index
from .v1_profile import V1ProfileError, verify_v1_profile

__all__ = [
    "ConformanceError",
    "ConformanceFixtureResult",
    "ConformanceRunResult",
    "bundled_fixture_manifest",
    "run_conformance",
    "render_conformance",
]


class ConformanceError(RuntimeError):
    """Raised when the conformance definition itself cannot be loaded."""


@dataclass(frozen=True)
class ConformanceFixtureResult:
    name: str
    expected: str
    actual: str
    passed: bool
    requirement: str
    detail: str | None = None


@dataclass(frozen=True)
class ConformanceRunResult:
    fixtures: tuple[ConformanceFixtureResult, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.fixtures)

    @property
    def passed_count(self) -> int:
        return sum(item.passed for item in self.fixtures)


def bundled_fixture_manifest() -> dict[str, Any]:
    resource = files("clu_latent").joinpath("data/conformance/fixture_manifest.json")
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceError(f"bundled fixture manifest is unavailable: {exc}") from exc


def _load_external_manifest(root: Path) -> dict[str, Any]:
    path = root / "fixture_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceError(f"could not load fixture manifest {path}: {exc}") from exc


def _write_required_index(package: Path) -> None:
    write_v1_package_index(package)


def _build_bundled(name: str, target: Path) -> Path:
    base_name = {
        "valid_full": "valid_keyframes_audio",
        "valid_without_agent_read": "valid_keyframes_audio",
        "valid_unknown_future_track": "valid_unknown_track",
        "invalid_package_id_mismatch": "valid_keyframes_audio",
        "invalid_stale_required_index": "valid_keyframes_audio",
        "invalid_broken_index_artifact_hash": "valid_keyframes_audio",
        "invalid_evidence_reference": "invalid_dangling_evidence_reference",
    }.get(name, name)
    package = fixtures_mod.build_fixture(base_name, target)

    if name.startswith("valid_"):
        _write_required_index(package)
    elif name in {
        "invalid_package_id_mismatch",
        "invalid_stale_required_index",
        "invalid_broken_index_artifact_hash",
    }:
        _write_required_index(package)
        manifest_path = package / INDEX_V1_DIR / "index_manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if name == "invalid_package_id_mismatch":
            data["package_id"] = "mismatched-package-id"
        elif name == "invalid_stale_required_index":
            data["package"]["track_counts"] = {"keyframes": 999}
        else:
            data["artifacts"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return package


def _result_for_directory(
    name: str,
    package: Path,
    expected: str,
    requirement: str,
) -> ConformanceFixtureResult:
    try:
        verification = verify_v1_profile(package)
        actual = verification.compatibility
        failed_checks = [
            check.key for check in verification.checks if check.status in {"FAIL", "MISSING"}
        ]
        detail = ", ".join(failed_checks) or None
    except V1ProfileError as exc:
        actual = "INCOMPATIBLE"
        detail = str(exc)
    return ConformanceFixtureResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=actual == expected,
        requirement=requirement,
        detail=detail,
    )


def _archive_marker() -> str:
    return json.dumps(
        {"format": "1.0", "media_type": ARCHIVE_MEDIA_TYPE},
        sort_keys=True,
        separators=(",", ":"),
    )


def _build_archive_fixture(name: str, target: Path, work: Path) -> Path:
    if name == "valid_portable_archive":
        package = _build_bundled("valid_full", work / "archive-source.clulatent")
        pack_package(package, target)
        return target
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ARCHIVE_MARKER, _archive_marker())
        archive.writestr("manifest.json", "{}")
        if name == "invalid_archive_unsafe_member":
            archive.writestr("../escape", "rejected")
        elif name == "invalid_archive_limits":
            archive.writestr("tracks/compression-bomb.jsonl", b"0" * (1024 * 1024))
        else:
            raise ConformanceError(f"unknown archive fixture: {name}")
    return target


def _result_for_archive(
    name: str,
    archive: Path,
    expected: str,
    requirement: str,
) -> ConformanceFixtureResult:
    try:
        verify_archive(archive)
        actual = verify_v1_profile(archive).compatibility
        detail = None
    except (ArchiveError, V1ProfileError) as exc:
        actual = "INCOMPATIBLE"
        detail = str(exc)
    return ConformanceFixtureResult(
        name=name,
        expected=expected,
        actual=actual,
        passed=actual == expected,
        requirement=requirement,
        detail=detail,
    )


def run_conformance(fixtures: Path | str | None = None) -> ConformanceRunResult:
    """Run fixtures without network, FFmpeg, OCR, models, or external media."""
    external = Path(fixtures) if fixtures is not None else None
    manifest = _load_external_manifest(external) if external else bundled_fixture_manifest()
    definitions = manifest.get("fixtures")
    if not isinstance(definitions, list) or not definitions:
        raise ConformanceError("fixture manifest must contain a non-empty fixtures list")

    results: list[ConformanceFixtureResult] = []
    with tempfile.TemporaryDirectory(prefix="clulatent-conformance-") as temp:
        generated = Path(temp)
        for entry in definitions:
            if not isinstance(entry, dict):
                raise ConformanceError("fixture entries must be objects")
            name = str(entry.get("name", ""))
            expected = str(entry.get("expected", ""))
            requirement = str(entry.get("requirement", "profile"))
            kind = str(entry.get("kind", "directory"))
            if not name or expected not in {"COMPATIBLE", "INCOMPATIBLE"}:
                raise ConformanceError(f"invalid fixture definition: {entry!r}")
            category = "valid" if expected == "COMPATIBLE" else "invalid"
            if kind == "archive":
                archive = (
                    external / category / f"{name}.clulatent"
                    if external
                    else _build_archive_fixture(
                        name, generated / f"{name}.clulatent", generated / name
                    )
                )
                results.append(_result_for_archive(name, archive, expected, requirement))
                continue
            if kind != "directory":
                raise ConformanceError(f"unsupported fixture kind {kind!r}")
            if external:
                package = external / category / f"{name}.clulatent"
            else:
                package = _build_bundled(name, generated / f"{name}.clulatent")
            results.append(_result_for_directory(name, package, expected, requirement))
    return ConformanceRunResult(tuple(results))


def render_conformance(result: ConformanceRunResult) -> str:
    lines = []
    for item in result.fixtures:
        marker = "PASS" if item.passed else "FAIL"
        detail = f" ({item.detail})" if item.detail else ""
        lines.append(
            f"{marker} {item.name}: expected={item.expected} "
            f"actual={item.actual} requirement={item.requirement}{detail}"
        )
    lines.append(
        f"{'PASS' if result.passed else 'FAIL'} "
        f"{result.passed_count}/{len(result.fixtures)} fixtures"
    )
    return "\n".join(lines) + "\n"
