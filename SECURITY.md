# Security policy

Security reports for supported releases should be submitted privately through
GitHub Security Advisories for `DOGESON-j/clu-latent`. Do not open a public
issue containing secrets, private media, or an exploit payload.

V1 receives security fixes for archive extraction, path containment, bounded
parsing, subprocess invocation, and package integrity. Reports should include
the version, platform, minimal synthetic reproduction, and observed impact.

Never attach customer media, credentials, tokens, or personal data. CLULatent
does not require cloud credentials and does not collect telemetry.

## Public/private boundary

This public repository is an independently released package and protocol. It
must not contain credentials, private GRAFT or CLU repository details, personal
computer paths, private business data, or generated state from THE-GRID.

The public repository does not provide inbound access to a developer machine.
GitHub Actions run on GitHub-hosted runners; this project does not use a
self-hosted runner. Any private system may consume CLULatent through its public
CLI, package format, or Python API, but CLULatent receives no credential or
network route back into that private system.

The `Public Safety Guard` workflow scans tracked files on pushes, pull requests,
and a weekly schedule. It checks for common token formats, private keys,
personal home paths, references to private GRAFT repositories, unsafe tracked
credential files, self-hosted runners, `pull_request_target`, and broad workflow
write permissions. This is a defense-in-depth check, not a guarantee that a
secret can never be committed.

If a real credential is ever committed, deleting it in a later commit is not
enough. Revoke or rotate it immediately, then remove it from Git history and
release artifacts as appropriate.
