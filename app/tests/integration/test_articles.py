from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.core.config import Settings
from app.db.models.article import ARTICLE_GOALS, Article
from app.db.models.login_rate_limit import LoginRateLimit
from app.db.models.user import User
from app.db.session import create_engine, create_session_factory
from app.main import create_app
from app.tests.integration.test_database import alembic_config, require_test_url

pytestmark = pytest.mark.database


async def clear_database(settings: Settings) -> None:
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory.begin() as session:
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
                    target_audience="Audience",
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


@pytest.fixture
def article_context(settings: Settings) -> Iterator[tuple[TestClient, Settings]]:
    database_url = require_test_url(settings)
    command.upgrade(alembic_config(database_url), "head")
    test_settings = settings.model_copy(update={"database_url": database_url})
    asyncio.run(clear_database(test_settings))
    with TestClient(create_app(test_settings)) as client:
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


def article_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "notes": "Research notes and an early idea",
        "working_title": "A practical working title",
        "target_audience": "Independent writers",
        "article_goal": "educate_with_practical_guidance",
    }
    payload.update(overrides)
    return payload


def create_article(client: TestClient, token: str, **overrides: str) -> dict[str, object]:
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
            target_audience="  Product leaders  ",
        ),
        headers=headers(token),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == user["id"]
    assert body["notes"] == "Detailed notes"
    assert body["working_title"] == "Working title"
    assert body["target_audience"] == "Product leaders"
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
        article_payload(target_audience="x" * 501),
        article_payload(article_goal="unsupported_goal"),
        {key: value for key, value in article_payload().items() if key != "article_goal"},
    ],
)
def test_create_rejects_invalid_input(
    article_context: tuple[TestClient, Settings], payload: dict[str, str]
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
        json={"working_title": "  Revised title  ", "article_goal": "inform_and_inspire"},
        headers=headers(token),
    )

    assert retrieved.status_code == 200
    assert retrieved.json() == created
    assert updated.status_code == 200
    assert updated.json()["working_title"] == "Revised title"
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

    response = client.delete(f"/api/v1/articles/{article['id']}", headers=headers(token))

    assert response.status_code == 204
    assert response.content == b""
    assert asyncio.run(article_count(settings)) == 0


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
