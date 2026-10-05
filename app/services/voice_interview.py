from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.voice_session import VoiceSession
from app.schemas.interview_invitation import GuestInterviewResponse
from app.schemas.interview_transcript import TranscriptTurn
from app.schemas.realtime import (
    ElevenLabsVoiceSessionResponse,
    OpenAIVoiceSessionResponse,
    VoiceSessionCreate,
    VoiceSessionResponse,
)
from app.services.openai_realtime import (
    OpenAIRealtimeService,
    build_interview_workflow_instructions,
)

logger = logging.getLogger(__name__)


class VoiceInterviewProvider(Protocol):
    async def create_session(
        self,
        *,
        payload: VoiceSessionCreate,
        interview: GuestInterviewResponse,
        previous_turns: list[TranscriptTurn],
    ) -> VoiceSessionResponse: ...


class OpenAILiveVoiceProvider:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.service = OpenAIRealtimeService(settings)

    async def create_session(
        self,
        *,
        payload: VoiceSessionCreate,
        interview: GuestInterviewResponse,
        previous_turns: list[TranscriptTurn],
    ) -> OpenAIVoiceSessionResponse:
        if payload.sdp is None:
            raise AppError(
                status_code=422,
                code="voice_sdp_required",
                message="An SDP offer is required for the configured voice provider",
            )
        record = VoiceSession(
            invitation_id=interview.invitation.id,
            provider="openai_live",
            status="connecting",
            started_at=datetime.now(UTC),
            provider_metadata={},
        )
        self.session.add(record)
        await self.session.flush()
        result = await self.service.create_call(
            sdp=payload.sdp,
            interview=interview,
            previous_turns=previous_turns,
        )
        record.external_session_id = result.external_session_id
        record.status = "connected"
        await self.session.flush()
        return OpenAIVoiceSessionResponse(session_id=record.id, sdp=result.sdp)


class ElevenLabsVoiceProvider:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        if settings.elevenlabs_api_key is None or settings.elevenlabs_agent_id is None:
            raise AppError(
                status_code=503,
                code="voice_provider_not_configured",
                message="The configured voice interview provider is not available",
            )
        self.session = session
        self.api_key = settings.elevenlabs_api_key.get_secret_value()
        self.agent_id = settings.elevenlabs_agent_id
        self.base_url = settings.elevenlabs_base_url
        self.timeout = settings.elevenlabs_request_timeout_seconds
        self.environment = "staging" if settings.app_env == "staging" else "production"

    async def create_session(
        self,
        *,
        payload: VoiceSessionCreate,
        interview: GuestInterviewResponse,
        previous_turns: list[TranscriptTurn],
    ) -> ElevenLabsVoiceSessionResponse:
        if payload.sdp is not None:
            raise AppError(
                status_code=422,
                code="voice_sdp_not_supported",
                message="An SDP offer is not accepted by the configured voice provider",
            )
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/v1/convai/conversation/token",
                    params={
                        "agent_id": self.agent_id,
                        "participant_name": interview.invitation.participant_name,
                        "environment": self.environment,
                    },
                    headers={"xi-api-key": self.api_key},
                )
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as exc:
            raise AppError(
                status_code=504,
                code="voice_provider_timeout",
                message="The voice interview provider timed out. Please try again.",
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                status_code=502,
                code="voice_provider_connection_failed",
                message="Unable to start the voice interview. Please try again.",
            ) from exc

        token = body.get("token") if isinstance(body, dict) else None
        conversation_id = body.get("conversation_id") if isinstance(body, dict) else None
        if not isinstance(token, str):
            raise AppError(
                status_code=502,
                code="voice_provider_invalid_response",
                message="The voice interview provider returned an invalid response",
            )

        record = VoiceSession(
            invitation_id=interview.invitation.id,
            provider="elevenlabs",
            external_session_id=conversation_id if isinstance(conversation_id, str) else None,
            status="created",
            started_at=datetime.now(UTC),
            provider_metadata={},
        )
        self.session.add(record)
        await self.session.flush()
        return ElevenLabsVoiceSessionResponse(
            session_id=record.id,
            conversation_token=token,
            conversation_id=record.external_session_id,
            dynamic_variables=_elevenlabs_dynamic_variables(interview, previous_turns),
        )


def create_voice_provider(
    session: AsyncSession, settings: Settings
) -> VoiceInterviewProvider:
    if settings.voice_interview_provider == "elevenlabs":
        return ElevenLabsVoiceProvider(session, settings)
    return OpenAILiveVoiceProvider(session, settings)


async def associate_voice_session(
    session: AsyncSession,
    *,
    invitation_id: UUID,
    voice_session_id: UUID,
    external_session_id: str,
) -> VoiceSession:
    record = cast(
        VoiceSession | None,
        await session.scalar(
            select(VoiceSession).where(
                VoiceSession.id == voice_session_id,
                VoiceSession.invitation_id == invitation_id,
            )
        ),
    )
    if record is None:
        raise AppError(
            status_code=404,
            code="voice_session_not_found",
            message="The voice interview session was not found",
        )
    if record.external_session_id not in {None, external_session_id}:
        raise AppError(
            status_code=409,
            code="voice_session_mismatch",
            message="The voice session identifier does not match",
        )
    record.external_session_id = external_session_id
    record.status = "connected"
    await session.flush()
    return record


def _elevenlabs_dynamic_variables(
    interview: GuestInterviewResponse, previous_turns: list[TranscriptTurn]
) -> dict[str, str | int | bool]:
    invitation = interview.invitation
    return {
        "participant_name": invitation.participant_name,
        "client_name": invitation.client_name or "the client's team",
        "writer_name": invitation.writer_name or "the writer",
        "article_title": invitation.article_title or "the upcoming article",
        "interview_instructions": build_interview_workflow_instructions(
            interview, previous_turns
        ),
        "is_resumed": bool(previous_turns),
    }
