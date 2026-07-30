# PyPI Trusted Publisher setup

Create the `clu-latent` project/pending publisher on PyPI with:

- owner: `DOGESON-j`
- repository: `clu-latent`
- workflow: `release.yml`
- environment: `pypi`

In GitHub, create a protected environment named `pypi`, then set repository
variable `PYPI_TRUSTED_PUBLISHING` to `true`. The workflow receives only the
short-lived GitHub OIDC identity (`id-token: write`); no API token or password
is stored.

Until account-side configuration is complete, GitHub Releases still succeed
and the PyPI job is explicitly skipped.
