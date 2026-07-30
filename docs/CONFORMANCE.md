# Conformance

Run `clulatent conformance run`. The bundled suite is deterministic and
offline. It covers minimal/full/optional/future-safe valid directories, a valid
portable archive, malformed/missing manifests and tracks, traversal, package
identity and index freshness/hash failures, invalid references, unsafe archive
members, and archive limits.

Use `--fixtures PATH` for a compatible external fixture root. The command
prints fixture name, expected and actual compatibility, pass/fail, and the
contract requirement.

The suite does not call FFmpeg, OCR, a model, or the network and contains no
copyrighted/private media.
