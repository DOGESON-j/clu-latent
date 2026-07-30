"""CLULatent: a human-inspectable, machine-readable source package format
for agent-readable media/perception data.

CLULatent is NOT a video codec, and this package does NOT build CLUBIN
(the future compiled binary format). Phase 1 only.
"""

from .archive import (
    ArchiveError,
    ArchiveLimits,
    ArchiveVerification,
    pack_package,
    unpack_archive,
    verify_archive,
)
from .package_reader import EventRecord, PackageReader, ReceiptHandle, TrackHandle, open_package
from .v1_profile import (
    V1ProfileCapability,
    V1ProfileContract,
    V1ProfileRequirement,
    V1ProfileVerificationResult,
    get_v1_profile_contract,
    verify_v1_profile,
)
from .version import software_version

__version__ = software_version()

__all__ = [
    "ArchiveError",
    "ArchiveLimits",
    "ArchiveVerification",
    "EventRecord",
    "PackageReader",
    "ReceiptHandle",
    "TrackHandle",
    "V1ProfileCapability",
    "V1ProfileContract",
    "V1ProfileRequirement",
    "V1ProfileVerificationResult",
    "get_v1_profile_contract",
    "open_package",
    "pack_package",
    "unpack_archive",
    "verify_archive",
    "verify_v1_profile",
]
