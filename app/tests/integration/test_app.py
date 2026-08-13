from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError


class FailingConnection:
    async def __aenter__(self) -> None:
        raise SQLAlchemyError("database unavailable")

    async def __aexit__(self, *args: object) -> None:
        return None


class FailingEngine:
    def connect(self) -> FailingConnection:
        return FailingConnection()


def test_health_does_not_require_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v1_domain_endpoints_are_not_exposed(client: TestClient) -> None:
    for path in ("outlines", "drafts", "reviews", "jobs"):
        response = client.get(f"/api/v1/{path}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "http_404"


def test_openapi_contains_health_and_authentication_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/health" in paths
    assert "/ready" in paths
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/me" in paths
    assert "/api/v1/articles" in paths
    assert "/api/v1/articles/{article_id}" in paths
    assert {path for path in paths if path.startswith("/api/v1/")} == {
        "/api/v1/articles",
        "/api/v1/articles/{article_id}",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/auth/register",
    }

    security_schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"]["scheme"] == "bearer"
    assert paths["/api/v1/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]
    assert paths["/api/v1/articles"]["post"]["security"] == [{"HTTPBearer": []}]
    assert paths["/api/v1/articles"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "security" not in paths["/api/v1/auth/login"]["post"]
    assert "security" not in paths["/api/v1/auth/register"]["post"]


def test_readiness_returns_standard_error_when_database_is_unavailable(
    client: TestClient,
) -> None:
    application = cast(FastAPI, client.app)
    original_engine = application.state.engine
    application.state.engine = FailingEngine()
    try:
        response = client.get("/ready")
    finally:
        application.state.engine = original_engine

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "The database is unavailable",
        }
    }
