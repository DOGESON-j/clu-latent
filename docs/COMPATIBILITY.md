# Compatibility

V1 readers accept the editable directory and portable archive forms. Unknown
future-safe tracks remain visible and safely readable without being promoted
to a known canonical lane. Optional capabilities may be absent.

The V1 format/profile identifiers are compatibility commitments. Public Python
APIs use semantic versioning; advanced undocumented helpers may change.

Supported Python targets are 3.10–3.14 on Linux, macOS, and Windows. Core tests
do not require FFmpeg. Media integration requires separately installed FFmpeg
and ffprobe. Platform claims are verified by the public CI matrix.
