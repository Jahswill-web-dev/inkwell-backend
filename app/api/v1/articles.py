from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.db.repositories.article import ArticleRepository
from app.db.session import get_db_session
from app.schemas.article import (
    ArticleCreate,
    ArticleListResponse,
    ArticleResponse,
    ArticleUpdate,
)
from app.schemas.common import ErrorResponse
from app.services.article_service import ArticleService

router = APIRouter(prefix="/articles", tags=["articles"])


def _service(session: AsyncSession) -> ArticleService:
    return ArticleService(ArticleRepository(session))


@router.post(
    "",
    response_model=ArticleResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)
async def create_article(
    payload: ArticleCreate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleResponse:
    article = await _service(session).create(user_id=current_user.id, payload=payload)
    return ArticleResponse.model_validate(article)


@router.get(
    "",
    response_model=ArticleListResponse,
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse}},
)
async def list_articles(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ArticleListResponse:
    items, total = await _service(session).list(user_id=current_user.id, offset=offset, limit=limit)
    return ArticleListResponse(
        items=[ArticleResponse.model_validate(article) for article in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{article_id}",
    response_model=ArticleResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def get_article(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleResponse:
    article = await _service(session).get(article_id=article_id, user_id=current_user.id)
    return ArticleResponse.model_validate(article)


@router.patch(
    "/{article_id}",
    response_model=ArticleResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def update_article(
    article_id: UUID,
    payload: ArticleUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleResponse:
    article = await _service(session).update(
        article_id=article_id, user_id=current_user.id, payload=payload
    )
    return ArticleResponse.model_validate(article)


@router.delete(
    "/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"model": ErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    },
)
async def delete_article(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await _service(session).delete(article_id=article_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
