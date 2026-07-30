# Security model

Every package and archive is untrusted input. Paths are package-relative and
resolved through containment and symlink checks. JSON/JSONL, manifests,
receipts, indexes, events, and archive extraction have bounded resource use.
External processes use argument arrays and never `shell=True`.

Read commands—validate, profile verify, package-index verify, archive verify,
package inspection, open, and agent-read summary/window—must not mutate the
input package or receipts. `open` writes generated files only to an external
output directory.

Locks provide tamper evidence, not authenticity. Hashes establish byte
identity, not semantic accuracy. Optional generated evidence remains candidate
evidence and requires human review.

The project stores no credentials, creates no cloud account, sends no
telemetry, and performs no model call in conformance or reader operations.
