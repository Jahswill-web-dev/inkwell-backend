from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.section_interview import SectionInterview


class SectionInterviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **values: Any) -> SectionInterview:
        interview = SectionInterview(**values)
        self.session.add(interview)
        await self.session.flush()
        await self.session.refresh(interview)
        return interview

    async def get(self, interview_id: UUID) -> SectionInterview | None:
        return await self.session.get(SectionInterview, interview_id)

    async def get_latest(self, draft_id: UUID, section_id: UUID) -> SectionInterview | None:
        result = await self.session.scalars(
            select(SectionInterview)
            .where(
                SectionInterview.draft_id == draft_id,
                SectionInterview.section_id == section_id,
            )
            .order_by(SectionInterview.created_at.desc(), SectionInterview.id.desc())
            .limit(1)
        )
        return result.first()

    async def update_answers(
        self, interview: SectionInterview, answers: list[dict[str, Any]]
    ) -> SectionInterview:
        if interview.answers != answers:
            interview.answers = answers
            interview.status = "awaiting_answers"
            interview.generated_blocks = None
            interview.draft_model_id = None
            interview.draft_prompt_version = None
            interview.draft_input_token_count = None
            interview.draft_output_token_count = None
            interview.draft_generation_duration_ms = None
        await self.session.flush()
        await self.session.refresh(interview)
        return interview

    async def save_draft(
        self,
        interview: SectionInterview,
        *,
        blocks: list[dict[str, Any]],
        model_id: str,
        prompt_version: str,
        input_token_count: int | None,
        output_token_count: int | None,
        duration_ms: int,
    ) -> SectionInterview:
        interview.status = "generated"
        interview.generated_blocks = blocks
        interview.draft_model_id = model_id
        interview.draft_prompt_version = prompt_version
        interview.draft_input_token_count = input_token_count
        interview.draft_output_token_count = output_token_count
        interview.draft_generation_duration_ms = duration_ms
        await self.session.flush()
        await self.session.refresh(interview)
        return interview
