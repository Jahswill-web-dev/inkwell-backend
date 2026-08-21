from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.config import Settings
from app.db.models.article import ARTICLE_GOALS, Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft
from app.db.models.article_outline import ArticleOutline
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.schemas.brief import GeneratedBrief
from app.schemas.outline import GeneratedOutline
from app.services.ai_service import (
    BriefGenerationResult,
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    BriefSource,
    OutlineGenerationResult,
    OutlineSource,
)
from app.tests.integration.test_database import alembic_config, require_test_url

pytestmark = pytest.mark.database


class FakeBriefGenerator:
    def __init__(self) -> None:
        self.calls: list[BriefSource] = []

    async def generate(self, source: BriefSource) -> BriefGenerationResult:
        self.calls.append(source)
        return BriefGenerationResult(
            brief=GeneratedBrief.model_validate(
                {
                    "summary": f"A brief for {source.working_title}.",
                    "core_angle": "Make publishing repeatable",
                    "audience_insights": ["Readers value practical steps"],
                    "tone_and_style": "Clear and pragmatic",
                    "key_takeaways": ["Plan", "Draft", "Review"],
                    "evidence_gaps": [],
                    "call_to_action": "Create a publishing checklist",
                    "seo": {
                        "suggested_titles": ["Title one", "Title two", "Title three"],
                        "primary_keyword": "publishing workflow",
                        "secondary_keywords": ["content process"],
                        "meta_description": "Build a repeatable publishing workflow.",
                    },
                }
            ),
            model_id="fake-gemini",
            input_token_count=100,
            output_token_count=200,
        )


class RaisingBriefGenerator:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, _source: BriefSource) -> BriefGenerationResult:
        raise self.error


class FakeOutlineGenerator:
    def __init__(self) -> None:
        self.calls: list[OutlineSource] = []

    async def generate(self, source: OutlineSource) -> OutlineGenerationResult:
        self.calls.append(source)
        return OutlineGenerationResult(
            outline=GeneratedOutline.model_validate(
                {
                    "sections": [
                        {
                            "heading": f"Section {index}: {source.core_angle}",
                            "purpose": "Guide the reader",
                            "key_points": ["A practical point"],
                        }
                        for index in range(3)
                    ]
                }
            ),
            model_id="fake-gemini",
            input_token_count=75,
            output_token_count=125,
        )


class RaisingOutlineGenerator:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate(self, _source: OutlineSource) -> OutlineGenerationResult:
        raise self.error


async def clear_database(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
            await session.execute(delete(ArticleDraft))
            await session.execute(delete(ArticleOutline))
            await session.execute(delete(ArticleBrief))
            await session.execute(delete(Article))
            await session.execute(delete(LoginRateLimit))
            await session.execute(delete(User))
    finally:
        await engine.dispose()


async def article_count(settings: Settings) -> int:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return (await session.scalar(select(func.count()).select_from(Article))) or 0
    finally:
        await engine.dispose()


async def article_brief_count(settings: Settings) -> int:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return (await session.scalar(select(func.count()).select_from(ArticleBrief))) or 0
    finally:
        await engine.dispose()


async def article_outline_count(settings: Settings) -> int:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            return (await session.scalar(select(func.count()).select_from(ArticleOutline))) or 0
    finally:
        await engine.dispose()


async def invalid_goal_is_constrained(settings: Settings, user_id: UUID) -> bool:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                Article(
                    user_id=user_id,
                    notes="Notes",
                    working_title="Title",
                    target_audience=["Audience"],
                    article_goal="unsupported_goal",
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return True
            return False
    finally:
        await engine.dispose()


async def invalid_audience_is_constrained(
    settings: Settings, user_id: UUID, target_audience: list[str]
) -> bool:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            session.add(
                Article(
                    user_id=user_id,
                    notes="Notes",
                    working_title="Title",
                    target_audience=target_audience,
                    article_goal="inform_and_inspire",
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                return True
            return False
    finally:
        await engine.dispose()


@pytest.fixture
def article_context(settings: Settings) -> Iterator[tuple[TestClient, Settings]]:
    database_url = require_test_url(settings)
    command.upgrade(alembic_config(database_url), "head")
    test_settings = settings.model_copy(update={"database_url": database_url})
    asyncio.run(clear_database(test_settings))
    with TestClient(
        create_app(
            test_settings,
            brief_generator=FakeBriefGenerator(),
            outline_generator=FakeOutlineGenerator(),
        )
    ) as client:
        yield client, test_settings


def register(client: TestClient, suffix: str = "one") -> tuple[str, dict[str, object]]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"writer-{suffix}@example.com",
            "username": f"writer_{suffix}",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201
    body = response.json()
    return body["access_token"], body["user"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def article_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "notes": "Research notes and an early idea",
        "working_title": "A practical working title",
        "target_audience": ["Independent writers", "Small content teams"],
        "article_goal": "educate_with_practical_guidance",
    }
    payload.update(overrides)
    return payload


def create_article(client: TestClient, token: str, **overrides: object) -> dict[str, object]:
    response = client.post(
        "/api/v1/articles", json=article_payload(**overrides), headers=headers(token)
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_create_persists_normalized_owner_scoped_article(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, settings = article_context
    token, user = register(client)

    response = client.post(
        "/api/v1/articles",
        json=article_payload(
            notes="  Detailed notes  ",
            working_title="  Working title  ",
            target_audience=["  Product leaders  ", " Startup founders "],
        ),
        headers=headers(token),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == user["id"]
    assert body["notes"] == "Detailed notes"
    assert body["working_title"] == "Working title"
    assert body["target_audience"] == ["Product leaders", "Startup founders"]
    assert body["article_goal"] == "educate_with_practical_guidance"
    assert body["created_at"]
    assert body["updated_at"]
    assert asyncio.run(article_count(settings)) == 1


@pytest.mark.parametrize("goal", ARTICLE_GOALS)
def test_create_accepts_each_supported_goal(
    article_context: tuple[TestClient, Settings], goal: str
) -> None:
    client, _settings = article_context
    token, _user = register(client)

    response = client.post(
        "/api/v1/articles", json=article_payload(article_goal=goal), headers=headers(token)
    )

    assert response.status_code == 201
    assert response.json()["article_goal"] == goal


@pytest.mark.parametrize(
    "payload",
    [
        article_payload(notes="   "),
        article_payload(notes="x" * 20_001),
        article_payload(working_title="x" * 201),
        article_payload(target_audience=[]),
        article_payload(target_audience=[f"Audience {index}" for index in range(11)]),
        article_payload(target_audience=["   "]),
        article_payload(target_audience=["x" * 501]),
        article_payload(target_audience=["Writers", "writers"]),
        article_payload(target_audience="Independent writers"),
        article_payload(target_audience=None),
        article_payload(article_goal="unsupported_goal"),
        {key: value for key, value in article_payload().items() if key != "article_goal"},
    ],
)
def test_create_rejects_invalid_input(
    article_context: tuple[TestClient, Settings], payload: dict[str, object]
) -> None:
    client, _settings = article_context
    token, _user = register(client)

    response = client.post("/api/v1/articles", json=payload, headers=headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_database_rejects_an_unsupported_goal(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, settings = article_context
    _token, user = register(client)

    assert asyncio.run(invalid_goal_is_constrained(settings, UUID(str(user["id"]))))


@pytest.mark.parametrize(
    "target_audience",
    [[], [f"Audience {index}" for index in range(11)], cast(list[str], [None])],
)
def test_database_constrains_target_audience_array(
    article_context: tuple[TestClient, Settings], target_audience: list[str]
) -> None:
    client, settings = article_context
    _token, user = register(client)

    assert asyncio.run(
        invalid_audience_is_constrained(settings, UUID(str(user["id"])), target_audience)
    )


def test_list_is_owner_scoped_newest_first_and_paginated(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    first_token, _first_user = register(client, "one")
    second_token, _second_user = register(client, "two")
    first = create_article(client, first_token, working_title="First")
    second = create_article(client, first_token, working_title="Second")
    create_article(client, second_token, working_title="Other user's article")

    response = client.get("/api/v1/articles?offset=0&limit=1", headers=headers(first_token))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["offset"] == 0
    assert body["limit"] == 1
    assert [item["id"] for item in body["items"]] == [second["id"]]

    next_page = client.get(
        "/api/v1/articles?offset=1&limit=100", headers=headers(first_token)
    ).json()
    assert next_page["total"] == 2
    assert [item["id"] for item in next_page["items"]] == [first["id"]]


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101"])
def test_list_rejects_invalid_pagination(
    article_context: tuple[TestClient, Settings], query: str
) -> None:
    client, _settings = article_context
    token, _user = register(client)

    response = client.get(f"/api/v1/articles?{query}", headers=headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_get_and_patch_return_owner_article(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client)
    created = create_article(client, token)

    retrieved = client.get(f"/api/v1/articles/{created['id']}", headers=headers(token))
    updated = client.patch(
        f"/api/v1/articles/{created['id']}",
        json={
            "working_title": "  Revised title  ",
            "target_audience": [" Editors ", "Publishers"],
            "article_goal": "inform_and_inspire",
        },
        headers=headers(token),
    )

    assert retrieved.status_code == 200
    assert retrieved.json() == created
    assert updated.status_code == 200
    assert updated.json()["working_title"] == "Revised title"
    assert updated.json()["target_audience"] == ["Editors", "Publishers"]
    assert updated.json()["article_goal"] == "inform_and_inspire"
    assert updated.json()["notes"] == created["notes"]


@pytest.mark.parametrize("payload", [{}, {"notes": None}, {"working_title": "   "}])
def test_patch_rejects_empty_null_or_blank_updates(
    article_context: tuple[TestClient, Settings], payload: dict[str, object]
) -> None:
    client, _settings = article_context
    token, _user = register(client)
    article = create_article(client, token)

    response = client.patch(
        f"/api/v1/articles/{article['id']}", json=payload, headers=headers(token)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_missing_and_other_users_articles_share_not_found_response(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    owner_token, _owner = register(client, "owner")
    other_token, _other = register(client, "other")
    article = create_article(client, owner_token)

    for article_id in (article["id"], str(uuid4())):
        kwargs: dict[str, object] = {"headers": headers(other_token)}
        if method == "patch":
            kwargs["json"] = {"working_title": "Not allowed"}
        response = getattr(client, method)(f"/api/v1/articles/{article_id}", **kwargs)
        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "article_not_found",
                "message": "The article was not found",
            }
        }


def test_delete_hard_deletes_article(article_context: tuple[TestClient, Settings]) -> None:
    client, settings = article_context
    token, _user = register(client)
    article = create_article(client, token)
    assert (
        client.post(f"/api/v1/articles/{article['id']}/brief", headers=headers(token)).status_code
        == 200
    )
    assert (
        client.post(f"/api/v1/articles/{article['id']}/outline", headers=headers(token)).status_code
        == 200
    )

    response = client.delete(f"/api/v1/articles/{article['id']}", headers=headers(token))

    assert response.status_code == 204
    assert response.content == b""
    assert asyncio.run(article_count(settings)) == 0
    assert asyncio.run(article_brief_count(settings)) == 0
    assert asyncio.run(article_outline_count(settings)) == 0


@pytest.mark.parametrize("method", ["post", "get", "patch", "delete"])
def test_article_endpoints_require_authentication(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    path = "/api/v1/articles" if method in {"post", "get"} else f"/api/v1/articles/{uuid4()}"
    kwargs: dict[str, object] = {}
    if method == "post":
        kwargs["json"] = article_payload()
    if method == "patch":
        kwargs["json"] = {"working_title": "Revised"}

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_invalid_article_id_uses_validation_error(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client)

    response = client.get("/api/v1/articles/not-a-uuid", headers=headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_generate_get_replace_and_stale_article_brief(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client)
    article = create_article(client, token)
    path = f"/api/v1/articles/{article['id']}/brief"

    missing = client.get(path, headers=headers(token))
    generated = client.post(path, headers=headers(token))
    retrieved = client.get(path, headers=headers(token))

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "brief_not_found"
    assert generated.status_code == 200
    first = generated.json()
    assert first["article_id"] == article["id"]
    assert first["model_id"] == "fake-gemini"
    assert first["prompt_version"] == "article_brief_v1"
    assert first["input_token_count"] == 100
    assert first["output_token_count"] == 200
    assert first["is_stale"] is False
    assert "outline" not in first
    assert retrieved.json() == first

    updated = client.patch(
        f"/api/v1/articles/{article['id']}",
        json={"working_title": "A changed title"},
        headers=headers(token),
    )
    stale = client.get(path, headers=headers(token))
    replaced = client.post(path, headers=headers(token))

    assert updated.status_code == 200
    assert stale.json()["is_stale"] is True
    assert replaced.status_code == 200
    assert replaced.json()["id"] == first["id"]
    assert replaced.json()["summary"] == "A brief for A changed title."
    assert replaced.json()["is_stale"] is False


@pytest.mark.parametrize("method", ["get", "post"])
def test_article_brief_is_owner_scoped(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    owner_token, _owner = register(client, "brief_owner")
    other_token, _other = register(client, "brief_other")
    article = create_article(client, owner_token)
    path = f"/api/v1/articles/{article['id']}/brief"
    assert client.post(path, headers=headers(owner_token)).status_code == 200

    response = getattr(client, method)(path, headers=headers(other_token))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "article_not_found"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (BriefProviderTimeoutError(), 504, "brief_generation_timeout"),
        (BriefProviderBlockedError(), 422, "brief_generation_blocked"),
        (BriefProviderResponseError(), 502, "brief_generation_failed"),
        (BriefProviderUnavailableError(), 503, "brief_generation_unavailable"),
    ],
)
def test_article_brief_maps_provider_failures(
    article_context: tuple[TestClient, Settings],
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    client, _settings = article_context
    token, _user = register(client, f"failure_{status_code}")
    article = create_article(client, token)
    application = cast(FastAPI, client.app)
    original_generator = application.state.brief_generator
    application.state.brief_generator = RaisingBriefGenerator(error)
    try:
        response = client.post(f"/api/v1/articles/{article['id']}/brief", headers=headers(token))
    finally:
        application.state.brief_generator = original_generator

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("method", ["get", "post"])
def test_article_brief_endpoints_require_authentication(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context

    response = getattr(client, method)(f"/api/v1/articles/{uuid4()}/brief")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_patch_brief_merges_seo_and_controls_outline_staleness(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client, "brief_patch")
    article = create_article(client, token)
    brief_path = f"/api/v1/articles/{article['id']}/brief"
    outline_path = f"/api/v1/articles/{article['id']}/outline"
    brief = client.post(brief_path, headers=headers(token)).json()
    assert client.post(outline_path, headers=headers(token)).json()["is_stale"] is False

    seo_updated = client.patch(
        brief_path,
        json={"seo": {"meta_description": "A revised description."}},
        headers=headers(token),
    )
    after_seo = client.get(outline_path, headers=headers(token))

    assert seo_updated.status_code == 200
    assert seo_updated.json()["seo"]["meta_description"] == "A revised description."
    assert seo_updated.json()["seo"]["primary_keyword"] == brief["seo"]["primary_keyword"]
    assert after_seo.json()["is_stale"] is False

    content_updated = client.patch(
        brief_path,
        json={"core_angle": "A user-edited core angle"},
        headers=headers(token),
    )
    assert content_updated.status_code == 200
    assert client.get(outline_path, headers=headers(token)).json()["is_stale"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"summary": None},
        {"seo": {}},
        {"seo": None},
        {"model_id": "not-editable"},
        {"seo": {"unknown": "not-editable"}},
    ],
)
def test_patch_brief_rejects_empty_or_null_updates(
    article_context: tuple[TestClient, Settings], payload: dict[str, object]
) -> None:
    client, _settings = article_context
    token, _user = register(client, f"invalid_brief_{len(payload)}")
    article = create_article(client, token)
    path = f"/api/v1/articles/{article['id']}/brief"
    assert client.post(path, headers=headers(token)).status_code == 200

    response = client.patch(path, json=payload, headers=headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_outline_full_lifecycle_uses_latest_brief(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, settings = article_context
    token, _user = register(client, "outline_lifecycle")
    article = create_article(client, token)
    brief_path = f"/api/v1/articles/{article['id']}/brief"
    outline_path = f"/api/v1/articles/{article['id']}/outline"
    assert client.post(brief_path, headers=headers(token)).status_code == 200
    missing = client.get(outline_path, headers=headers(token))
    assert missing.json()["error"]["code"] == "outline_not_found"

    first = client.post(outline_path, headers=headers(token))
    retrieved = client.get(outline_path, headers=headers(token))
    assert first.status_code == 200
    assert retrieved.json() == first.json()
    assert first.json()["article_id"] == article["id"]
    assert first.json()["prompt_version"] == "article_outline_v1"
    assert first.json()["is_stale"] is False

    original_sections = first.json()["sections"]
    changed_sections = [
        {
            "id": original_sections[index]["id"],
            "heading": f"Edited section {index}",
            "purpose": "A user-edited purpose",
            "key_points": ["An edited point"],
        }
        for index in range(3)
    ]
    edited = client.patch(
        outline_path, json={"sections": changed_sections}, headers=headers(token)
    )
    assert edited.status_code == 200
    assert edited.json()["sections"] == changed_sections

    unknown_sections = [dict(section) for section in changed_sections]
    unknown_sections[0]["id"] = str(uuid4())
    unknown = client.patch(
        outline_path, json={"sections": unknown_sections}, headers=headers(token)
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"

    assert client.patch(
        brief_path,
        json={"core_angle": "The latest saved angle"},
        headers=headers(token),
    ).status_code == 200
    stale_edit = client.patch(
        outline_path, json={"sections": changed_sections}, headers=headers(token)
    )
    assert stale_edit.json()["is_stale"] is True

    regenerated = client.post(outline_path, headers=headers(token))
    assert regenerated.status_code == 200
    assert regenerated.json()["id"] == first.json()["id"]
    assert {section["id"] for section in regenerated.json()["sections"]}.isdisjoint(
        {section["id"] for section in first.json()["sections"]}
    )
    assert regenerated.json()["is_stale"] is False
    assert "The latest saved angle" in regenerated.json()["sections"][0]["heading"]
    assert asyncio.run(article_outline_count(settings)) == 1

    deleted = client.delete(outline_path, headers=headers(token))
    assert deleted.status_code == 204
    assert deleted.content == b""
    missing_again = client.get(outline_path, headers=headers(token))
    assert missing_again.json()["error"]["code"] == "outline_not_found"


@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_article_outline_is_owner_scoped(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    owner_token, _owner = register(client, f"outline_owner_{method}")
    other_token, _other = register(client, f"outline_other_{method}")
    article = create_article(client, owner_token)
    brief_path = f"/api/v1/articles/{article['id']}/brief"
    outline_path = f"/api/v1/articles/{article['id']}/outline"
    assert client.post(brief_path, headers=headers(owner_token)).status_code == 200
    assert client.post(outline_path, headers=headers(owner_token)).status_code == 200
    kwargs: dict[str, object] = {"headers": headers(other_token)}
    if method == "patch":
        kwargs["json"] = {"sections": [
            {"heading": str(index), "purpose": "Purpose", "key_points": ["Point"]}
            for index in range(3)
        ]}

    response = getattr(client, method)(outline_path, **kwargs)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "article_not_found"


def test_outline_requires_an_existing_brief(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client, "outline_without_brief")
    article = create_article(client, token)

    response = client.post(
        f"/api/v1/articles/{article['id']}/outline", headers=headers(token)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "brief_not_found"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"sections": None},
        {"sections": []},
        {
            "sections": [
                {"heading": str(index), "purpose": "Purpose", "key_points": ["Point"]}
                for index in range(2)
            ]
        },
        {
            "sections": [
                {
                    "heading": str(index),
                    "purpose": "Purpose",
                    "key_points": [str(point) for point in range(6)],
                }
                for index in range(3)
            ]
        },
        {
            "sections": [
                {"heading": str(index), "purpose": "Purpose", "key_points": ["Point"]}
                for index in range(3)
            ],
            "model_id": "not-editable",
        },
    ],
)
def test_patch_outline_rejects_invalid_updates(
    article_context: tuple[TestClient, Settings], payload: dict[str, object]
) -> None:
    client, _settings = article_context
    token, _user = register(client, f"invalid_outline_{len(str(payload))}")
    article = create_article(client, token)
    brief_path = f"/api/v1/articles/{article['id']}/brief"
    outline_path = f"/api/v1/articles/{article['id']}/outline"
    assert client.post(brief_path, headers=headers(token)).status_code == 200
    assert client.post(outline_path, headers=headers(token)).status_code == 200

    response = client.patch(outline_path, json=payload, headers=headers(token))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("method", ["get", "post", "patch", "delete"])
def test_article_outline_endpoints_require_authentication(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    kwargs: dict[str, object] = {}
    if method == "patch":
        kwargs["json"] = {
            "sections": [
                {"heading": str(index), "purpose": "Purpose", "key_points": ["Point"]}
                for index in range(3)
            ]
        }

    response = getattr(client, method)(f"/api/v1/articles/{uuid4()}/outline", **kwargs)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (BriefProviderTimeoutError(), 504, "outline_generation_timeout"),
        (BriefProviderBlockedError(), 422, "outline_generation_blocked"),
        (BriefProviderResponseError(), 502, "outline_generation_failed"),
        (BriefProviderUnavailableError(), 503, "outline_generation_unavailable"),
    ],
)
def test_article_outline_maps_provider_failures(
    article_context: tuple[TestClient, Settings],
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    client, _settings = article_context
    token, _user = register(client, f"outline_failure_{status_code}")
    article = create_article(client, token)
    assert client.post(
        f"/api/v1/articles/{article['id']}/brief", headers=headers(token)
    ).status_code == 200
    application = cast(FastAPI, client.app)
    original_generator = application.state.outline_generator
    application.state.outline_generator = RaisingOutlineGenerator(error)
    try:
        response = client.post(
            f"/api/v1/articles/{article['id']}/outline", headers=headers(token)
        )
    finally:
        application.state.outline_generator = original_generator

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_draft_lifecycle_and_outline_reconciliation(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    token, _user = register(client, "draft_lifecycle")
    article = create_article(client, token)
    outline_path = f"/api/v1/articles/{article['id']}/outline"
    draft_path = f"/api/v1/articles/{article['id']}/draft"
    brief = client.post(
        f"/api/v1/articles/{article['id']}/brief", headers=headers(token)
    )
    assert brief.status_code == 200

    missing = client.get(draft_path, headers=headers(token))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "draft_not_found"
    no_outline = client.post(draft_path, headers=headers(token))
    assert no_outline.status_code == 404
    assert no_outline.json()["error"]["code"] == "outline_not_found"

    outline = client.post(outline_path, headers=headers(token)).json()
    created = client.post(draft_path, headers=headers(token))
    repeated = client.post(draft_path, headers=headers(token))
    assert created.status_code == 200
    assert repeated.json() == created.json()
    draft = created.json()
    assert [section["outline_section_id"] for section in draft["sections"]] == [
        section["id"] for section in outline["sections"]
    ]
    assert all(section["checklist"] == [] for section in draft["sections"])
    assert all(
        json.loads(section["editor_state"])["root"]["type"] == "root"
        for section in draft["sections"]
    )

    saved_sections = draft["sections"]
    saved_sections[0]["checklist"] = [
        {"id": "opening", "label": "Set the context", "completed": True}
    ]
    saved_sections[0]["editor_state"] = (
        '{"root":{"children":[],"type":"root","version":1}}'
    )
    standalone_id = str(uuid4())
    saved_sections.append(
        {
            "id": standalone_id,
            "outline_section_id": None,
            "title": "Standalone",
            "goal": "A directly added section",
            "checklist": [],
            "editor_state": '{"root":{"children":[],"type":"root","version":1}}',
        }
    )
    saved = client.patch(draft_path, json={"sections": saved_sections}, headers=headers(token))
    assert saved.status_code == 200
    assert saved.json()["sections"] == saved_sections

    new_outline = [
        {
            "id": outline["sections"][0]["id"],
            "heading": "Refreshed title",
            "purpose": "Refreshed goal",
            "key_points": ["Point"],
        },
        outline["sections"][2],
        {"heading": "New outline section", "purpose": "New goal", "key_points": ["Point"]},
    ]
    updated_outline = client.patch(
        outline_path, json={"sections": new_outline}, headers=headers(token)
    ).json()
    reconciled = client.get(draft_path, headers=headers(token)).json()["sections"]
    assert [section["id"] for section in reconciled[:4]] == [
        section["id"] for section in saved_sections
    ]
    assert reconciled[0]["title"] == "Refreshed title"
    assert reconciled[0]["goal"] == "Refreshed goal"
    assert reconciled[0]["checklist"][0]["completed"] is True
    assert reconciled[0]["editor_state"] == saved_sections[0]["editor_state"]
    assert reconciled[1]["outline_section_id"] is None
    assert reconciled[3]["outline_section_id"] is None
    assert reconciled[4]["outline_section_id"] == updated_outline["sections"][2]["id"]

    assert client.delete(outline_path, headers=headers(token)).status_code == 204
    without_outline = client.get(draft_path, headers=headers(token)).json()
    assert all(section["outline_section_id"] is None for section in without_outline["sections"])


@pytest.mark.parametrize(
    "payload",
    [
        {"sections": [{"id": str(uuid4())}]},
        {
            "sections": [
                {
                    "id": str(uuid4()),
                    "outline_section_id": None,
                    "title": "Title",
                    "goal": "Goal",
                    "checklist": [],
                    "editor_state": "not-json",
                }
            ]
        },
    ],
)
def test_patch_draft_rejects_invalid_sections(
    article_context: tuple[TestClient, Settings], payload: dict[str, object]
) -> None:
    client, _settings = article_context
    token, _user = register(client, f"invalid_draft_{len(str(payload))}")
    article = create_article(client, token)
    path = f"/api/v1/articles/{article['id']}/draft"
    response = client.patch(path, json=payload, headers=headers(token))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("method", ["get", "post", "patch"])
def test_article_draft_endpoints_require_authentication(
    article_context: tuple[TestClient, Settings], method: str
) -> None:
    client, _settings = article_context
    kwargs: dict[str, object] = {}
    if method == "patch":
        kwargs["json"] = {"sections": []}
    response = getattr(client, method)(f"/api/v1/articles/{uuid4()}/draft", **kwargs)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_article_draft_is_owner_scoped(
    article_context: tuple[TestClient, Settings],
) -> None:
    client, _settings = article_context
    owner_token, _owner = register(client, "draft_owner")
    other_token, _other = register(client, "draft_other")
    article = create_article(client, owner_token)
    article_id = article["id"]
    assert client.post(
        f"/api/v1/articles/{article_id}/brief", headers=headers(owner_token)
    ).status_code == 200
    assert client.post(
        f"/api/v1/articles/{article_id}/outline", headers=headers(owner_token)
    ).status_code == 200
    path = f"/api/v1/articles/{article_id}/draft"
    assert client.post(path, headers=headers(owner_token)).status_code == 200

    for method in ("get", "post", "patch"):
        kwargs: dict[str, object] = {"headers": headers(other_token)}
        if method == "patch":
            kwargs["json"] = {"sections": []}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "article_not_found"
