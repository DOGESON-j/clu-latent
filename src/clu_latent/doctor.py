"""Local capability checks for ``clulatent doctor`` and ``welcome``.

Read-only, no network, no package mutation. Reports whether the core
CLULatent surfaces (the CLI itself, the package reader, the validator,
keyframe retrieval) are usable in *this* environment, plus the current
OS system-integration status (`system_integration.get_system_integration`).
Each check is a genuine probe (an import), not a hardcoded "True" --
a broken or partial install (e.g. a missing/incompatible dependency)
is reported as unavailable rather than silently assumed to work.
"""

from __future__ import annotations

import platform
import shutil
import sys
import tempfile
import webbrowser
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

from .constants import TOOL_NAME, TOOL_VERSION
from .system_integration import RegistrationStatus, get_system_integration


@dataclass
class CapabilityCheck:
    name: str
    available: bool
    detail: str
    level: str = "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "detail": self.detail,
            "level": self.level,
        }


@dataclass
class DoctorReport:
    tool_name: str
    tool_version: str
    checks: list[CapabilityCheck] = field(default_factory=list)
    system_status: RegistrationStatus | None = None

    @property
    def all_available(self) -> bool:
        return all(check.level != "FAIL" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "checks": [check.to_dict() for check in self.checks],
            "system_status": self.system_status.to_dict() if self.system_status else None,
        }


def _check_package_reader() -> CapabilityCheck:
    try:
        from . import manifest as _manifest  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return CapabilityCheck("Reader", False, f"import failed: {exc}")
    return CapabilityCheck("Reader", True, "manifest.py importable")


def _check_validator() -> CapabilityCheck:
    try:
        from . import validate as _validate  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return CapabilityCheck("Validator", False, f"import failed: {exc}")
    return CapabilityCheck("Validator", True, "validate.py importable")


def _check_keyframe_retrieval() -> CapabilityCheck:
    try:
        from . import keyframe_retrieval as _keyframe_retrieval  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return CapabilityCheck("Keyframe retrieval", False, f"import failed: {exc}")
    return CapabilityCheck(
        "Keyframe retrieval", True, "keyframe_retrieval.py importable"
    )


def _check_python() -> CapabilityCheck:
    supported = (3, 10) <= sys.version_info[:2] <= (3, 14)
    return CapabilityCheck(
        "Python",
        supported,
        f"{platform.python_version()} ({'supported' if supported else 'unsupported'})",
        "PASS" if supported else "FAIL",
    )


def _check_executable(name: str) -> CapabilityCheck:
    path = shutil.which(name)
    return CapabilityCheck(
        name,
        path is not None,
        "available" if path else "not found; required only for media build operations",
        "PASS" if path else "WARNING",
    )


def _check_pillow() -> CapabilityCheck:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return CapabilityCheck(
            "Pillow visual extra",
            False,
            "not installed; optional visual lanes unavailable",
            "WARNING",
        )
    return CapabilityCheck("Pillow visual extra", True, "available")


def _check_temp_writable() -> CapabilityCheck:
    try:
        with tempfile.NamedTemporaryFile(prefix="clulatent-doctor-", delete=True):
            pass
    except OSError as exc:
        return CapabilityCheck("Temporary directory", False, f"not writable: {exc}", "FAIL")
    return CapabilityCheck("Temporary directory", True, f"writable: {Path(tempfile.gettempdir())}")


def _check_browser() -> CapabilityCheck:
    try:
        webbrowser.get()
    except webbrowser.Error:
        return CapabilityCheck(
            "Browser opener",
            False,
            "no registered opener; informational only",
            "WARNING",
        )
    return CapabilityCheck("Browser opener", True, "registered; informational only")


def _check_package_data() -> CapabilityCheck:
    manifest = files("clu_latent").joinpath("data/conformance/fixture_manifest.json")
    try:
        available = manifest.is_file()
    except OSError:
        available = False
    return CapabilityCheck(
        "Conformance fixtures",
        available,
        "bundled fixture manifest available" if available else "bundled fixture manifest missing",
        "PASS" if available else "FAIL",
    )


def run_doctor_checks(*, system_name: str | None = None) -> DoctorReport:
    """Run every local capability check and return a structured report.

    `system_name` is forwarded to `get_system_integration` (used by
    tests to exercise a specific platform branch deterministically).
    Never raises for an unavailable capability -- that is reported as
    `available=False`, not an exception.
    """
    checks = [
        CapabilityCheck("CLI", True, f"{TOOL_NAME} {TOOL_VERSION} running"),
        _check_python(),
        _check_package_reader(),
        _check_validator(),
        _check_keyframe_retrieval(),
        _check_executable("ffmpeg"),
        _check_executable("ffprobe"),
        _check_pillow(),
        _check_temp_writable(),
        _check_browser(),
        _check_package_data(),
    ]
    system_status = get_system_integration(system_name=system_name).status()
    return DoctorReport(
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        checks=checks,
        system_status=system_status,
    )
