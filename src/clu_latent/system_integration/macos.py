"""Phase 3.14: macOS system-integration adapter.

Real macOS `.clulatent` file-type registration (Launch Services /
`LSHandlerRoleAll`-style entries, typically installed via a `duti`
call or an `.app` bundle's `Info.plist`) is not implemented in this
phase -- see `docs/PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md` for
why. This adapter honestly reports that OS-level registration requires
manual setup on macOS rather than pretending to have registered
anything.
"""

from __future__ import annotations

from .base import ManualRegistrationIntegration


class MacOSSystemIntegration(ManualRegistrationIntegration):
    platform_name = "Darwin"
    mechanism = "macOS Launch Services file-type registration (LSHandlerRoleAll)"
