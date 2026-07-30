"""Phase 3.14: structured, read-only-by-default OS system-integration adapters.

`get_system_integration()` returns the `SystemIntegration` adapter for
the current platform (or an explicitly given `system_name`, used by
tests to exercise every branch without mocking `platform.system()`
globally). An unrecognized platform never crashes -- it returns
`UnsupportedSystemIntegration`, which behaves exactly like every other
adapter in this phase (honest "not implemented" results, `registered`
always `False` until something genuinely registers).

See `base.py` for why every adapter in this phase reports
`ActionOutcome.NOT_IMPLEMENTED` rather than performing real OS
mutations, and `docs/PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md`
for the full rationale.
"""

from __future__ import annotations

import platform as _platform_mod

from .base import (
    ActionOutcome,
    ActionStep,
    ManualRegistrationIntegration,
    RegistrationResult,
    RegistrationStatus,
    SystemIntegration,
    UnsupportedSystemIntegration,
)
from .linux import LinuxSystemIntegration
from .macos import MacOSSystemIntegration
from .windows import WindowsSystemIntegration

_ADAPTERS: dict[str, type[SystemIntegration]] = {
    "Darwin": MacOSSystemIntegration,
    "Linux": LinuxSystemIntegration,
    "Windows": WindowsSystemIntegration,
}


def get_system_integration(*, system_name: str | None = None) -> SystemIntegration:
    """Return the `SystemIntegration` adapter for `system_name` (default: `platform.system()`).

    Never raises for an unrecognized platform name -- it falls back to
    `UnsupportedSystemIntegration`, which reports honestly rather than
    guessing or crashing.
    """
    name = system_name if system_name is not None else _platform_mod.system()
    adapter_cls = _ADAPTERS.get(name)
    if adapter_cls is None:
        return UnsupportedSystemIntegration(system_name=name)
    return adapter_cls()


__all__ = [
    "ActionOutcome",
    "ActionStep",
    "ManualRegistrationIntegration",
    "RegistrationResult",
    "RegistrationStatus",
    "SystemIntegration",
    "UnsupportedSystemIntegration",
    "LinuxSystemIntegration",
    "MacOSSystemIntegration",
    "WindowsSystemIntegration",
    "get_system_integration",
]
