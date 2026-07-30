"""CLULatent security kernel.

Every module in this package is a shared guard used by the rest of
CLULatent to treat media files, existing `.clulatent` packages,
manifests, JSONL tracks, and the derived SQLite index as untrusted
input. See the project security policy (docs/SECURITY.md) for the
full threat model.

Nothing outside this package should duplicate these checks — ingest,
validate, reindex, and query all route package-relative paths,
subprocess calls, bounded parsing, and SQLite access through here.
"""

from __future__ import annotations
