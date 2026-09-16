from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast
from uuid import UUID

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.db.models.interview_transcript import InterviewTranscript
from app.db.repositories.interview_invitation import InterviewInvitationRepository
from app.prompts.interview_insights import SYSTEM_INSTRUCTION, build_prompt
from app.schemas.interview_transcript import (
    GeneratedInterviewInsights,
    InterviewTranscriptResponse,
    TranscriptTurn,
)


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
        if not isinstance(parsed, GeneratedInterviewInsights):
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
        existing_ids = {turn["item_id"] for turn in transcript.turns}
        new_turns: list[dict[str, str]] = []
        for turn in turns:
            if turn.item_id in existing_ids:
                continue
            existing_ids.add(turn.item_id)
            new_turns.append(turn.model_dump())
        transcript.turns = [
            *transcript.turns,
            *new_turns,
        ]
        await self.session.flush()
        await self.session.refresh(transcript)
        return transcript_response(transcript)

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
            raise AppError(
                status_code=422,
                code="transcript_empty",
                message="No interview transcript is available yet",
            )
        if self.generator is None:
            raise AppError(
                status_code=503,
                code="interview_insights_unavailable",
                message="Interview insights are not configured",
            )
        try:
            result = await self.generator.generate(
                article_title=invitation.article.working_title
                if invitation.article
                else "the article",
                turns=turns,
            )
        except AppError as exc:
            transcript.insight_status = "failed"
            transcript.generation_error = exc.message
            await self.session.flush()
            raise
        self._validate_sources(
            result.insights,
            {turn.item_id for turn in turns if turn.speaker == "participant"},
        )
        transcript.insight_status = "ready"
        transcript.insights = result.insights.model_dump(mode="json")
        transcript.model_id = result.model_id
        transcript.generation_error = None
        await self.session.flush()
        await self.session.refresh(transcript)
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

    @staticmethod
    def _validate_sources(insights: GeneratedInterviewInsights, valid_ids: set[str]) -> None:
        sourced = [
            *insights.key_insights,
            *insights.examples_and_evidence,
            *insights.claims_to_verify,
        ]
        if any(not set(item.source_item_ids) <= valid_ids for item in sourced):
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
