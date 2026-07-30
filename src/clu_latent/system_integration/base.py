"""Phase 3.14: structured system-integration abstractions.

This module defines the *shape* of an OS-level system-integration
adapter (`SystemIntegration`) and its structured results
(`RegistrationResult`, `RegistrationStatus`) so that per-platform
adapters (`macos.py`, `linux.py`, `windows.py`) never print anything
themselves -- they only return data. Rendering that data to a terminal
or to JSON is `presentation.py`'s job, not this module's.

Trust position, extended to system integration: **do not claim
something happened that did not happen.** `register()` and
`unregister()` must only ever report a step as `SUCCESS` if it
genuinely completed; every field here exists so a caller (human or
CLI) can distinguish "this was actually done" from "this was not
attempted" without guessing from prose.

`ManualRegistrationIntegration` is the shared, honest implementation
used by every platform adapter in this phase: real OS-level
`.clulatent` file-type registration (macOS Launch Services, Linux
xdg-mime, the Windows Registry) is not performed automatically here --
see `docs/PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md` for why.
Every platform adapter instead reports, honestly and structurally,
that automatic registration is not implemented and manual registration
is required. `status()` always and only reports `registered=False`
because nothing has ever registered anything. This is not a
placeholder that "will lie less" later -- it is the correct, safe
answer for this phase, and it satisfies the phase's canonical wording
rule that "registered" is only ever said once an adapter genuinely
confirms it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionOutcome(str, Enum):
    """The genuine outcome of one system-integration action or step."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    NOT_IMPLEMENTED = "not_implemented"


@dataclass
class ActionStep:
    """One granular step within a `register()`/`unregister()` action."""

    name: str
    outcome: ActionOutcome
    message: str
    changed_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome.value,
            "message": self.message,
            "changed_paths": list(self.changed_paths),
        }


@dataclass
class RegistrationResult:
    """The structured result of a `register()` or `unregister()` call.

    `outcome` is the single overall verdict; `steps` breaks that down
    granularly so a caller can see exactly which sub-actions succeeded,
    failed, were skipped, or were never implemented. `changed_paths`
    lists every filesystem/registry path this action genuinely wrote to
    -- empty whenever nothing was written, which today is always.
    """

    action: str  # "register" or "unregister"
    platform: str
    outcome: ActionOutcome
    message: str
    steps: list[ActionStep] = field(default_factory=list)
    changed_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.outcome == ActionOutcome.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "platform": self.platform,
            "outcome": self.outcome.value,
            "message": self.message,
            "steps": [step.to_dict() for step in self.steps],
            "changed_paths": list(self.changed_paths),
            "warnings": list(self.warnings),
        }


@dataclass
class RegistrationStatus:
    """The structured result of a `status()` call. Always read-only."""

    platform: str
    registered: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "registered": self.registered,
            "message": self.message,
            "details": dict(self.details),
            "warnings": list(self.warnings),
        }


class SystemIntegration(ABC):
    """OS-level `.clulatent` integration adapter for one platform.

    Implementations must never print, log to the console, or otherwise
    perform presentation -- they only return structured results. They
    must never mutate a `.clulatent` package, access the network, or
    claim a step succeeded unless it genuinely did.
    """

    platform_name: str

    @abstractmethod
    def register(self) -> RegistrationResult: ...

    @abstractmethod
    def unregister(self) -> RegistrationResult: ...

    @abstractmethod
    def status(self) -> RegistrationStatus: ...


class ManualRegistrationIntegration(SystemIntegration):
    """Shared, honest behaviour for a platform with no automatic registration yet.

    Every concrete platform adapter in this phase (`macos.py`,
    `linux.py`, `windows.py`) subclasses this and only sets
    `platform_name` and `mechanism` -- the actual OS mechanism that a
    *future* phase would use (e.g. "macOS Launch Services file-type
    registration"). Until that mechanism is implemented, `register()`
    and `unregister()` both report `ActionOutcome.NOT_IMPLEMENTED` and
    change nothing, and `status()` always reports `registered=False`.
    This is deliberate, not a stub oversight: it is the only honest
    answer this phase can give, per the "do not pretend success" rule.
    """

    platform_name: str = "unknown"
    mechanism: str = "OS-level file-type registration"

    def status(self) -> RegistrationStatus:
        return RegistrationStatus(
            platform=self.platform_name,
            registered=False,
            message="System registration: not registered",
            details={
                "mechanism": self.mechanism,
                "note": (
                    "No OS-level .clulatent file association has been "
                    "registered by this tool."
                ),
            },
        )

    def register(self) -> RegistrationResult:
        step = ActionStep(
            name="register_file_association",
            outcome=ActionOutcome.NOT_IMPLEMENTED,
            message=(
                f"Automatic {self.mechanism} is not implemented on "
                f"{self.platform_name} in this phase; manual registration "
                "required."
            ),
        )
        return RegistrationResult(
            action="register",
            platform=self.platform_name,
            outcome=ActionOutcome.NOT_IMPLEMENTED,
            message=(
                "System registration: not registered. Automatic OS-level "
                f".clulatent registration is not implemented on "
                f"{self.platform_name} yet; manual registration required."
            ),
            steps=[step],
        )

    def unregister(self) -> RegistrationResult:
        step = ActionStep(
            name="unregister_file_association",
            outcome=ActionOutcome.NOT_IMPLEMENTED,
            message=(
                f"Automatic {self.mechanism} removal is not implemented on "
                f"{self.platform_name} in this phase; nothing to unregister."
            ),
        )
        return RegistrationResult(
            action="unregister",
            platform=self.platform_name,
            outcome=ActionOutcome.NOT_IMPLEMENTED,
            message=(
                "System unregistration is not implemented on "
                f"{self.platform_name} yet; nothing was changed."
            ),
            steps=[step],
        )


class UnsupportedSystemIntegration(ManualRegistrationIntegration):
    """Fallback adapter for a platform `get_system_integration` does not recognize.

    Behaves identically to `ManualRegistrationIntegration` -- it never
    crashes and never claims success -- but its messages make clear the
    platform itself was not recognized, not merely "not implemented yet".
    """

    def __init__(self, *, system_name: str) -> None:
        self.platform_name = system_name or "unknown"
        self.mechanism = "OS-level file-type registration (platform not recognized)"
