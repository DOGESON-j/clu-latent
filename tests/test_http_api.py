from __future__ import annotations

import importlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient


def _module(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("CLULATENT_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLULATENT_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("CLULATENT_API_KEY", raising=False)
    import clu_latent.http_api as api

    return importlib.reload(api)


def test_health_is_public(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    client = TestClient(api.app)
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["profile"] == "clulatent.profile.v1"


def test_protected_endpoint_refuses_unconfigured_auth(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    client = TestClient(api.app)
    response = client.get("/v1/capabilities")
    assert response.status_code == 503


def test_bearer_key_unlocks_protected_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    api = _module(monkeypatch, tmp_path)
    monkeypatch.setenv("CLULATENT_API_KEY", "test-secret")
    client = TestClient(api.app)
    response = client.get(
        "/v1/capabilities", headers={"Authorization": "Bearer test-secret"}
    )
    assert response.status_code == 200
    assert response.json()["semantic_claims"] is False
