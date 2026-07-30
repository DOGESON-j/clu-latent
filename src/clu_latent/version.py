"""Single runtime source for the CLULatent software release version."""

from __future__ import annotations

import platform
from importlib.metadata import PackageNotFoundError, version

PROFILE_ID = "clulatent.profile.v1"
FORMAT_CONTRACT_VERSION = "1.0"
_SOURCE_FALLBACK = "1.0.0.dev0"


def software_version() -> str:
    """Return installed distribution metadata, with a source checkout fallback."""
    try:
        return version("clu-latent")
    except PackageNotFoundError:
        return _SOURCE_FALLBACK


def version_report() -> str:
    return (
        f"clulatent {software_version()}\n"
        f"profile {PROFILE_ID}\n"
        f"Python {platform.python_version()}"
    )
