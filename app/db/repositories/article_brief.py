from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.article_brief import ArticleBrief
from app.schemas.brief import GeneratedBrief


class ArticleBriefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_article(self, article_id: UUID) -> ArticleBrief | None:
        result = await self.session.scalars(
            select(ArticleBrief).where(ArticleBrief.article_id == article_id)
        )
        return result.first()

    async def upsert(
        self,
        *,
        article_id: UUID,
        content: GeneratedBrief,
        source_article_updated_at: datetime,
        model_id: str,
        prompt_version: str,
        input_token_count: int | None,
        output_token_count: int | None,
        generation_duration_ms: int,
    ) -> ArticleBrief:
        values: dict[str, Any] = {
            "summary": content.summary,
            "core_angle": content.core_angle,
            "audience_insights": content.audience_insights,
            "tone_and_style": content.tone_and_style,
            "key_takeaways": content.key_takeaways,
            "evidence_gaps": content.evidence_gaps,
            "call_to_action": content.call_to_action,
            "seo": content.seo.model_dump(mode="json"),
            "source_article_updated_at": source_article_updated_at,
            "model_id": model_id,
            "prompt_version": prompt_version,
            "input_token_count": input_token_count,
            "output_token_count": output_token_count,
            "generation_duration_ms": generation_duration_ms,
        }
        statement = (
            insert(ArticleBrief)
            .values(id=uuid4(), article_id=article_id, outline_revision=1, **values)
            .on_conflict_do_update(
                constraint="uq_article_briefs_article_id",
                set_={
                    **values,
                    "outline_revision": ArticleBrief.outline_revision + 1,
                    "updated_at": func.now(),
                },
            )
            .returning(ArticleBrief)
        )
        brief = await self.session.scalar(statement)
        assert brief is not None
        return brief
