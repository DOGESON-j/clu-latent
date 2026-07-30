# Contributing

Use synthetic or clearly redistributable fixtures. Never commit private media,
credentials, personal data, or rights-unverified voice material.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[visual,dev]"
python -m pytest
```

Changes to the V1 profile, manifest rules, archive limits, safe extraction,
read-only behavior, or public APIs require focused tests and documentation.
Generated evidence must remain bounded, referenced, caveated, and
non-semantic. By contributing, you agree your contribution is licensed under
BSD-3-Clause.
