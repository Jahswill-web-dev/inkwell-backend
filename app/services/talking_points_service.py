from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models.article import Article
from app.db.models.article_brief import ArticleBrief
from app.db.models.article_draft import ArticleDraft
from app.db.repositories.article import ArticleRepository
from app.db.repositories.article_brief import ArticleBriefRepository
from app.db.repositories.article_draft import ArticleDraftRepository
from app.db.repositories.article_outline import ArticleOutlineRepository
from app.prompts.talking_points import PROMPT_VERSION
from app.schemas.outline import ArticleOutlineSection
from app.schemas.talking_points import TalkingPointsResponse
from app.services.ai_service import (
    BriefProviderBlockedError,
    BriefProviderResponseError,
    BriefProviderTimeoutError,
    BriefProviderUnavailableError,
    TalkingPointsGenerator,
    TalkingPointsSource,
)
from app.services.draft_context import build_draft_section_context
from app.services.drafting_service import reconcile_sections

logger = logging.getLogger(__name__)


class TalkingPointsService:
    def __init__(self, session: AsyncSession, generator: TalkingPointsGenerator) -> None:
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
    ) -> TalkingPointsResponse:
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
        outline_sections = (
            []
            if outline is None
            else [ArticleOutlineSection.model_validate(section) for section in outline.sections]
        )
        reconciled = reconcile_sections(draft.sections, outline_sections)
        if reconciled != draft.sections:
            draft = await self.drafts.update_sections(draft, reconciled)

        selected = next(
            (section for section in draft.sections if section.get("id") == str(section_id)),
            None,
        )
        if selected is None:
            raise _error(404, "draft_section_not_found", "Draft section not found")

        context = build_talking_points_context(
            article=article,
            brief=brief,
            draft=draft,
            outline_sections=outline.sections if outline is not None else [],
            selected_section_id=section_id,
        )
        started_at = perf_counter()
        try:
            result = await self.generator.generate(
                TalkingPointsSource(context=context, instruction=instruction)
            )
        except BriefProviderTimeoutError as exc:
            raise _error(
                504, "talking_points_generation_timeout", "Talking-point generation timed out"
            ) from exc
        except BriefProviderBlockedError as exc:
            raise _error(
                422,
                "talking_points_generation_blocked",
                "The article content could not be processed",
            ) from exc
        except BriefProviderResponseError as exc:
            raise _error(
                502,
                "talking_points_generation_failed",
                "The generated talking points were invalid",
            ) from exc
        except BriefProviderUnavailableError as exc:
            raise _error(
                503,
                "talking_points_generation_unavailable",
                "Talking-point generation is temporarily unavailable",
            ) from exc

        duration_ms = round((perf_counter() - started_at) * 1000)
        logger.info(
            "Talking points generated article_id=%s section_id=%s prompt_version=%s model_id=%s "
            "duration_ms=%s input_tokens=%s output_tokens=%s",
            article_id,
            section_id,
            PROMPT_VERSION,
            result.model_id,
            duration_ms,
            result.input_token_count,
            result.output_token_count,
        )
        return TalkingPointsResponse(
            section_id=section_id,
            points=result.talking_points.points,
        )


def build_talking_points_context(
    *,
    article: Article,
    brief: ArticleBrief,
    draft: ArticleDraft,
    outline_sections: list[dict[str, Any]],
    selected_section_id: UUID,
) -> dict[str, object]:
    context = build_draft_section_context(
        article=article,
        brief=brief,
        draft=draft,
        outline_sections=outline_sections,
        selected_section_id=selected_section_id,
    )
    return {
        **context,
        "outline": outline_sections,
        "draft_sections": [context["selected_section"], *context["other_sections"]],
        "selected_section_id": str(selected_section_id),
    }


def _error(status_code: int, code: str, message: str) -> AppError:
    return AppError(status_code=status_code, code=code, message=message)
