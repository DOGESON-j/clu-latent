from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def _module(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CLULATENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLULATENT_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CLULATENT_API_KEY", raising=False)
    import clu_latent.http_api as api

    return importlib.reload(api)


def test_health_payload(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    payload = api.health()
    assert payload["status"] == "ok"
    assert payload["profile"] == "clulatent.profile.v1"


def test_expected_routes_are_registered(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    paths = {route.path for route in api.app.routes}
    assert "/v1/health" in paths
    assert "/v1/jobs" in paths
    assert "/v1/jobs/{job_id}" in paths
    assert "/v1/jobs/{job_id}/summary" in paths
    assert "/v1/jobs/{job_id}/window" in paths
    assert "/v1/jobs/{job_id}/artifact" in paths


def test_protected_access_refuses_unconfigured_auth(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        api._auth_required(None)
    assert exc_info.value.status_code == 503


def test_bearer_key_unlocks_protected_access(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    monkeypatch.setenv("CLULATENT_API_KEY", "test-secret")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-secret")
    assert api._auth_required(credentials) is None
    assert api.capabilities()["semantic_claims"] is False


def test_invalid_job_id_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        api._read_job("job_not-hex")
    assert exc_info.value.status_code == 404
