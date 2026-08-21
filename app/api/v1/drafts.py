from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.draft import ArticleDraftResponse, ArticleDraftUpdate
from app.services.drafting_service import ArticleDraftService

router = APIRouter(prefix="/articles/{article_id}/draft", tags=["article drafts"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    502: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
    504: {"model": ErrorResponse},
}


@router.get("", response_model=ArticleDraftResponse, responses=ERROR_RESPONSES)
async def get_article_draft(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleDraftResponse:
    return await ArticleDraftService(session).get(article_id=article_id, user_id=current_user.id)


@router.post("", response_model=ArticleDraftResponse, responses=ERROR_RESPONSES)
async def create_article_draft(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleDraftResponse:
    return await ArticleDraftService(session).create(article_id=article_id, user_id=current_user.id)


@router.patch("", response_model=ArticleDraftResponse, responses=ERROR_RESPONSES)
async def update_article_draft(
    article_id: UUID,
    payload: ArticleDraftUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleDraftResponse:
    return await ArticleDraftService(session).update(
        article_id=article_id, user_id=current_user.id, payload=payload
    )
