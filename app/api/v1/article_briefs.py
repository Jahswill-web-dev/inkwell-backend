from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.api.dependencies.brief_generator import CurrentBriefGenerator
from app.db.session import get_db_session
from app.schemas.brief import ArticleBriefResponse, ArticleBriefUpdate
from app.schemas.common import ErrorResponse
from app.services.article_brief_service import ArticleBriefService

router = APIRouter(prefix="/articles/{article_id}/brief", tags=["article briefs"])

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=ArticleBriefResponse,
    responses={
        **ERROR_RESPONSES,
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def generate_article_brief(
    article_id: UUID,
    current_user: CurrentUser,
    generator: CurrentBriefGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleBriefResponse:
    return await ArticleBriefService(session, generator).generate(
        article_id=article_id, user_id=current_user.id
    )


@router.get("", response_model=ArticleBriefResponse, responses=ERROR_RESPONSES)
async def get_article_brief(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleBriefResponse:
    return await ArticleBriefService(session).get(article_id=article_id, user_id=current_user.id)


@router.patch("", response_model=ArticleBriefResponse, responses=ERROR_RESPONSES)
async def update_article_brief(
    article_id: UUID,
    payload: ArticleBriefUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ArticleBriefResponse:
    return await ArticleBriefService(session).update(
        article_id=article_id, user_id=current_user.id, payload=payload
    )
