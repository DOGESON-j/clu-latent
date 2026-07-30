"""Phase 3.14: Linux system-integration adapter.

Real Linux `.clulatent` MIME-type/file-association registration
(freedesktop.org `xdg-mime` + a `.desktop` entry, updating
`~/.local/share/applications/mimeapps.list`) is not implemented in
this phase -- see
`docs/PHASE_3_14_LOCAL_INSTALL_SYSTEM_INTEGRATION.md` for why. This
adapter honestly reports that OS-level registration requires manual
setup on Linux rather than pretending to have registered anything.
"""

from __future__ import annotations

from .base import ManualRegistrationIntegration


class LinuxSystemIntegration(ManualRegistrationIntegration):
    platform_name = "Linux"
    mechanism = "freedesktop.org MIME type association (xdg-mime / mimeapps.list)"
