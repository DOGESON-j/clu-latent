# Portable archive V1

Media type: `application/vnd.clulatent+zip`

The portable `.clulatent` form is a deterministic ZIP transport containing the
editable package at archive root plus `clulatent-archive.json`. It is not
`.clubin`.

Packing uses stable member order, the ZIP epoch timestamp, normalized regular
file permissions, stored mode for common compressed media/images, and deflate
for text/metadata. It records no host path, user name, or machine metadata.

Default safety limits:

- members: 10,000
- one uncompressed file: 512 MiB
- total uncompressed bytes: 2 GiB
- compression ratio: 200:1
- nested archives: 0

Verification/extraction rejects traversal, absolute paths, Windows drive
escapes, symlink or special-file modes, encryption, unsupported compression,
case-folded duplicates, file/directory prefix conflicts, suspicious ratios,
and limit violations. Extraction never calls `ZipFile.extractall()`.
