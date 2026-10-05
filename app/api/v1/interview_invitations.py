from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser
from app.core.config import Settings, get_settings
from app.db.repositories.workspace import WorkspaceRepository
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.interview_invitation import (
    GuestInterviewResponse,
    InterviewInvitationCreate,
    InterviewInvitationResponse,
)
from app.schemas.interview_transcript import (
    InterviewTranscriptResponse,
    TranscriptTurnBatch,
)
from app.schemas.realtime import (
    OpenAIVoiceSessionResponse,
    RealtimeCallCreate,
    RealtimeCallResponse,
    VoiceSessionAssociation,
    VoiceSessionCreate,
    VoiceSessionResponse,
)
from app.services.interview_invitation_service import InterviewInvitationService
from app.services.interview_transcript_service import InterviewTranscriptService
from app.services.voice_interview import associate_voice_session, create_voice_provider

router = APIRouter(tags=["interview invitations"])
logger = logging.getLogger(__name__)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    410: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


# --- Authenticated Writer Routes ---


@router.post(
    "/articles/{article_id}/invitations",
    response_model=InterviewInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def create_invitation(
    article_id: UUID,
    payload: InterviewInvitationCreate,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
) -> InterviewInvitationResponse:
    service = InterviewInvitationService(
        session,
        question_generator=getattr(request.app.state, "client_interview_question_generator", None),
    )
    return await service.create(article_id=article_id, user_id=current_user.id, payload=payload)


@router.get(
    "/articles/{article_id}/invitations",
    response_model=InterviewInvitationResponse | None,
    responses=ERROR_RESPONSES,
)
async def get_latest_invitation(
    article_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InterviewInvitationResponse | None:
    service = InterviewInvitationService(session)
    return await service.get_latest_for_article(article_id=article_id, user_id=current_user.id)


@router.get(
    "/articles/{article_id}/invitations/{invitation_id}/transcript",
    response_model=InterviewTranscriptResponse,
    responses=ERROR_RESPONSES,
)
async def get_interview_transcript(
    article_id: UUID,
    invitation_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InterviewTranscriptResponse:
    workspace_membership = await WorkspaceRepository(session).get_default_for_user(current_user.id)
    invitation = await InterviewInvitationService(session).get_latest_for_article(
        article_id=article_id, user_id=current_user.id
    )
    if invitation is None or invitation.id != invitation_id:
        from app.core.exceptions import AppError

        raise AppError(
            status_code=404,
            code="interview_transcript_not_found",
            message="The interview transcript was not found",
        )
    if workspace_membership is None:
        from app.core.exceptions import AppError

        raise AppError(
            status_code=404, code="workspace_not_found", message="The workspace was not found"
        )
    return await InterviewTranscriptService(session).get_for_writer(
        invitation_id=invitation_id, workspace_id=workspace_membership[0].id
    )


@router.delete(
    "/articles/{article_id}/invitations/{invitation_id}",
    response_model=InterviewInvitationResponse,
    responses=ERROR_RESPONSES,
)
async def revoke_invitation(
    article_id: UUID,
    invitation_id: UUID,
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InterviewInvitationResponse:
    service = InterviewInvitationService(session)
    return await service.revoke(
        article_id=article_id, invitation_id=invitation_id, user_id=current_user.id
    )


# --- Public Guest Routes ---


@router.get(
    "/interviews/{token}",
    response_model=GuestInterviewResponse,
    responses=ERROR_RESPONSES,
)
async def get_guest_interview(
    token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GuestInterviewResponse:
    service = InterviewInvitationService(session)
    interview = await service.get_guest_interview(token=token)
    return interview.model_copy(
        update={
            "voice_transport": "elevenlabs_webrtc"
            if settings.voice_interview_provider == "elevenlabs"
            else "openai_webrtc"
        }
    )


@router.post(
    "/interviews/{token}/voice/session",
    response_model=VoiceSessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def create_voice_interview_session(
    token: str,
    payload: VoiceSessionCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> VoiceSessionResponse:
    interview = await InterviewInvitationService(session).get_guest_interview(token=token)
    previous_turns = await InterviewTranscriptService(session).get_turns_for_guest(token=token)
    response = await create_voice_provider(session, settings).create_session(
        payload=payload,
        interview=interview,
        previous_turns=previous_turns,
    )
    logger.info(
        "interview_voice_session_created invitation_id=%s provider=%s prior_turn_count=%d",
        interview.invitation.id,
        settings.voice_interview_provider,
        len(previous_turns),
    )
    return response


@router.put(
    "/interviews/{token}/voice/sessions/{voice_session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=ERROR_RESPONSES,
)
async def associate_interview_voice_session(
    token: str,
    voice_session_id: UUID,
    payload: VoiceSessionAssociation,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    interview = await InterviewInvitationService(session).get_guest_interview(token=token)
    await associate_voice_session(
        session,
        invitation_id=interview.invitation.id,
        voice_session_id=voice_session_id,
        external_session_id=payload.external_session_id,
    )


@router.post(
    "/interviews/{token}/realtime",
    response_model=RealtimeCallResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_realtime_interview_call(
    token: str,
    payload: RealtimeCallCreate,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RealtimeCallResponse:
    if settings.voice_interview_provider != "openai_live":
        from app.core.exceptions import AppError

        raise AppError(
            status_code=409,
            code="voice_transport_mismatch",
            message="The legacy OpenAI voice endpoint is not active",
        )
    response = await create_voice_interview_session(
        token,
        VoiceSessionCreate(sdp=payload.sdp),
        session,
        settings,
    )
    assert isinstance(response, OpenAIVoiceSessionResponse)
    return RealtimeCallResponse(sdp=response.sdp)


@router.post(
    "/interviews/{token}/transcript",
    response_model=InterviewTranscriptResponse,
    status_code=status.HTTP_201_CREATED,
    responses=ERROR_RESPONSES,
)
async def append_interview_transcript(
    token: str,
    payload: TranscriptTurnBatch,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InterviewTranscriptResponse:
    return await InterviewTranscriptService(session).append_turns(token=token, turns=payload.turns)


@router.post(
    "/interviews/{token}/transcript/finalize",
    response_model=InterviewTranscriptResponse,
    responses=ERROR_RESPONSES,
)
async def finalize_interview_transcript(
    token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
) -> InterviewTranscriptResponse:
    return await InterviewTranscriptService(
        session, generator=getattr(request.app.state, "interview_insights_generator", None)
    ).finalize(token=token)
