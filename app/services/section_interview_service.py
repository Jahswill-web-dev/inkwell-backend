from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article_draft import ArticleDraft
from app.db.models.section_interview import SectionInterview
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.db.repositories.article_draft import ArticleDraftRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.db.repositories.section_interview import SectionInterviewRepository
from app.prompts.section_interview import DRAFT_PROMPT_VERSION, QUESTIONS_PROMPT_VERSION
from app.schemas.outline import ArticleOutlineSection
from app.schemas.section_interview import (
    SectionAnswersUpdate,
    SectionInterviewResponse,
    SectionQuestion,
)
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
)
from app.services.draft_context import build_draft_section_context
from app.services.drafting_service import reconcile_sections
from app.services.section_interview_ai import SectionInterviewGenerator


@dataclass(frozen=True)
class InterviewContext:
    draft: ArticleDraft
    context: dict[str, Any]


class SectionInterviewService:
    def __init__(
        self, session: AsyncSession, generator: SectionInterviewGenerator | None = None
    ) -> None:
        self.articles = ArticleRepository(session)
        self.briefs = ArticleBriefRepository(session)
        self.drafts = ArticleDraftRepository(session)
        self.outlines = ArticleOutlineRepository(session)
        self.interviews = SectionInterviewRepository(session)
        self.generator = generator

    async def create(
        self,
        *,
        article_id: UUID,
        section_id: UUID,
        user_id: UUID,
        instruction: str | None,
    ) -> SectionInterviewResponse:
        loaded = await self._load_context(article_id, section_id, user_id)
        assert self.generator is not None
        started = perf_counter()
        try:
            result = await self.generator.generate_questions(loaded.context, instruction)
        except Exception as exc:
            raise _generation_error(exc, "section_questions") from exc
        questions = [
            {"id": str(uuid4()), **question.model_dump(mode="json")}
            for question in result.questions.questions
        ]
        interview = await self.interviews.create(
            draft_id=loaded.draft.id,
            section_id=section_id,
            status="awaiting_answers",
            questions=questions,
            answers=[],
            generated_blocks=None,
            context_fingerprint=context_fingerprint(loaded.context),
            question_model_id=result.model_id,
            question_prompt_version=QUESTIONS_PROMPT_VERSION,
            question_input_token_count=result.input_token_count,
            question_output_token_count=result.output_token_count,
            question_generation_duration_ms=round((perf_counter() - started) * 1000),
        )
        return interview_response(interview, is_stale=False)

    async def get_latest(
        self, *, article_id: UUID, section_id: UUID, user_id: UUID
    ) -> SectionInterviewResponse:
        loaded = await self._load_context(article_id, section_id, user_id)
        interview = await self.interviews.get_latest(loaded.draft.id, section_id)
        if interview is None:
            raise _error(404, "section_interview_not_found", "Section interview not found")
        return interview_response(
            interview,
            is_stale=interview.context_fingerprint != context_fingerprint(loaded.context),
        )

    async def get(
        self, *, article_id: UUID, section_id: UUID, interview_id: UUID, user_id: UUID
    ) -> SectionInterviewResponse:
        loaded, interview = await self._load_interview(
            article_id, section_id, interview_id, user_id
        )
        return interview_response(
            interview,
            is_stale=interview.context_fingerprint != context_fingerprint(loaded.context),
        )

    async def save_answers(
        self,
        *,
        article_id: UUID,
        section_id: UUID,
        interview_id: UUID,
        user_id: UUID,
        payload: SectionAnswersUpdate,
    ) -> SectionInterviewResponse:
        loaded, interview = await self._load_interview(
            article_id, section_id, interview_id, user_id
        )
        known_ids = {UUID(str(question["id"])) for question in interview.questions}
        supplied_ids = {answer.question_id for answer in payload.answers}
        if not supplied_ids <= known_ids:
            raise _error(
                422, "unknown_section_question", "An answer references an unknown question"
            )
        answers = [answer.model_dump(mode="json") for answer in payload.answers]
        interview = await self.interviews.update_answers(interview, answers)
        return interview_response(
            interview,
            is_stale=interview.context_fingerprint != context_fingerprint(loaded.context),
        )

    async def generate(
        self, *, article_id: UUID, section_id: UUID, interview_id: UUID, user_id: UUID
    ) -> SectionInterviewResponse:
        loaded, interview = await self._load_interview(
            article_id, section_id, interview_id, user_id
        )
        if interview.context_fingerprint != context_fingerprint(loaded.context):
            raise _error(
                409,
                "section_interview_stale",
                "The article context changed; generate a new section interview",
            )
        answer_by_id = {
            str(item["question_id"]): item.get("answer")
            for item in interview.answers
            if isinstance(item, dict)
        }
        questions_and_answers = [
            {**question, "answer": answer_by_id.get(str(question["id"]))}
            for question in interview.questions
            if isinstance(answer_by_id.get(str(question["id"])), str)
            and str(answer_by_id[str(question["id"])]).strip()
        ]
        if not questions_and_answers:
            raise _error(
                422,
                "section_answers_required",
                "At least one substantive answer is required",
            )
        assert self.generator is not None
        started = perf_counter()
        try:
            result = await self.generator.generate_draft(loaded.context, questions_and_answers)
        except Exception as exc:
            raise _generation_error(exc, "section_draft") from exc
        interview = await self.interviews.save_draft(
            interview,
            blocks=[block.model_dump(mode="json") for block in result.draft.blocks],
            model_id=result.model_id,
            prompt_version=DRAFT_PROMPT_VERSION,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
            duration_ms=round((perf_counter() - started) * 1000),
        )
        return interview_response(interview, is_stale=False)

    async def _load_interview(
        self, article_id: UUID, section_id: UUID, interview_id: UUID, user_id: UUID
    ) -> tuple[InterviewContext, SectionInterview]:
        loaded = await self._load_context(article_id, section_id, user_id)
        interview = await self.interviews.get(interview_id)
        if (
            interview is None
            or interview.draft_id != loaded.draft.id
            or interview.section_id != section_id
        ):
            raise _error(404, "section_interview_not_found", "Section interview not found")
        return loaded, interview

    async def _load_context(
        self, article_id: UUID, section_id: UUID, user_id: UUID
    ) -> InterviewContext:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise _error(404, "article_not_found", "Article not found")
        draft = await self.drafts.get_for_article(article_id)
        if draft is None:
            raise _error(404, "draft_not_found", "Draft not found")
        brief = await self.briefs.get_for_article(article_id)
        if brief is None:
            raise _error(404, "brief_not_found", "Article brief not found")
        outline = await self.outlines.get_for_article(article_id)
        outline_sections = [] if outline is None else outline.sections
        typed_outline = [ArticleOutlineSection.model_validate(item) for item in outline_sections]
        reconciled = reconcile_sections(draft.sections, typed_outline)
        if reconciled != draft.sections:
            draft = await self.drafts.update_sections(draft, reconciled)
        if not any(section.get("id") == str(section_id) for section in draft.sections):
            raise _error(404, "draft_section_not_found", "Draft section not found")
        return InterviewContext(
            draft=draft,
            context=build_draft_section_context(
                article=article,
                brief=brief,
                draft=draft,
                outline_sections=outline_sections,
                selected_section_id=section_id,
            ),
        )


def context_fingerprint(context: dict[str, Any]) -> str:
    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def interview_response(interview: SectionInterview, *, is_stale: bool) -> SectionInterviewResponse:
    return SectionInterviewResponse(
        id=interview.id,
        draft_id=interview.draft_id,
        section_id=interview.section_id,
        status=interview.status,
        questions=[SectionQuestion.model_validate(item) for item in interview.questions],
        answers=interview.answers,
        generated_blocks=interview.generated_blocks,
        is_stale=is_stale,
        created_at=interview.created_at,
        updated_at=interview.updated_at,
    )


def _generation_error(exc: Exception, prefix: str) -> AppError:
    if isinstance(exc, BriefProviderTimeoutError):
        return _error(504, f"{prefix}_generation_timeout", "Generation timed out")
    if isinstance(exc, BriefProviderBlockedError):
        return _error(422, f"{prefix}_generation_blocked", "The content could not be processed")
    if isinstance(exc, BriefProviderResponseError):
        return _error(502, f"{prefix}_generation_failed", "The generated content was invalid")
    if isinstance(exc, BriefProviderUnavailableError):
        return _error(503, f"{prefix}_generation_unavailable", "Generation is unavailable")
    raise exc


def _error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
