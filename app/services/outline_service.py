from __future__ import annotations

import logging
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_outline import ArticleOutline
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.prompts.outline import PROMPT_VERSION
from app.schemas.outline import ArticleOutlineResponse, ArticleOutlineUpdate, GeneratedOutline
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    OutlineGenerationResult,
    OutlineGenerator,
    OutlineSource,
)

logger = logging.getLogger(__name__)


class ArticleOutlineService:
    def __init__(self, session: AsyncSession, generator: OutlineGenerator | None = None) -> None:
        self.session = session
        self.articles = ArticleRepository(session)
        self.briefs = ArticleBriefRepository(session)
        self.outlines = ArticleOutlineRepository(session)
        self.generator = generator

    async def generate(self, *, article_id: UUID, user_id: UUID) -> ArticleOutlineResponse:
        if self.generator is None:
            raise _generation_error(
                503, "outline_generation_unavailable", "Outline generation is not configured"
            )
        brief = await self._get_owned_brief(article_id=article_id, user_id=user_id)
        source = OutlineSource(
            summary=brief.summary,
            core_angle=brief.core_angle,
            audience_insights=brief.audience_insights,
            tone_and_style=brief.tone_and_style,
            key_takeaways=brief.key_takeaways,
            evidence_gaps=brief.evidence_gaps,
            call_to_action=brief.call_to_action,
        )
        started_at = perf_counter()
        try:
            result = await self.generator.generate(source)
        except BriefProviderTimeoutError as exc:
            raise _generation_error(
                504, "outline_generation_timeout", "Outline generation timed out"
            ) from exc
        except BriefProviderBlockedError as exc:
            raise _generation_error(
                422, "outline_generation_blocked", "The brief content could not be processed"
            ) from exc
        except BriefProviderResponseError as exc:
            raise _generation_error(
                502, "outline_generation_failed", "The generated outline was invalid"
            ) from exc
        except BriefProviderUnavailableError as exc:
            raise _generation_error(
                503,
                "outline_generation_unavailable",
                "Outline generation is temporarily unavailable",
            ) from exc

        duration_ms = round((perf_counter() - started_at) * 1000)
        outline = await self._save_result(
            article_id=article_id,
            brief_revision=brief.outline_revision,
            result=result,
            duration_ms=duration_ms,
        )
        logger.info(
            "Outline generated article_id=%s user_id=%s prompt_version=%s model_id=%s "
            "duration_ms=%s input_tokens=%s output_tokens=%s",
            article_id,
            user_id,
            PROMPT_VERSION,
            result.model_id,
            duration_ms,
            result.input_token_count,
            result.output_token_count,
        )
        return outline_response(outline, brief)

    async def get(self, *, article_id: UUID, user_id: UUID) -> ArticleOutlineResponse:
        brief = await self._get_owned_brief(article_id=article_id, user_id=user_id)
        outline = await self._get_outline(article_id)
        return outline_response(outline, brief)

    async def update(
        self, *, article_id: UUID, user_id: UUID, payload: ArticleOutlineUpdate
    ) -> ArticleOutlineResponse:
        brief = await self._get_owned_brief(article_id=article_id, user_id=user_id)
        outline = await self._get_outline(article_id)
        sections = [section.model_dump(mode="json") for section in payload.sections]
        if outline.sections != sections:
            outline = await self.outlines.update_sections(outline, sections)
        return outline_response(outline, brief)

    async def delete(self, *, article_id: UUID, user_id: UUID) -> None:
        await self._get_owned_brief(article_id=article_id, user_id=user_id)
        outline = await self._get_outline(article_id)
        await self.outlines.delete(outline)

    async def _get_owned_brief(self, *, article_id: UUID, user_id: UUID) -> ArticleBrief:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise AppError(
                status_code=404,
                code="article_not_found",
                message="The article was not found",
            )
        brief = await self.briefs.get_for_article(article_id)
        if brief is None:
            raise AppError(
                status_code=404,
                code="brief_not_found",
                message="The article brief was not found",
            )
        return brief

    async def _get_outline(self, article_id: UUID) -> ArticleOutline:
        outline = await self.outlines.get_for_article(article_id)
        if outline is None:
            raise AppError(
                status_code=404,
                code="outline_not_found",
                message="The article outline was not found",
            )
        return outline

    async def _save_result(
        self,
        *,
        article_id: UUID,
        brief_revision: int,
        result: OutlineGenerationResult,
        duration_ms: int,
    ) -> ArticleOutline:
        return await self.outlines.upsert(
            article_id=article_id,
            content=result.outline,
            source_brief_revision=brief_revision,
            model_id=result.model_id,
            prompt_version=PROMPT_VERSION,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
            generation_duration_ms=duration_ms,
        )


def outline_response(outline: ArticleOutline, brief: ArticleBrief) -> ArticleOutlineResponse:
    content = GeneratedOutline.model_validate(outline, from_attributes=True)
    return ArticleOutlineResponse(
        **content.model_dump(),
        id=outline.id,
        article_id=outline.article_id,
        model_id=outline.model_id,
        prompt_version=outline.prompt_version,
        input_token_count=outline.input_token_count,
        output_token_count=outline.output_token_count,
        generation_duration_ms=outline.generation_duration_ms,
        is_stale=outline.source_brief_revision != brief.outline_revision,
        created_at=outline.created_at,
        updated_at=outline.updated_at,
    )


def _generation_error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
