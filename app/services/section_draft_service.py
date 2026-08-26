from __future__ import annotations

import logging
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.db.repositories.article_draft import ArticleDraftRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.prompts.section_draft import PROMPT_VERSION
from app.schemas.outline import ArticleOutlineSection
from app.schemas.section_draft import SectionDraftResponse
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
)
from app.services.draft_context import build_draft_section_context
from app.services.drafting_service import reconcile_sections
from app.services.section_interview_ai import SectionInterviewGenerator

logger = logging.getLogger(__name__)


class SectionDraftService:
    def __init__(self, session: AsyncSession, generator: SectionInterviewGenerator) -> None:
        self.articles = ArticleRepository(session)
        self.briefs = ArticleBriefRepository(session)
        self.drafts = ArticleDraftRepository(session)
        self.outlines = ArticleOutlineRepository(session)
        self.generator = generator

    async def generate(
        self,
        *,
        article_id: UUID,
        section_id: UUID,
        user_id: UUID,
        instruction: str | None,
    ) -> SectionDraftResponse:
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

        context = build_draft_section_context(
            article=article,
            brief=brief,
            draft=draft,
            outline_sections=outline_sections,
            selected_section_id=section_id,
        )
        started = perf_counter()
        try:
            result = await self.generator.generate_direct_draft(context, instruction)
        except BriefProviderTimeoutError as exc:
            raise _error(504, "section_draft_generation_timeout", "Generation timed out") from exc
        except BriefProviderBlockedError as exc:
            raise _error(
                422,
                "section_draft_generation_blocked",
                "The content could not be processed",
            ) from exc
        except BriefProviderResponseError as exc:
            raise _error(
                502,
                "section_draft_generation_failed",
                "The generated content was invalid",
            ) from exc
        except BriefProviderUnavailableError as exc:
            raise _error(
                503,
                "section_draft_generation_unavailable",
                "Generation is unavailable",
            ) from exc

        duration_ms = round((perf_counter() - started) * 1000)
        logger.info(
            "Section draft generated article_id=%s section_id=%s prompt_version=%s model_id=%s "
            "duration_ms=%s input_tokens=%s output_tokens=%s",
            article_id,
            section_id,
            PROMPT_VERSION,
            result.model_id,
            duration_ms,
            result.input_token_count,
            result.output_token_count,
        )
        return SectionDraftResponse(section_id=section_id, blocks=result.draft.blocks)


def _error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
