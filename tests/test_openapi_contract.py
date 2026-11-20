"""The public HTTP contract is OpenAPI 3.2.1 and never unauthenticated."""

from __future__ import annotations

from fastapi.testclient import TestClient

import machina.api.auth as auth
from machina.api.app import create_app


def test_openapi_document_is_pinned_and_docs_require_a_key(monkeypatch) -> None:
    monkeypatch.setattr(auth, "configured_key", lambda: "test-key")
    client = TestClient(create_app())

    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs").status_code == 401

    response = client.get("/openapi.json", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    document = response.json()
    assert document["openapi"] == "3.2.1"
    assert "/api/v1/sessions" in document["paths"]
    assert "/rpc" in document["paths"]
    assert "/a2a" in document["paths"]

    assert client.get("/docs", headers={"X-API-Key": "test-key"}).status_code == 200
    assert client.get("/redoc", headers={"X-API-Key": "test-key"}).status_code == 200
