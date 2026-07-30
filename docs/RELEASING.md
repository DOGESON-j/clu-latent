# Releasing

1. Confirm a clean `main`, version metadata, changelog, and release notes.
2. Run focused tests, then the full suite once.
3. Build wheel/sdist, run `twine check`, inspect contents, and clean-install
   each distribution without source-tree `PYTHONPATH`.
4. Generate and verify the synthetic demo, checksums, conformance suite, and
   leak/security audits.
5. Tag `v1.0.0`. The release workflow rebuilds from the exact tag, smoke-tests
   both distributions, generates checksums, and creates the GitHub Release.
6. PyPI runs only when repository variable `PYPI_TRUSTED_PUBLISHING=true` and
   the protected `pypi` environment are configured.

Never store a PyPI token in the repository.
