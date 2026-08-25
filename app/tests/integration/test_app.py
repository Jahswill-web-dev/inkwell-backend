from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.main import create_app
from app.services.ai_service import BriefGenerator
from app.services.openrouter_ai import (
    OpenRouterBriefGenerator,
    OpenRouterOutlineGenerator,
    OpenRouterSectionInterviewGenerator,
    OpenRouterTalkingPointsGenerator,
)


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
    assert "/api/v1/articles/{article_id}/brief" in paths
    assert "/api/v1/articles/{article_id}/outline" in paths
    assert "/api/v1/articles/{article_id}/draft" in paths
    assert "/api/v1/articles/{article_id}/draft/sections/{section_id}/talking-points" in paths
    interview_base = "/api/v1/articles/{article_id}/draft/sections/{section_id}/interviews"
    assert interview_base in paths
    assert f"{interview_base}/latest" in paths
    assert f"{interview_base}/{{interview_id}}" in paths
    assert f"{interview_base}/{{interview_id}}/answers" in paths
    assert f"{interview_base}/{{interview_id}}/generate" in paths
    assert {path for path in paths if path.startswith("/api/v1/")} == {
        "/api/v1/articles",
        "/api/v1/articles/{article_id}",
        "/api/v1/articles/{article_id}/brief",
        "/api/v1/articles/{article_id}/outline",
        "/api/v1/articles/{article_id}/draft",
        "/api/v1/articles/{article_id}/draft/sections/{section_id}/talking-points",
        interview_base,
        f"{interview_base}/latest",
        f"{interview_base}/{{interview_id}}",
        f"{interview_base}/{{interview_id}}/answers",
        f"{interview_base}/{{interview_id}}/generate",
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


def test_openrouter_provider_wires_all_generators_and_closes_shared_client(
    settings: Settings,
) -> None:
    configured = settings.model_copy(
        update={
            "ai_provider": "openrouter",
            "openrouter_api_key": SecretStr("test-openrouter-key"),
        }
    )
    with TestClient(create_app(configured)) as client:
        application = cast(FastAPI, client.app)
        brief = cast(OpenRouterBriefGenerator, application.state.brief_generator)
        outline = cast(OpenRouterOutlineGenerator, application.state.outline_generator)
        points = cast(
            OpenRouterTalkingPointsGenerator,
            application.state.talking_points_generator,
        )
        interview = cast(
            OpenRouterSectionInterviewGenerator,
            application.state.section_interview_generator,
        )
        assert isinstance(brief, OpenRouterBriefGenerator)
        assert isinstance(outline, OpenRouterOutlineGenerator)
        assert isinstance(points, OpenRouterTalkingPointsGenerator)
        assert isinstance(interview, OpenRouterSectionInterviewGenerator)
        assert brief.client is outline.client is points.client is interview.client
        http_client = brief.client._http

    assert http_client.is_closed


def test_openrouter_without_key_keeps_generators_unconfigured(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"ai_provider": "openrouter", "openrouter_api_key": None}
    )
    with TestClient(create_app(configured)) as client:
        application = cast(FastAPI, client.app)
        assert application.state.brief_generator is None
        assert application.state.outline_generator is None
        assert application.state.talking_points_generator is None
        assert application.state.section_interview_generator is None


def test_injected_generator_takes_precedence_over_openrouter(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "ai_provider": "openrouter",
            "openrouter_api_key": SecretStr("test-openrouter-key"),
        }
    )
    injected = cast(BriefGenerator, object())
    with TestClient(create_app(configured, brief_generator=injected)) as client:
        application = cast(FastAPI, client.app)
        assert application.state.brief_generator is injected
        assert isinstance(application.state.outline_generator, OpenRouterOutlineGenerator)


def test_vertex_selection_does_not_activate_configured_openrouter(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "ai_provider": "vertex",
            "vertex_project_id": None,
            "openrouter_api_key": SecretStr("test-openrouter-key"),
        }
    )
    with TestClient(create_app(configured)) as client:
        application = cast(FastAPI, client.app)
        assert application.state.brief_generator is None
        assert application.state.outline_generator is None
        assert application.state.talking_points_generator is None
        assert application.state.section_interview_generator is None
