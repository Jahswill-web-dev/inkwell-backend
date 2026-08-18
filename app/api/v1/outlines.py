from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.api.dependencies.outline_generator import CurrentOutlineGenerator
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.outline import ArticleOutlineResponse, ArticleOutlineUpdate
from app.services.outline_service import ArticleOutlineService

router = APIRouter(prefix="/articles/{article_id}/outline", tags=["article outlines"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=ArticleOutlineResponse,
    responses={
        **ERROR_RESPONSES,
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def generate_article_outline(
    article_id: UUID,
    current_user: CurrentUser,
    generator: CurrentOutlineGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleOutlineResponse:
    return await ArticleOutlineService(session, generator).generate(
        article_id=article_id, user_id=current_user.id
    )


@router.get("", response_model=ArticleOutlineResponse, responses=ERROR_RESPONSES)
async def get_article_outline(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleOutlineResponse:
    return await ArticleOutlineService(session).get(
        article_id=article_id, user_id=current_user.id
    )


@router.patch("", response_model=ArticleOutlineResponse, responses=ERROR_RESPONSES)
async def update_article_outline(
    article_id: UUID,
    payload: ArticleOutlineUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleOutlineResponse:
    return await ArticleOutlineService(session).update(
        article_id=article_id, user_id=current_user.id, payload=payload
    )


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=ERROR_RESPONSES,
)
async def delete_article_outline(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await ArticleOutlineService(session).delete(article_id=article_id, user_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
