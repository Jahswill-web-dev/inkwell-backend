from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.interview_transcript import InterviewTranscript
from app.db.models.voice_session import VoiceSession
from app.db.repositories.interview_invitation import InterviewInvitationRepository
from app.prompts.interview_insights import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.interview_transcript import (
    GeneratedInterviewInsights,
    InterviewTranscriptResponse,
    TranscriptTurn,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InterviewInsightsResult:
    insights: GeneratedInterviewInsights
    model_id: str


class InterviewInsightsGenerator(Protocol):
    async def generate(
        self, *, article_title: str, turns: list[TranscriptTurn]
    ) -> InterviewInsightsResult: ...


class OpenAIInterviewInsightsGenerator:
    def __init__(self, settings: Settings) -> None:
        assert settings.openai_api_key is not None
        self.model_id = settings.openai_interview_question_model
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_realtime_request_timeout_seconds,
        )

    async def generate(
        self, *, article_title: str, turns: list[TranscriptTurn]
    ) -> InterviewInsightsResult:
        try:
            response = await self._client.responses.parse(
                model=self.model_id,
                instructions=SYSTEM_INSTRUCTION,
                input=build_prompt(article_title=article_title, turns=turns),
                text_format=GeneratedInterviewInsights,
                max_output_tokens=2_000,
                reasoning={"effort": "none"},
                store=False,
            )
        except APITimeoutError as exc:
            raise AppError(
                status_code=504,
                code="interview_insights_timeout",
                message="Interview insights timed out",
            ) from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise AppError(
                status_code=503,
                code="interview_insights_unavailable",
                message="Interview insights are unavailable",
            ) from exc
        parsed = response.output_parsed
        logger.info(
            "interview_insights_response_received response_status=%s parsed=%s",
            getattr(response, "status", None),
            isinstance(parsed, GeneratedInterviewInsights),
        )
        if not isinstance(parsed, GeneratedInterviewInsights):
            logger.warning(
                "interview_insights_unparsed response_status=%s incomplete_reason=%s",
                response.status,
                getattr(getattr(response, "incomplete_details", None), "reason", None),
            )
            raise AppError(
                status_code=502,
                code="interview_insights_invalid",
                message="Interview insights were invalid",
            )
        return InterviewInsightsResult(insights=parsed, model_id=self.model_id)

    async def close(self) -> None:
        await self._client.close()


class InterviewTranscriptService:
    def __init__(
        self, session: AsyncSession, generator: InterviewInsightsGenerator | None = None
    ) -> None:
        self.session = session
        self.invitations = InterviewInvitationRepository(session)
        self.generator = generator

    async def append_turns(
        self, *, token: str, turns: list[TranscriptTurn]
    ) -> InterviewTranscriptResponse:
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="invitation_not_found",
                message="This interview link isn't valid",
            )
        transcript = await self._get_or_create(invitation.id)
        await self._validate_voice_sessions(invitation.id, turns)
        existing_ids = {turn["item_id"] for turn in transcript.turns}
        new_turns: list[dict[str, Any]] = []
        for turn in turns:
            if turn.item_id in existing_ids:
                continue
            existing_ids.add(turn.item_id)
            new_turns.append(turn.model_dump(mode="json", exclude_none=True))
        transcript.turns = [
            *transcript.turns,
            *new_turns,
        ]
        await self.session.flush()
        await self.session.refresh(transcript)
        logger.info(
            "interview_transcript_appended invitation_id=%s new_turn_count=%d total_turn_count=%d",
            invitation.id,
            len(new_turns),
            len(transcript.turns),
        )
        return transcript_response(transcript)

    async def reconcile_elevenlabs_transcript(
        self,
        *,
        external_session_id: str,
        provider_turns: list[dict[str, Any]],
        provider_metadata: dict[str, Any],
    ) -> tuple[InterviewTranscriptResponse | None, bool, str | None]:
        voice_session = cast(
            VoiceSession | None,
            await self.session.scalar(
                select(VoiceSession).where(
                    VoiceSession.provider == "elevenlabs",
                    VoiceSession.external_session_id == external_session_id,
                )
            ),
        )
        if voice_session is None:
            logger.warning(
                "elevenlabs_webhook_unknown_conversation external_session_id=%s",
                external_session_id,
            )
            return None, False, None

        transcript = await self._get_or_create(voice_session.invitation_id)
        existing_counts: dict[tuple[str, str], int] = {}
        for value in transcript.turns:
            if value.get("voice_session_id") != str(voice_session.id):
                continue
            key = (
                str(value.get("speaker", "")),
                _normalized_turn_text(str(value.get("text", ""))),
            )
            existing_counts[key] = existing_counts.get(key, 0) + 1

        seen_counts: dict[tuple[str, str], int] = {}
        additions: list[dict[str, Any]] = []
        for index, value in enumerate(provider_turns):
            role = value.get("role")
            text = value.get("message")
            if role not in {"user", "agent"} or not isinstance(text, str) or not text.strip():
                continue
            speaker = "participant" if role == "user" else "interviewer"
            key = (speaker, _normalized_turn_text(text))
            occurrence = seen_counts.get(key, 0) + 1
            seen_counts[key] = occurrence
            if occurrence <= existing_counts.get(key, 0):
                continue
            seconds = value.get("time_in_call_secs")
            occurred_at_ms = (
                max(0, int(float(seconds) * 1000))
                if isinstance(seconds, (int, float))
                else None
            )
            additions.append(
                TranscriptTurn(
                    item_id=f"elevenlabs:{external_session_id}:{index}",
                    speaker=speaker,
                    text=text.strip(),
                    provider="elevenlabs",
                    voice_session_id=voice_session.id,
                    occurred_at_ms=occurred_at_ms,
                ).model_dump(mode="json", exclude_none=True)
            )

        if additions:
            transcript.turns = [*transcript.turns, *additions]
            transcript.insight_status = "pending"
            transcript.insights = None
            transcript.model_id = None
            transcript.generation_error = None
        voice_session.status = "completed"
        voice_session.ended_at = datetime.now(UTC)
        voice_session.provider_metadata = provider_metadata
        termination_reason = provider_metadata.get("termination_reason")
        if isinstance(termination_reason, str):
            voice_session.termination_reason = termination_reason[:80]
        await self.session.flush()
        await self.session.refresh(transcript)
        invitation = await self.invitations.get(voice_session.invitation_id)
        invitation_token = invitation.token if invitation else None
        return transcript_response(transcript), bool(additions), invitation_token

    async def finalize(self, *, token: str) -> InterviewTranscriptResponse:
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="invitation_not_found",
                message="This interview link isn't valid",
            )
        transcript = await self._get_or_create(invitation.id)
        turns = [TranscriptTurn.model_validate(turn) for turn in transcript.turns]
        if not turns:
            logger.warning(
                "interview_transcript_finalize_rejected_empty invitation_id=%s",
                invitation.id,
            )
            raise AppError(
                status_code=422,
                code="transcript_empty",
                message="No interview transcript is available yet",
            )
        logger.info(
            "interview_transcript_finalize_started invitation_id=%s "
            "turn_count=%d insight_enabled=%s",
            invitation.id,
            len(turns),
            self.generator is not None,
        )
        invitation.progress_state = "completed"
        if invitation.completed_at is None:
            invitation.completed_at = datetime.now(UTC)
        if invitation.article:
            invitation.article.status = "ready_to_draft"
        if self.generator is None:
            transcript.insight_status = "failed"
            transcript.generation_error = "Interview insights are not configured"
            await self.session.flush()
            await self.session.refresh(transcript)
            logger.warning(
                "interview_transcript_finalized_without_insights invitation_id=%s turn_count=%d",
                invitation.id,
                len(turns),
            )
            return transcript_response(transcript)
        try:
            result = await self.generator.generate(
                article_title=invitation.article.working_title
                if invitation.article
                else "the article",
                turns=turns,
            )
            self._validate_sources(
                result.insights,
                {turn.item_id for turn in turns if turn.speaker == "participant"},
            )
        except AppError as exc:
            transcript.insight_status = "failed"
            transcript.generation_error = exc.message
            await self.session.flush()
            await self.session.refresh(transcript)
            logger.warning(
                "interview_transcript_finalized_with_insight_failure "
                "invitation_id=%s turn_count=%d error_code=%s",
                invitation.id,
                len(turns),
                exc.code,
            )
            return transcript_response(transcript)
        except Exception:
            logger.exception(
                "interview_insights_unexpected_failure invitation_id=%s turn_count=%d",
                invitation.id,
                len(turns),
            )
            transcript.insight_status = "failed"
            transcript.generation_error = "Interview insights could not be generated"
            await self.session.flush()
            await self.session.refresh(transcript)
            return transcript_response(transcript)
        transcript.insight_status = "ready"
        transcript.insights = result.insights.model_dump(mode="json")
        transcript.model_id = result.model_id
        transcript.generation_error = None
        await self.session.flush()
        await self.session.refresh(transcript)
        logger.info(
            "interview_transcript_finalized invitation_id=%s turn_count=%d insight_status=%s",
            invitation.id,
            len(turns),
            transcript.insight_status,
        )
        return transcript_response(transcript)

    async def get_for_writer(
        self, *, invitation_id: UUID, workspace_id: UUID
    ) -> InterviewTranscriptResponse:
        invitation = await self.invitations.get_by_id_in_workspace(invitation_id, workspace_id)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="interview_transcript_not_found",
                message="The interview transcript was not found",
            )
        transcript = await self._get(invitation.id)
        if transcript is None:
            raise AppError(
                status_code=404,
                code="interview_transcript_not_found",
                message="The interview transcript was not found",
            )
        return transcript_response(transcript)

    async def get_turns_for_guest(self, *, token: str) -> list[TranscriptTurn]:
        """Get already persisted dialogue for a replacement realtime connection."""
        invitation = await self.invitations.get_by_token(token)
        if invitation is None:
            raise AppError(
                status_code=404,
                code="invitation_not_found",
                message="This interview link isn't valid",
            )
        transcript = await self._get(invitation.id)
        if transcript is None:
            return []
        return [TranscriptTurn.model_validate(turn) for turn in transcript.turns]

    async def _get(self, invitation_id: UUID) -> InterviewTranscript | None:
        return cast(
            InterviewTranscript | None,
            await self.session.scalar(
                select(InterviewTranscript).where(
                    InterviewTranscript.invitation_id == invitation_id
                )
            ),
        )

    async def _get_or_create(self, invitation_id: UUID) -> InterviewTranscript:
        transcript = await self._get(invitation_id)
        if transcript is not None:
            return transcript
        transcript = InterviewTranscript(invitation_id=invitation_id, turns=[])
        self.session.add(transcript)
        await self.session.flush()
        return transcript

    async def _validate_voice_sessions(
        self, invitation_id: UUID, turns: list[TranscriptTurn]
    ) -> None:
        session_ids = {turn.voice_session_id for turn in turns if turn.voice_session_id is not None}
        if not session_ids:
            return
        records = list(
            (
                await self.session.scalars(
                    select(VoiceSession).where(
                        VoiceSession.id.in_(session_ids),
                        VoiceSession.invitation_id == invitation_id,
                    )
                )
            ).all()
        )
        providers = {record.id: record.provider for record in records}
        if len(providers) != len(session_ids) or any(
            turn.voice_session_id is not None
            and turn.provider is not None
            and providers.get(turn.voice_session_id) != turn.provider
            for turn in turns
        ):
            raise AppError(
                status_code=422,
                code="invalid_voice_session",
                message="A transcript turn referenced an invalid voice session",
            )

    @staticmethod
    def _validate_sources(insights: GeneratedInterviewInsights, valid_ids: set[str]) -> None:
        sourced = [
            *insights.key_insights,
            *insights.examples_and_evidence,
            *insights.claims_to_verify,
        ]
        if any(not set(item.source_item_ids) <= valid_ids for item in sourced):
            logger.warning(
                "interview_insights_unknown_sources sourced_item_count=%d "
                "valid_participant_turn_count=%d",
                len(sourced),
                len(valid_ids),
            )
            raise AppError(
                status_code=502,
                code="interview_insights_invalid",
                message="Interview insights referenced unknown transcript turns",
            )


def transcript_response(transcript: InterviewTranscript) -> InterviewTranscriptResponse:
    return InterviewTranscriptResponse(
        id=transcript.id,
        invitation_id=transcript.invitation_id,
        turns=[TranscriptTurn.model_validate(turn) for turn in transcript.turns],
        insight_status=cast(Literal["pending", "ready", "failed"], transcript.insight_status),
        insights=GeneratedInterviewInsights.model_validate(transcript.insights)
        if transcript.insights
        else None,
        model_id=transcript.model_id,
        generation_error=transcript.generation_error,
        created_at=transcript.created_at,
        updated_at=transcript.updated_at,
    )


def _normalized_turn_text(value: str) -> str:
    return " ".join(value.casefold().split())
