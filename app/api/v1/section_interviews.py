from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.api.dependencies.section_interview_generator import (
    CurrentSectionDraftGenerator,
    CurrentSectionQuestionsGenerator,
)
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.section_interview import (
    SectionAnswersUpdate,
    SectionInterviewCreate,
    SectionInterviewResponse,
)
from app.services.section_interview_service import SectionInterviewService

router = APIRouter(
    prefix="/articles/{article_id}/draft/sections/{section_id}/interviews",
    tags=["section interviews"],
)
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 409, 422, 502, 503, 504)
}


@router.post("", response_model=SectionInterviewResponse, responses=ERROR_RESPONSES)
async def create_section_interview(
    article_id: UUID,
    section_id: UUID,
    current_user: CurrentUser,
    generator: CurrentSectionQuestionsGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    payload: Annotated[SectionInterviewCreate | None, Body()] = None,
) -> SectionInterviewResponse:
    return await SectionInterviewService(session, generator).create(
        article_id=article_id,
        section_id=section_id,
        user_id=current_user.id,
        instruction=payload.instruction if payload else None,
    )


@router.get("/latest", response_model=SectionInterviewResponse, responses=ERROR_RESPONSES)
async def get_latest_section_interview(
    article_id: UUID,
    section_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SectionInterviewResponse:
    return await SectionInterviewService(session).get_latest(
        article_id=article_id, section_id=section_id, user_id=current_user.id
    )


@router.get("/{interview_id}", response_model=SectionInterviewResponse, responses=ERROR_RESPONSES)
async def get_section_interview(
    article_id: UUID,
    section_id: UUID,
    interview_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SectionInterviewResponse:
    return await SectionInterviewService(session).get(
        article_id=article_id,
        section_id=section_id,
        interview_id=interview_id,
        user_id=current_user.id,
    )


@router.patch(
    "/{interview_id}/answers",
    response_model=SectionInterviewResponse,
    responses=ERROR_RESPONSES,
)
async def save_section_interview_answers(
    article_id: UUID,
    section_id: UUID,
    interview_id: UUID,
    payload: SectionAnswersUpdate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SectionInterviewResponse:
    return await SectionInterviewService(session).save_answers(
        article_id=article_id,
        section_id=section_id,
        interview_id=interview_id,
        user_id=current_user.id,
        payload=payload,
    )


@router.post(
    "/{interview_id}/generate",
    response_model=SectionInterviewResponse,
    responses=ERROR_RESPONSES,
)
async def generate_section_interview_draft(
    article_id: UUID,
    section_id: UUID,
    interview_id: UUID,
    current_user: CurrentUser,
    generator: CurrentSectionDraftGenerator,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SectionInterviewResponse:
    return await SectionInterviewService(session, generator).generate(
        article_id=article_id,
        section_id=section_id,
        interview_id=interview_id,
        user_id=current_user.id,
    )
