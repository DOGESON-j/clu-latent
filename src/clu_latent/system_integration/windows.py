"""Phase 3.14: Windows system-integration adapter.

Real Windows `.clulatent` file-type registration (writing
`HKEY_CURRENT_USER\\Software\\Classes` entries) is not implemented in
this phase -- see
`docs/PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md` for why. This
adapter honestly reports that OS-level registration requires manual
setup on Windows rather than pretending to have registered anything.
"""

from __future__ import annotations

from .base import ManualRegistrationIntegration


class WindowsSystemIntegration(ManualRegistrationIntegration):
    platform_name = "Windows"
    mechanism = "Windows Registry file-type association (HKEY_CURRENT_USER\\Software\\Classes)"
