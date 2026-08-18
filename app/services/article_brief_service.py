from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.prompts.brief import PROMPT_VERSION
from app.schemas.brief import ArticleBriefResponse, ArticleBriefUpdate, BriefSeo, GeneratedBrief
from app.services.ai_service import (
    BriefGenerationResult,
    BriefGenerator,
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    BriefSource,
)

logger = logging.getLogger(__name__)

OUTLINE_AFFECTING_FIELDS = {
    "summary",
    "core_angle",
    "audience_insights",
    "tone_and_style",
    "key_takeaways",
    "evidence_gaps",
    "call_to_action",
}


class ArticleBriefService:
    def __init__(self, session: AsyncSession, generator: BriefGenerator | None = None) -> None:
        self.session = session
        self.articles = ArticleRepository(session)
        self.briefs = ArticleBriefRepository(session)
        self.generator = generator

    async def generate(self, *, article_id: UUID, user_id: UUID) -> ArticleBriefResponse:
        if self.generator is None:
            raise _generation_error(
                503,
                "brief_generation_unavailable",
                "Brief generation is not configured",
            )
        article = await self._get_article(article_id=article_id, user_id=user_id)
        source_updated_at = article.updated_at
        source = BriefSource(
            working_title=article.working_title,
            notes=article.notes,
            target_audience=article.target_audience,
            article_goal=article.article_goal,
        )
        started_at = perf_counter()
        try:
            result = await self.generator.generate(source)
        except BriefProviderTimeoutError as exc:
            self._log_failure(article_id, user_id, started_at, "timeout")
            raise _generation_error(
                504, "brief_generation_timeout", "Brief generation timed out"
            ) from exc
        except BriefProviderBlockedError as exc:
            self._log_failure(article_id, user_id, started_at, "blocked")
            raise _generation_error(
                422, "brief_generation_blocked", "The article content could not be processed"
            ) from exc
        except BriefProviderResponseError as exc:
            self._log_failure(article_id, user_id, started_at, "invalid_response")
            raise _generation_error(
                502, "brief_generation_failed", "The generated brief was invalid"
            ) from exc
        except BriefProviderUnavailableError as exc:
            self._log_failure(article_id, user_id, started_at, "unavailable")
            raise _generation_error(
                503,
                "brief_generation_unavailable",
                "Brief generation is temporarily unavailable",
            ) from exc

        duration_ms = round((perf_counter() - started_at) * 1000)
        brief = await self._save_result(
            article_id=article_id,
            result=result,
            source_updated_at=source_updated_at,
            duration_ms=duration_ms,
        )
        await self.session.refresh(article, attribute_names=["updated_at"])
        logger.info(
            "Brief generated article_id=%s user_id=%s prompt_version=%s model_id=%s "
            "duration_ms=%s input_tokens=%s output_tokens=%s",
            article_id,
            user_id,
            PROMPT_VERSION,
            result.model_id,
            duration_ms,
            result.input_token_count,
            result.output_token_count,
        )
        return brief_response(brief, article)

    async def get(self, *, article_id: UUID, user_id: UUID) -> ArticleBriefResponse:
        article = await self._get_article(article_id=article_id, user_id=user_id)
        brief = await self.briefs.get_for_article(article_id)
        if brief is None:
            raise AppError(
                status_code=404,
                code="brief_not_found",
                message="The article brief was not found",
            )
        return brief_response(brief, article)

    async def update(
        self, *, article_id: UUID, user_id: UUID, payload: ArticleBriefUpdate
    ) -> ArticleBriefResponse:
        article = await self._get_article(article_id=article_id, user_id=user_id)
        brief = await self._get_brief(article_id)
        updates = payload.model_dump(exclude_unset=True, mode="json")

        seo_update = updates.pop("seo", None)
        changed_fields: set[str] = set()
        for field, value in updates.items():
            if getattr(brief, field) != value:
                setattr(brief, field, value)
                changed_fields.add(field)

        if seo_update is not None:
            merged_seo = BriefSeo.model_validate({**brief.seo, **seo_update}).model_dump(
                mode="json"
            )
            if brief.seo != merged_seo:
                brief.seo = merged_seo
                changed_fields.add("seo")

        if changed_fields & OUTLINE_AFFECTING_FIELDS:
            brief.outline_revision += 1
        if changed_fields:
            await self.session.flush()
            await self.session.refresh(brief)
        return brief_response(brief, article)

    async def _get_article(self, *, article_id: UUID, user_id: UUID) -> Article:
        article = await self.articles.get_owned(article_id, user_id)
        if article is None:
            raise AppError(
                status_code=404,
                code="article_not_found",
                message="The article was not found",
            )
        return article

    async def _get_brief(self, article_id: UUID) -> ArticleBrief:
        brief = await self.briefs.get_for_article(article_id)
        if brief is None:
            raise AppError(
                status_code=404,
                code="brief_not_found",
                message="The article brief was not found",
            )
        return brief

    async def _save_result(
        self,
        *,
        article_id: UUID,
        result: BriefGenerationResult,
        source_updated_at: datetime,
        duration_ms: int,
    ) -> ArticleBrief:
        return await self.briefs.upsert(
            article_id=article_id,
            content=result.brief,
            source_article_updated_at=source_updated_at,
            model_id=result.model_id,
            prompt_version=PROMPT_VERSION,
            input_token_count=result.input_token_count,
            output_token_count=result.output_token_count,
            generation_duration_ms=duration_ms,
        )

    def _log_failure(
        self, article_id: UUID, user_id: UUID, started_at: float, outcome: str
    ) -> None:
        logger.warning(
            "Brief generation failed article_id=%s user_id=%s prompt_version=%s model_id=%s "
            "outcome=%s duration_ms=%s",
            article_id,
            user_id,
            PROMPT_VERSION,
            getattr(self.generator, "model_id", "unknown"),
            outcome,
            round((perf_counter() - started_at) * 1000),
        )


def brief_response(brief: ArticleBrief, article: Article) -> ArticleBriefResponse:
    content = GeneratedBrief.model_validate(brief, from_attributes=True)
    return ArticleBriefResponse(
        **content.model_dump(),
        id=brief.id,
        article_id=brief.article_id,
        model_id=brief.model_id,
        prompt_version=brief.prompt_version,
        input_token_count=brief.input_token_count,
        output_token_count=brief.output_token_count,
        generation_duration_ms=brief.generation_duration_ms,
        is_stale=article.updated_at > brief.source_article_updated_at,
        created_at=brief.created_at,
        updated_at=brief.updated_at,
    )


def _generation_error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
