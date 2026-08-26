from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.api.dependencies.section_interview_generator import CurrentDirectSectionDraftGenerator
from app.api.dependencies.talking_points_generator import CurrentTalkingPointsGenerator
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.draft import ArticleDraftResponse, ArticleDraftUpdate
from app.schemas.section_draft import SectionDraftCreate, SectionDraftResponse
from app.schemas.talking_points import TalkingPointsRequest, TalkingPointsResponse
from app.services.drafting_service import ArticleDraftService
from app.services.section_draft_service import SectionDraftService
from app.services.talking_points_service import TalkingPointsService

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


@router.post(
    "/sections/{section_id}/talking-points",
    response_model=TalkingPointsResponse,
    responses=ERROR_RESPONSES,
)
async def generate_section_talking_points(
    article_id: UUID,
    section_id: UUID,
    current_user: CurrentUser,
    generator: CurrentTalkingPointsGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[TalkingPointsRequest | None, Body()] = None,
) -> TalkingPointsResponse:
    return await TalkingPointsService(session, generator).generate(
        article_id=article_id,
        section_id=section_id,
        user_id=current_user.id,
        instruction=payload.instruction if payload is not None else None,
    )


@router.post(
    "/sections/{section_id}/generate",
    response_model=SectionDraftResponse,
    responses=ERROR_RESPONSES,
)
async def generate_section_draft(
    article_id: UUID,
    section_id: UUID,
    current_user: CurrentUser,
    generator: CurrentDirectSectionDraftGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[SectionDraftCreate | None, Body()] = None,
) -> SectionDraftResponse:
    return await SectionDraftService(session, generator).generate(
        article_id=article_id,
        section_id=section_id,
        user_id=current_user.id,
        instruction=payload.instruction if payload is not None else None,
    )
